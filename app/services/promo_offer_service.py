from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.database import AsyncSessionLocal
from app.database.models import PromoOffer, PromoOfferType, User, UserStatus
from app.database.crud.discount_offer import upsert_discount_offer
from app.database.crud.promo_offer import (
    create_delivery,
    get_delivery_by_offer_and_user,
    get_promo_offer_by_id,
    update_promo_offer,
)
from app.localization.texts import get_texts
from app.services.promo_offer_utils import determine_user_segments


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PromoOfferTask:
    task: asyncio.Task


class PromoOfferService:
    def __init__(self) -> None:
        self._bot: Optional[Bot] = None
        self._tasks: dict[int, _PromoOfferTask] = {}
        self._lock = asyncio.Lock()

    def set_bot(self, bot: Bot) -> None:
        self._bot = bot

    async def start_offer(self, offer_id: int) -> None:
        if self._bot is None:
            logger.error("Невозможно запустить промо-предложение %s: бот не инициализирован", offer_id)
            return

        async with self._lock:
            task_entry = self._tasks.get(offer_id)
            if task_entry and not task_entry.task.done():
                logger.warning("Промо-предложение %s уже обрабатывается", offer_id)
                return

            task = asyncio.create_task(
                self._run_offer(offer_id),
                name=f"promo-offer-{offer_id}",
            )
            self._tasks[offer_id] = _PromoOfferTask(task=task)
            task.add_done_callback(lambda _: self._tasks.pop(offer_id, None))

    async def _run_offer(self, offer_id: int) -> None:
        sent = 0
        failed = 0
        now = datetime.utcnow()

        try:
            async with AsyncSessionLocal() as session:
                offer = await get_promo_offer_by_id(session, offer_id)
                if not offer:
                    logger.warning("Промо-предложение %s не найдено", offer_id)
                    return
                if offer.is_cancelled:
                    logger.info("Промо-предложение %s отменено, отправка пропущена", offer_id)
                    return

                if offer.starts_at > now:
                    delay = (offer.starts_at - now).total_seconds()
                    if delay > 0:
                        logger.info(
                            "⏳ Промо-предложение %s запланировано, ожидание %.0f секунд",
                            offer.id,
                            delay,
                        )
                        await session.close()
                        await asyncio.sleep(delay)
                        await self._run_offer(offer_id)
                        return

                if offer.expires_at <= now:
                    logger.info("Промо-предложение %s истекло до начала отправки", offer_id)
                    await update_promo_offer(
                        session,
                        offer,
                        status="expired",
                        completed_at=now,
                    )
                    return

                offer = await update_promo_offer(
                    session,
                    offer,
                    status="in_progress",
                    started_at=now,
                    sent_count=0,
                    failed_count=0,
                )

                recipients = await self._collect_recipients(session, offer)
                offer = await update_promo_offer(
                    session,
                    offer,
                    total_count=len(recipients),
                )

                for user in recipients:
                    if not user.telegram_id:
                        continue

                    existing_delivery = await get_delivery_by_offer_and_user(
                        session, offer.id, user.id
                    )
                    if existing_delivery:
                        continue

                    try:
                        keyboard, discount_offer_id = await self._build_keyboard(
                            session, offer, user
                        )

                        await self._send_message(user, offer, keyboard)

                        await create_delivery(
                            session,
                            offer_id=offer.id,
                            user_id=user.id,
                            discount_offer_id=discount_offer_id,
                            status="sent",
                        )
                        sent += 1
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        logger.error(
                            "Ошибка отправки промо-предложения %s пользователю %s: %s",
                            offer.id,
                            user.telegram_id,
                            exc,
                        )
                        await create_delivery(
                            session,
                            offer_id=offer.id,
                            user_id=user.id,
                            status="failed",
                            error_message=str(exc),
                        )

                    offer = await update_promo_offer(
                        session,
                        offer,
                        sent_count=sent,
                        failed_count=failed,
                    )

                await update_promo_offer(
                    session,
                    offer,
                    status="completed",
                    completed_at=datetime.utcnow(),
                    sent_count=sent,
                    failed_count=failed,
                )

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Критическая ошибка при выполнении промо-предложения %s: %s",
                offer_id,
                exc,
            )
            async with AsyncSessionLocal() as session:
                offer = await get_promo_offer_by_id(session, offer_id)
                if offer:
                    await update_promo_offer(
                        session,
                        offer,
                        status="failed",
                        completed_at=datetime.utcnow(),
                        sent_count=sent,
                        failed_count=failed,
                    )

    async def _collect_recipients(
        self,
        session: AsyncSession,
        offer: PromoOffer,
    ) -> list[User]:
        stmt = (
            select(User)
            .options(selectinload(User.subscription))
            .where(User.status == UserStatus.ACTIVE.value)
        )
        result = await session.execute(stmt)
        users = result.scalars().all()

        target_segments = set(offer.target_segments or [])
        now = datetime.utcnow()
        recipients: list[User] = []

        for user in users:
            segments = determine_user_segments(user, now)
            if not segments:
                continue
            if target_segments & segments:
                recipients.append(user)

        return recipients

    async def _build_keyboard(
        self,
        session: AsyncSession,
        offer: PromoOffer,
        user: User,
    ) -> tuple[InlineKeyboardMarkup, Optional[int]]:
        texts = get_texts(user.language)

        if offer.offer_type == PromoOfferType.TEST_SQUADS.value:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=offer.button_text,
                            callback_data=f"promo_offer_test_{offer.id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=texts.t("MENU_SUBSCRIPTION", "💎 Подписка"),
                            callback_data="menu_subscription",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=texts.t("SUPPORT_BUTTON", "🆘 Поддержка"),
                            callback_data="menu_support",
                        )
                    ],
                ]
            )
            return keyboard, None

        # Discount-based offers reuse the discount offer flow
        valid_hours = self._resolve_discount_valid_hours(offer)
        subscription_id = user.subscription.id if user.subscription else None

        discount = await upsert_discount_offer(
            session,
            user_id=user.id,
            subscription_id=subscription_id,
            notification_type=f"promo_offer_{offer.id}",
            discount_percent=offer.discount_percent,
            bonus_amount_kopeks=offer.bonus_amount_kopeks,
            valid_hours=valid_hours,
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=offer.button_text,
                        callback_data=f"claim_discount_{discount.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("SUBSCRIPTION_EXTEND", "💎 Продлить подписку"),
                        callback_data="subscription_extend",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("BALANCE_TOPUP", "💳 Пополнить баланс"),
                        callback_data="balance_topup",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("SUPPORT_BUTTON", "🆘 Поддержка"),
                        callback_data="menu_support",
                    )
                ],
            ]
        )

        return keyboard, discount.id

    def _resolve_discount_valid_hours(self, offer: PromoOffer) -> int:
        if offer.discount_valid_hours and offer.discount_valid_hours > 0:
            return offer.discount_valid_hours

        now = datetime.utcnow()
        delta = offer.expires_at - now
        if delta.total_seconds() <= 0:
            return 1

        hours = ceil(delta.total_seconds() / 3600)
        return max(1, hours)

    async def _send_message(
        self,
        user: User,
        offer: PromoOffer,
        keyboard: InlineKeyboardMarkup,
    ) -> None:
        if self._bot is None:
            raise RuntimeError("Bot instance is not configured for PromoOfferService")

        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=offer.message_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            raise


promo_offer_service = PromoOfferService()
