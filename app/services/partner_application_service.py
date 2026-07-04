"""Сервис для обработки заявок на партнёрский статус."""

from datetime import UTC, datetime

import structlog
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AdvertisingCampaign,
    PartnerApplication,
    PartnerStatus,
    User,
)
from app.utils.user_utils import generate_unique_referral_code

logger = structlog.get_logger(__name__)


class PartnerApplicationService:
    """Сервис управления партнёрскими заявками."""

    async def submit_application(
        self,
        db: AsyncSession,
        user_id: int,
        company_name: str | None = None,
        website_url: str | None = None,
        telegram_channel: str | None = None,
        description: str | None = None,
        expected_monthly_referrals: int | None = None,
        desired_commission_percent: int | None = None,
    ) -> tuple[PartnerApplication | None, str]:
        """
        Подаёт заявку на партнёрский статус.
        Возвращает (application, error_message).
        """
        user = await db.get(User, user_id)
        if not user:
            return None, "Пользователь не найден"

        if user.partner_status == PartnerStatus.APPROVED.value:
            return None, "Вы уже являетесь партнёром"

        if user.partner_status == PartnerStatus.PENDING.value:
            return None, "У вас уже есть заявка на рассмотрении"

        application = PartnerApplication(
            user_id=user_id,
            company_name=company_name,
            website_url=website_url,
            telegram_channel=telegram_channel,
            description=description,
            expected_monthly_referrals=expected_monthly_referrals,
            desired_commission_percent=desired_commission_percent,
        )

        user.partner_status = PartnerStatus.PENDING.value

        db.add(application)
        await db.commit()
        await db.refresh(application)

        logger.info(
            "Подана заявка на партнёрство",
            user_id=user_id,
            application_id=application.id,
        )

        return application, ""

    async def approve_application(
        self,
        db: AsyncSession,
        application_id: int,
        admin_id: int,
        commission_percent: int,
        comment: str | None = None,
    ) -> tuple[bool, str]:
        """
        Одобряет заявку на партнёрство.
        Возвращает (success, error_message).
        """
        result = await db.execute(
            select(PartnerApplication)
            .where(PartnerApplication.id == application_id)
            .with_for_update()
        )
        application = result.scalar_one_or_none()
        if not application:
            return False, "Заявка не найдена"

        if application.status != PartnerStatus.PENDING.value:
            return False, "Заявка уже обработана"

        user_result = await db.execute(
            select(User).where(User.id == application.user_id).with_for_update()
        )
        user = user_result.scalar_one_or_none()
        if not user:
            return False, "Пользователь не найден"

        # Генерируем реферальный код, если его нет
        if not user.referral_code:
            user.referral_code = await generate_unique_referral_code(
                db, user.telegram_id or 0
            )

        user.partner_status = PartnerStatus.APPROVED.value
        user.referral_commission_percent = commission_percent

        application.status = PartnerStatus.APPROVED.value
        application.approved_commission_percent = commission_percent
        application.admin_comment = comment
        application.processed_by = admin_id
        application.processed_at = datetime.now(UTC)

        await db.commit()

        logger.info(
            "Партнёрская заявка одобрена",
            application_id=application_id,
            user_id=application.user_id,
            commission_percent=commission_percent,
            admin_id=admin_id,
        )

        return True, ""

    async def reject_application(
        self,
        db: AsyncSession,
        application_id: int,
        admin_id: int,
        comment: str | None = None,
    ) -> tuple[bool, str]:
        """Отклоняет заявку на партнёрство."""
        result = await db.execute(
            select(PartnerApplication)
            .where(PartnerApplication.id == application_id)
            .with_for_update()
        )
        application = result.scalar_one_or_none()
        if not application:
            return False, "Заявка не найдена"

        if application.status != PartnerStatus.PENDING.value:
            return False, "Заявка уже обработана"

        user_result = await db.execute(
            select(User).where(User.id == application.user_id).with_for_update()
        )
        user = user_result.scalar_one_or_none()
        if user:
            user.partner_status = PartnerStatus.REJECTED.value

        application.status = PartnerStatus.REJECTED.value
        application.admin_comment = comment
        application.processed_by = admin_id
        application.processed_at = datetime.now(UTC)

        await db.commit()

        logger.info(
            "Партнёрская заявка отклонена",
            application_id=application_id,
            user_id=application.user_id,
            admin_id=admin_id,
        )

        return True, ""

    async def revoke_partner(
        self,
        db: AsyncSession,
        user_id: int,
        admin_id: int,
    ) -> tuple[bool, str]:
        """Отзывает партнёрский статус."""
        user = await db.get(User, user_id)
        if not user:
            return False, "Пользователь не найден"

        if user.partner_status != PartnerStatus.APPROVED.value:
            return False, "Пользователь не является партнёром"

        user.partner_status = PartnerStatus.NONE.value
        user.referral_commission_percent = None

        # Отвязываем все кампании от бывшего партнёра
        await db.execute(
            update(AdvertisingCampaign)
            .where(AdvertisingCampaign.partner_user_id == user_id)
            .values(partner_user_id=None)
        )

        await db.commit()

        logger.info(
            "Партнёрский статус отозван",
            user_id=user_id,
            admin_id=admin_id,
        )

        return True, ""

    async def get_pending_applications(
        self, db: AsyncSession
    ) -> list[PartnerApplication]:
        """Получает все заявки на рассмотрении."""
        result = await db.execute(
            select(PartnerApplication)
            .where(PartnerApplication.status == PartnerStatus.PENDING.value)
            .order_by(PartnerApplication.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_all_applications(
        self,
        db: AsyncSession,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PartnerApplication], int]:
        """Получает заявки с фильтрацией. Возвращает (items, total)."""
        query = select(PartnerApplication)
        count_query = select(func.count()).select_from(PartnerApplication)

        if status:
            query = query.where(PartnerApplication.status == status)
            count_query = count_query.where(PartnerApplication.status == status)

        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        query = (
            query.order_by(desc(PartnerApplication.created_at))
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(query)

        return list(result.scalars().all()), total

    async def get_latest_application(
        self, db: AsyncSession, user_id: int
    ) -> PartnerApplication | None:
        """Получает последнюю заявку пользователя."""
        result = await db.execute(
            select(PartnerApplication)
            .where(PartnerApplication.user_id == user_id)
            .order_by(desc(PartnerApplication.created_at))
            .limit(1)
        )
        return result.scalar_one_or_none()


# Синглтон сервиса
partner_application_service = PartnerApplicationService()
