import logging
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.campaign import record_campaign_registration
from app.database.crud.subscription import (
    create_paid_subscription,
    get_subscription_by_user_id,
)
from app.database.crud.user import add_user_balance
from app.database.crud.tariff import get_tariff_by_id
from app.database.models import AdvertisingCampaign, User
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)


@dataclass
class CampaignBonusResult:
    success: bool
    bonus_type: Optional[str] = None
    balance_kopeks: int = 0
    subscription_days: Optional[int] = None
    subscription_traffic_gb: Optional[int] = None
    subscription_device_limit: Optional[int] = None
    subscription_squads: Optional[List[str]] = None
    # Поля для tariff
    tariff_id: Optional[int] = None
    tariff_name: Optional[str] = None
    tariff_duration_days: Optional[int] = None


class AdvertisingCampaignService:
    def __init__(self) -> None:
        self.subscription_service = SubscriptionService()

    async def apply_campaign_bonus(
        self,
        db: AsyncSession,
        user: User,
        campaign: AdvertisingCampaign,
    ) -> CampaignBonusResult:
        if not campaign.is_active:
            logger.warning(
                "⚠️ Попытка выдать бонус по неактивной кампании %s", campaign.id
            )
            return CampaignBonusResult(success=False)

        if campaign.is_balance_bonus:
            return await self._apply_balance_bonus(db, user, campaign)

        if campaign.is_subscription_bonus:
            return await self._apply_subscription_bonus(db, user, campaign)

        if campaign.is_none_bonus:
            return await self._apply_none_bonus(db, user, campaign)

        if campaign.is_tariff_bonus:
            return await self._apply_tariff_bonus(db, user, campaign)

        logger.error("❌ Неизвестный тип бонуса кампании: %s", campaign.bonus_type)
        return CampaignBonusResult(success=False)

    async def _apply_balance_bonus(
        self,
        db: AsyncSession,
        user: User,
        campaign: AdvertisingCampaign,
    ) -> CampaignBonusResult:
        amount = campaign.balance_bonus_kopeks or 0
        if amount <= 0:
            logger.info("ℹ️ Кампания %s не имеет бонуса на баланс", campaign.id)
            return CampaignBonusResult(success=False)

        description = f"Бонус за регистрацию по кампании '{campaign.name}'"
        success = await add_user_balance(
            db,
            user,
            amount,
            description=description,
        )

        if not success:
            return CampaignBonusResult(success=False)

        await record_campaign_registration(
            db,
            campaign_id=campaign.id,
            user_id=user.id,
            bonus_type="balance",
            balance_bonus_kopeks=amount,
        )

        logger.info(
            "💰 Пользователю %s начислен бонус %s₽ по кампании %s",
            user.telegram_id,
            amount / 100,
            campaign.id,
        )

        return CampaignBonusResult(
            success=True,
            bonus_type="balance",
            balance_kopeks=amount,
        )

    async def _apply_subscription_bonus(
        self,
        db: AsyncSession,
        user: User,
        campaign: AdvertisingCampaign,
    ) -> CampaignBonusResult:
        existing_subscription = await get_subscription_by_user_id(db, user.id)
        if existing_subscription:
            logger.warning(
                "⚠️ У пользователя %s уже есть подписка, бонус кампании %s пропущен",
                user.telegram_id,
                campaign.id,
            )
            return CampaignBonusResult(success=False)

        duration_days = campaign.subscription_duration_days or 0
        if duration_days <= 0:
            logger.info(
                "ℹ️ Кампания %s не содержит корректной длительности подписки",
                campaign.id,
            )
            return CampaignBonusResult(success=False)

        traffic_limit = campaign.subscription_traffic_gb
        device_limit = campaign.subscription_device_limit
        if device_limit is None:
            device_limit = settings.DEFAULT_DEVICE_LIMIT
        squads = list(campaign.subscription_squads or [])

        if not squads:
            try:
                from app.database.crud.server_squad import get_random_trial_squad_uuid

                trial_uuid = await get_random_trial_squad_uuid(db)
                if trial_uuid:
                    squads = [trial_uuid]
            except Exception as error:
                logger.error(
                    "Не удалось подобрать сквад для кампании %s: %s",
                    campaign.id,
                    error,
                )

        squad_uuid = squads[0] if squads else None

        new_subscription = await create_paid_subscription(
            db=db,
            user_id=user.id,
            duration_days=duration_days,
            traffic_limit_gb=traffic_limit or 0,
            device_limit=device_limit,
            connected_squads=squads,
            update_server_counters=True,
            is_trial=True,
        )

        try:
            await self.subscription_service.create_remnawave_user(db, new_subscription)
        except Exception as error:
            logger.error(
                "❌ Ошибка синхронизации RemnaWave для кампании %s: %s",
                campaign.id,
                error,
            )

        await record_campaign_registration(
            db,
            campaign_id=campaign.id,
            user_id=user.id,
            bonus_type="subscription",
            subscription_duration_days=duration_days,
        )

        logger.info(
            "🎁 Пользователю %s выдана подписка по кампании %s на %s дней",
            user.telegram_id,
            campaign.id,
            duration_days,
        )

        return CampaignBonusResult(
            success=True,
            bonus_type="subscription",
            subscription_days=duration_days,
            subscription_traffic_gb=traffic_limit or 0,
            subscription_device_limit=device_limit,
            subscription_squads=squads,
        )

    async def _apply_none_bonus(
        self,
        db: AsyncSession,
        user: User,
        campaign: AdvertisingCampaign,
    ) -> CampaignBonusResult:
        """Обычная ссылка без награды - только регистрация для отслеживания."""
        await record_campaign_registration(
            db,
            campaign_id=campaign.id,
            user_id=user.id,
            bonus_type="none",
        )

        logger.info(
            "📊 Пользователь %s зарегистрирован по ссылке кампании %s (без награды)",
            user.telegram_id,
            campaign.id,
        )

        return CampaignBonusResult(
            success=True,
            bonus_type="none",
        )

    async def _apply_tariff_bonus(
        self,
        db: AsyncSession,
        user: User,
        campaign: AdvertisingCampaign,
    ) -> CampaignBonusResult:
        """Выдача тарифа на определённое время."""
        existing_subscription = await get_subscription_by_user_id(db, user.id)
        if existing_subscription:
            logger.warning(
                "⚠️ У пользователя %s уже есть подписка, бонус тарифа кампании %s пропущен",
                user.telegram_id,
                campaign.id,
            )
            return CampaignBonusResult(success=False)

        if not campaign.tariff_id:
            logger.error(
                "❌ Кампания %s не имеет указанного тарифа для выдачи",
                campaign.id,
            )
            return CampaignBonusResult(success=False)

        duration_days = campaign.tariff_duration_days or 0
        if duration_days <= 0:
            logger.error(
                "❌ Кампания %s не имеет указанной длительности тарифа",
                campaign.id,
            )
            return CampaignBonusResult(success=False)

        # Получаем тариф для извлечения параметров
        tariff = await get_tariff_by_id(db, campaign.tariff_id)
        if not tariff:
            logger.error(
                "❌ Тариф %s не найден для кампании %s",
                campaign.tariff_id,
                campaign.id,
            )
            return CampaignBonusResult(success=False)

        if not tariff.is_active:
            logger.warning(
                "⚠️ Тариф %s неактивен, бонус кампании %s пропущен",
                tariff.id,
                campaign.id,
            )
            return CampaignBonusResult(success=False)

        traffic_limit = tariff.traffic_limit_gb
        device_limit = tariff.device_limit
        squads = list(tariff.allowed_squads or [])

        if not squads:
            try:
                from app.database.crud.server_squad import get_random_trial_squad_uuid

                trial_uuid = await get_random_trial_squad_uuid(db)
                if trial_uuid:
                    squads = [trial_uuid]
            except Exception as error:
                logger.error(
                    "Не удалось подобрать сквад для тарифа кампании %s: %s",
                    campaign.id,
                    error,
                )

        # Создаём подписку как платную (не trial) с привязкой к тарифу
        new_subscription = await create_paid_subscription(
            db=db,
            user_id=user.id,
            duration_days=duration_days,
            traffic_limit_gb=traffic_limit or 0,
            device_limit=device_limit,
            connected_squads=squads,
            update_server_counters=True,
            is_trial=False,  # Это полноценная подписка, не пробная
            tariff_id=tariff.id,
        )

        try:
            await self.subscription_service.create_remnawave_user(db, new_subscription)
        except Exception as error:
            logger.error(
                "❌ Ошибка синхронизации RemnaWave для тарифа кампании %s: %s",
                campaign.id,
                error,
            )

        await record_campaign_registration(
            db,
            campaign_id=campaign.id,
            user_id=user.id,
            bonus_type="tariff",
            tariff_id=tariff.id,
            tariff_duration_days=duration_days,
        )

        logger.info(
            "🎁 Пользователю %s выдан тариф '%s' по кампании %s на %s дней",
            user.telegram_id,
            tariff.name,
            campaign.id,
            duration_days,
        )

        return CampaignBonusResult(
            success=True,
            bonus_type="tariff",
            tariff_id=tariff.id,
            tariff_name=tariff.name,
            tariff_duration_days=duration_days,
            subscription_traffic_gb=traffic_limit or 0,
            subscription_device_limit=device_limit,
            subscription_squads=squads,
        )
