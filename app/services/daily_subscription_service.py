"""
Сервис для автоматического списания суточных подписок.
Проверяет подписки с суточным тарифом и списывает плату раз в сутки.
Также сбрасывает докупленный трафик по истечении 30 дней.
"""
import logging
import asyncio
from datetime import datetime
from typing import Optional

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import get_db
from app.database.crud.subscription import (
    get_daily_subscriptions_for_charge,
    update_daily_charge_time,
    suspend_daily_subscription_insufficient_balance,
)
from app.database.crud.user import subtract_user_balance, get_user_by_id
from app.database.crud.transaction import create_transaction
from app.database.models import TransactionType, PaymentMethod, Subscription, User
from app.localization.texts import get_texts


logger = logging.getLogger(__name__)


class DailySubscriptionService:
    """
    Сервис автоматического списания для суточных подписок.
    """

    def __init__(self):
        self._running = False
        self._bot: Optional[Bot] = None
        self._check_interval_minutes = 30  # Проверка каждые 30 минут

    def set_bot(self, bot: Bot):
        """Устанавливает бота для отправки уведомлений."""
        self._bot = bot

    def is_enabled(self) -> bool:
        """Проверяет, включен ли сервис суточных подписок."""
        return getattr(settings, 'DAILY_SUBSCRIPTIONS_ENABLED', True)

    def get_check_interval_minutes(self) -> int:
        """Возвращает интервал проверки в минутах."""
        return getattr(settings, 'DAILY_SUBSCRIPTIONS_CHECK_INTERVAL_MINUTES', 30)

    async def process_daily_charges(self) -> dict:
        """
        Обрабатывает суточные списания.

        Returns:
            dict: Статистика обработки
        """
        stats = {
            "checked": 0,
            "charged": 0,
            "suspended": 0,
            "errors": 0,
        }

        try:
            async for db in get_db():
                subscriptions = await get_daily_subscriptions_for_charge(db)
                stats["checked"] = len(subscriptions)

                for subscription in subscriptions:
                    try:
                        result = await self._process_single_charge(db, subscription)
                        if result == "charged":
                            stats["charged"] += 1
                        elif result == "suspended":
                            stats["suspended"] += 1
                        elif result == "error":
                            stats["errors"] += 1
                    except Exception as e:
                        logger.error(
                            f"Ошибка обработки суточной подписки {subscription.id}: {e}",
                            exc_info=True
                        )
                        stats["errors"] += 1

        except Exception as e:
            logger.error(f"Ошибка при получении подписок для списания: {e}", exc_info=True)

        return stats

    async def _process_single_charge(self, db, subscription) -> str:
        """
        Обрабатывает списание для одной подписки.

        Returns:
            str: "charged", "suspended", "error", "skipped"
        """
        user = subscription.user
        if not user:
            user = await get_user_by_id(db, subscription.user_id)

        if not user:
            logger.warning(f"Пользователь не найден для подписки {subscription.id}")
            return "error"

        tariff = subscription.tariff
        if not tariff:
            logger.warning(f"Тариф не найден для подписки {subscription.id}")
            return "error"

        daily_price = tariff.daily_price_kopeks
        if daily_price <= 0:
            logger.warning(f"Некорректная суточная цена для тарифа {tariff.id}")
            return "error"

        # Проверяем баланс
        if user.balance_kopeks < daily_price:
            # Недостаточно средств - приостанавливаем подписку
            await suspend_daily_subscription_insufficient_balance(db, subscription)

            # Уведомляем пользователя
            if self._bot:
                await self._notify_insufficient_balance(user, subscription, daily_price)

            logger.info(
                f"Подписка {subscription.id} приостановлена: недостаточно средств "
                f"(баланс: {user.balance_kopeks}, требуется: {daily_price})"
            )
            return "suspended"

        # Списываем средства
        description = f"Суточная оплата тарифа «{tariff.name}»"

        try:
            deducted = await subtract_user_balance(
                db,
                user,
                daily_price,
                description,
            )

            if not deducted:
                logger.warning(f"Не удалось списать средства для подписки {subscription.id}")
                return "error"

            # Создаём транзакцию
            await create_transaction(
                db=db,
                user_id=user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=daily_price,
                description=description,
                payment_method=PaymentMethod.MANUAL,
            )

            # Обновляем время последнего списания и продлеваем подписку
            subscription = await update_daily_charge_time(db, subscription)

            logger.info(
                f"✅ Суточное списание: подписка {subscription.id}, "
                f"сумма {daily_price} коп., пользователь {user.telegram_id}"
            )

            # Синхронизируем с Remnawave (обновляем срок подписки)
            try:
                from app.services.subscription_service import SubscriptionService
                subscription_service = SubscriptionService()
                await subscription_service.create_remnawave_user(
                    db,
                    subscription,
                    reset_traffic=False,
                    reset_reason=None,
                )
            except Exception as e:
                logger.warning(f"Не удалось обновить Remnawave: {e}")

            # Уведомляем пользователя
            if self._bot:
                await self._notify_daily_charge(user, subscription, daily_price)

            return "charged"

        except Exception as e:
            logger.error(
                f"Ошибка при списании средств для подписки {subscription.id}: {e}",
                exc_info=True
            )
            return "error"

    async def _notify_daily_charge(self, user, subscription, amount_kopeks: int):
        """Уведомляет пользователя о суточном списании."""
        if not self._bot:
            return

        try:
            texts = get_texts(getattr(user, "language", "ru"))
            amount_rubles = amount_kopeks / 100
            balance_rubles = user.balance_kopeks / 100

            message = (
                f"💳 <b>Суточное списание</b>\n\n"
                f"Списано: {amount_rubles:.2f} ₽\n"
                f"Остаток баланса: {balance_rubles:.2f} ₽\n\n"
                f"Следующее списание через 24 часа."
            )

            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление о списании: {e}")

    async def _notify_insufficient_balance(self, user, subscription, required_amount: int):
        """Уведомляет пользователя о недостатке средств."""
        if not self._bot:
            return

        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            texts = get_texts(getattr(user, "language", "ru"))
            required_rubles = required_amount / 100
            balance_rubles = user.balance_kopeks / 100

            message = (
                f"⚠️ <b>Подписка приостановлена</b>\n\n"
                f"Недостаточно средств для суточной оплаты.\n\n"
                f"Требуется: {required_rubles:.2f} ₽\n"
                f"Баланс: {balance_rubles:.2f} ₽\n\n"
                f"Пополните баланс, чтобы возобновить подписку."
            )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="💳 Пополнить баланс",
                        callback_data="menu_balance"
                    )],
                    [InlineKeyboardButton(
                        text="📱 Моя подписка",
                        callback_data="menu_subscription"
                    )],
                ]
            )

            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление о недостатке средств: {e}")

    async def process_traffic_resets(self) -> dict:
        """
        Сбрасывает докупленный трафик у подписок, у которых истёк срок.

        Returns:
            dict: Статистика обработки
        """
        stats = {
            "checked": 0,
            "reset": 0,
            "errors": 0,
        }

        try:
            async for db in get_db():
                # Находим подписки с истёкшим сроком сброса трафика
                now = datetime.utcnow()
                query = (
                    select(Subscription)
                    .where(Subscription.traffic_reset_at.isnot(None))
                    .where(Subscription.traffic_reset_at <= now)
                    .where(Subscription.purchased_traffic_gb > 0)
                )
                result = await db.execute(query)
                subscriptions = result.scalars().all()
                stats["checked"] = len(subscriptions)

                for subscription in subscriptions:
                    try:
                        await self._reset_subscription_traffic(db, subscription)
                        stats["reset"] += 1
                    except Exception as e:
                        logger.error(
                            f"Ошибка сброса трафика подписки {subscription.id}: {e}",
                            exc_info=True
                        )
                        stats["errors"] += 1

        except Exception as e:
            logger.error(f"Ошибка при получении подписок для сброса трафика: {e}", exc_info=True)

        return stats

    async def _reset_subscription_traffic(self, db: AsyncSession, subscription: Subscription):
        """Сбрасывает докупленный трафик у подписки."""
        purchased_gb = subscription.purchased_traffic_gb or 0
        old_limit = subscription.traffic_limit_gb

        # Получаем тариф для базового лимита
        if subscription.tariff_id:
            from app.database.crud.tariff import get_tariff_by_id
            tariff = await get_tariff_by_id(db, subscription.tariff_id)
            base_limit = tariff.traffic_limit_gb if tariff else old_limit - purchased_gb
        else:
            base_limit = old_limit - purchased_gb

        # Сбрасываем докупленный трафик
        subscription.traffic_limit_gb = max(0, base_limit)
        subscription.purchased_traffic_gb = 0
        subscription.traffic_reset_at = None
        subscription.updated_at = datetime.utcnow()

        await db.commit()

        logger.info(
            f"🔄 Сброс докупленного трафика: подписка {subscription.id}, "
            f"было {old_limit} ГБ, стало {subscription.traffic_limit_gb} ГБ "
            f"(сброшено {purchased_gb} ГБ)"
        )

        # Синхронизируем с RemnaWave
        try:
            from app.services.subscription_service import SubscriptionService
            subscription_service = SubscriptionService()
            await subscription_service.update_remnawave_user(db, subscription)
        except Exception as e:
            logger.warning(f"Не удалось синхронизировать с RemnaWave после сброса трафика: {e}")

        # Уведомляем пользователя
        if self._bot and subscription.user_id:
            user = await get_user_by_id(db, subscription.user_id)
            if user:
                await self._notify_traffic_reset(user, subscription, purchased_gb)

    async def _notify_traffic_reset(self, user: User, subscription: Subscription, reset_gb: int):
        """Уведомляет пользователя о сбросе докупленного трафика."""
        if not self._bot:
            return

        try:
            message = (
                f"ℹ️ <b>Сброс докупленного трафика</b>\n\n"
                f"Ваш докупленный трафик ({reset_gb} ГБ) был сброшен, "
                f"так как прошло 30 дней с момента первой докупки.\n\n"
                f"Текущий лимит трафика: {subscription.traffic_limit_gb} ГБ\n\n"
                f"Вы можете докупить трафик снова в любое время."
            )

            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление о сбросе трафика: {e}")

    async def start_monitoring(self):
        """Запускает периодическую проверку суточных подписок и сброса трафика."""
        self._running = True
        interval_minutes = self.get_check_interval_minutes()

        logger.info(
            f"🔄 Запуск сервиса суточных подписок (интервал: {interval_minutes} мин)"
        )

        while self._running:
            try:
                # Обработка суточных списаний
                stats = await self.process_daily_charges()

                if stats["charged"] > 0 or stats["suspended"] > 0:
                    logger.info(
                        f"📊 Суточные списания: проверено={stats['checked']}, "
                        f"списано={stats['charged']}, приостановлено={stats['suspended']}, "
                        f"ошибок={stats['errors']}"
                    )

                # Обработка сброса докупленного трафика
                traffic_stats = await self.process_traffic_resets()
                if traffic_stats["reset"] > 0:
                    logger.info(
                        f"📊 Сброс трафика: проверено={traffic_stats['checked']}, "
                        f"сброшено={traffic_stats['reset']}, ошибок={traffic_stats['errors']}"
                    )
            except Exception as e:
                logger.error(f"Ошибка в цикле проверки суточных подписок: {e}", exc_info=True)

            await asyncio.sleep(interval_minutes * 60)

    def stop_monitoring(self):
        """Останавливает периодическую проверку."""
        self._running = False
        logger.info("⏹️ Сервис суточных подписок остановлен")


# Глобальный экземпляр сервиса
daily_subscription_service = DailySubscriptionService()


__all__ = ["DailySubscriptionService", "daily_subscription_service"]
