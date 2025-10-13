"""Общие инструменты платёжного сервиса.

В этом модуле собраны методы, которые нужны всем платёжным каналам:
построение клавиатур, базовые уведомления и стандартная обработка
успешных платежей.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.database.crud.user import get_user_by_telegram_id
from app.database.database import get_db
from app.localization.texts import get_texts
from app.services.subscription_checkout_service import (
    has_subscription_checkout_draft,
    should_offer_checkout_resume,
)
from app.utils.miniapp_buttons import build_miniapp_or_callback_button

logger = logging.getLogger(__name__)


class PaymentCommonMixin:
    """Mixin с базовой логикой, которую используют остальные платёжные блоки."""

    async def build_topup_success_keyboard(self, user: Any) -> InlineKeyboardMarkup:
        """Формирует клавиатуру по завершении платежа, подстраиваясь под пользователя."""
        # Загружаем нужные тексты с учётом выбранного языка пользователя.
        texts = get_texts(user.language if user else "ru")

        # Определяем статус подписки, чтобы показать подходящую кнопку.
        has_active_subscription = bool(
            user and user.subscription and not user.subscription.is_trial and user.subscription.is_active
        )

        first_button = build_miniapp_or_callback_button(
            text=(
                texts.MENU_EXTEND_SUBSCRIPTION
                if has_active_subscription
                else texts.MENU_BUY_SUBSCRIPTION
            ),
            callback_data=(
                "subscription_extend" if has_active_subscription else "menu_buy"
            ),
        )

        keyboard_rows: list[list[InlineKeyboardButton]] = [[first_button]]

        # Если для пользователя есть незавершённый checkout, предлагаем вернуться к нему.
        if user:
            draft_exists = await has_subscription_checkout_draft(user.id)
            if should_offer_checkout_resume(user, draft_exists):
                keyboard_rows.append([
                    build_miniapp_or_callback_button(
                        text=texts.RETURN_TO_SUBSCRIPTION_CHECKOUT,
                        callback_data="subscription_resume_checkout",
                    )
                ])

        # Стандартные кнопки быстрого доступа к балансу и главному меню.
        keyboard_rows.append([
            build_miniapp_or_callback_button(
                text="💰 Мой баланс",
                callback_data="menu_balance",
            )
        ])
        keyboard_rows.append([
            InlineKeyboardButton(
                text="🏠 Главное меню",
                callback_data="back_to_menu",
            )
        ])

        return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    async def _send_payment_success_notification(
        self,
        telegram_id: int,
        amount_kopeks: int,
        user: Any | None = None,
    ) -> None:
        """Отправляет пользователю уведомление об успешном платеже."""
        if not getattr(self, "bot", None):
            # Если бот не передан (например, внутри фоновых задач), уведомление пропускаем.
            return

        if user is None:
            try:
                async for db in get_db():
                    user = await get_user_by_telegram_id(db, telegram_id)
                    break
            except Exception as fetch_error:
                logger.warning(
                    "Не удалось получить пользователя %s для уведомления: %s",
                    telegram_id,
                    fetch_error,
                )
                user = None

        try:
            keyboard = await self.build_topup_success_keyboard(user)

            message = (
                "✅ <b>Платеж успешно завершен!</b>\n\n"
                f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
                "💳 Способ: Банковская карта (YooKassa)\n\n"
                "Средства зачислены на ваш баланс!"
            )

            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as error:
            logger.error(
                "Ошибка отправки уведомления пользователю %s: %s",
                telegram_id,
                error,
            )

    async def process_successful_payment(
        self,
        payment_id: str,
        amount_kopeks: int,
        user_id: int,
        payment_method: str,
    ) -> bool:
        """Общая точка учёта успешных платежей (используется провайдерами при необходимости)."""
        try:
            logger.info(
                "Обработан успешный платеж: %s, %s₽, пользователь %s, метод %s",
                payment_id,
                amount_kopeks / 100,
                user_id,
                payment_method,
            )
            return True
        except Exception as error:
            logger.error("Ошибка обработки платежа %s: %s", payment_id, error)
            return False
