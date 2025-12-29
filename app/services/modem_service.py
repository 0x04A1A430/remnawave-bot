"""
Сервис для управления модемом в подписке.

Модем - это дополнительное устройство, которое можно подключить к подписке
за отдельную плату. При подключении увеличивается лимит устройств.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Subscription, User, TransactionType
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.services.subscription_service import SubscriptionService
from app.utils.pricing_utils import get_remaining_months, calculate_prorated_price

logger = logging.getLogger(__name__)


class ModemError(Enum):
    """Типы ошибок при работе с модемом."""
    NO_SUBSCRIPTION = "no_subscription"
    TRIAL_SUBSCRIPTION = "trial_subscription"
    MODEM_DISABLED = "modem_disabled"
    ALREADY_ENABLED = "already_enabled"
    NOT_ENABLED = "not_enabled"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    CHARGE_ERROR = "charge_error"
    UPDATE_ERROR = "update_error"


@dataclass
class ModemAvailabilityResult:
    """Результат проверки доступности модема."""
    available: bool
    error: Optional[ModemError] = None
    modem_enabled: bool = False


@dataclass
class ModemPriceResult:
    """Результат расчёта цены модема."""
    base_price: int
    final_price: int
    discount_percent: int
    discount_amount: int
    charged_months: int
    remaining_days: int
    end_date: datetime

    @property
    def has_discount(self) -> bool:
        return self.discount_percent > 0


@dataclass
class ModemEnableResult:
    """Результат подключения модема."""
    success: bool
    error: Optional[ModemError] = None
    charged_amount: int = 0
    new_device_limit: int = 0


@dataclass
class ModemDisableResult:
    """Результат отключения модема."""
    success: bool
    error: Optional[ModemError] = None
    new_device_limit: int = 0


# Константы для предупреждений о сроке действия
MODEM_WARNING_DAYS_CRITICAL = 7
MODEM_WARNING_DAYS_INFO = 30


class ModemService:
    """
    Сервис для управления модемом в подписке.

    Инкапсулирует всю бизнес-логику:
    - Проверки доступности
    - Расчёт цен и скидок
    - Подключение/отключение модема
    - Синхронизация с RemnaWave
    """

    def __init__(self):
        self._subscription_service = SubscriptionService()

    @staticmethod
    def is_modem_feature_enabled() -> bool:
        """Проверяет, включена ли функция модема в настройках."""
        return settings.is_modem_enabled()

    @staticmethod
    def get_modem_enabled(subscription: Optional[Subscription]) -> bool:
        """Безопасно получает статус модема из подписки."""
        if subscription is None:
            return False
        return getattr(subscription, 'modem_enabled', False) or False

    def check_availability(
        self,
        user: User,
        for_enable: bool = False,
        for_disable: bool = False
    ) -> ModemAvailabilityResult:
        """
        Проверяет доступность модема для пользователя.

        Args:
            user: Пользователь
            for_enable: Проверка для подключения (модем должен быть отключен)
            for_disable: Проверка для отключения (модем должен быть включен)

        Returns:
            ModemAvailabilityResult с результатом проверки
        """
        subscription = user.subscription
        modem_enabled = self.get_modem_enabled(subscription)

        if not subscription:
            return ModemAvailabilityResult(
                available=False,
                error=ModemError.NO_SUBSCRIPTION,
                modem_enabled=modem_enabled
            )

        if subscription.is_trial:
            return ModemAvailabilityResult(
                available=False,
                error=ModemError.TRIAL_SUBSCRIPTION,
                modem_enabled=modem_enabled
            )

        if not self.is_modem_feature_enabled():
            return ModemAvailabilityResult(
                available=False,
                error=ModemError.MODEM_DISABLED,
                modem_enabled=modem_enabled
            )

        if for_enable and modem_enabled:
            return ModemAvailabilityResult(
                available=False,
                error=ModemError.ALREADY_ENABLED,
                modem_enabled=modem_enabled
            )

        if for_disable and not modem_enabled:
            return ModemAvailabilityResult(
                available=False,
                error=ModemError.NOT_ENABLED,
                modem_enabled=modem_enabled
            )

        return ModemAvailabilityResult(
            available=True,
            modem_enabled=modem_enabled
        )

    def calculate_price(self, subscription: Subscription) -> ModemPriceResult:
        """
        Рассчитывает стоимость подключения модема.

        Использует пропорциональную цену на основе оставшегося времени подписки
        и применяет скидки в зависимости от периода.

        Args:
            subscription: Подписка пользователя

        Returns:
            ModemPriceResult с детализацией цены
        """
        modem_price_per_month = settings.get_modem_price_per_month()

        base_price, charged_months = calculate_prorated_price(
            modem_price_per_month,
            subscription.end_date,
        )

        now = datetime.utcnow()
        remaining_days = max(0, (subscription.end_date - now).days)

        discount_percent = settings.get_modem_period_discount(charged_months)
        if discount_percent > 0:
            discount_amount = base_price * discount_percent // 100
            final_price = base_price - discount_amount
        else:
            discount_amount = 0
            final_price = base_price

        return ModemPriceResult(
            base_price=base_price,
            final_price=final_price,
            discount_percent=discount_percent,
            discount_amount=discount_amount,
            charged_months=charged_months,
            remaining_days=remaining_days,
            end_date=subscription.end_date
        )

    def check_balance(self, user: User, price: int) -> Tuple[bool, int]:
        """
        Проверяет достаточность баланса.

        Args:
            user: Пользователь
            price: Требуемая сумма

        Returns:
            Tuple[достаточно ли средств, недостающая сумма]
        """
        if price <= 0:
            return True, 0

        if user.balance_kopeks >= price:
            return True, 0

        missing = price - user.balance_kopeks
        return False, missing

    async def enable_modem(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription
    ) -> ModemEnableResult:
        """
        Подключает модем к подписке.

        Выполняет:
        1. Расчёт цены
        2. Проверку баланса
        3. Списание средств
        4. Создание транзакции
        5. Обновление подписки
        6. Синхронизацию с RemnaWave

        Args:
            db: Сессия базы данных
            user: Пользователь
            subscription: Подписка

        Returns:
            ModemEnableResult с результатом операции
        """
        price_info = self.calculate_price(subscription)
        price = price_info.final_price

        has_funds, _ = self.check_balance(user, price)
        if not has_funds:
            return ModemEnableResult(
                success=False,
                error=ModemError.INSUFFICIENT_FUNDS
            )

        try:
            if price > 0:
                success = await subtract_user_balance(
                    db, user, price,
                    "Подключение модема"
                )

                if not success:
                    return ModemEnableResult(
                        success=False,
                        error=ModemError.CHARGE_ERROR
                    )

                await create_transaction(
                    db=db,
                    user_id=user.id,
                    type=TransactionType.SUBSCRIPTION_PAYMENT,
                    amount_kopeks=price,
                    description=f"Подключение модема на {price_info.charged_months} мес"
                )

            subscription.modem_enabled = True
            subscription.device_limit = (subscription.device_limit or 1) + 1
            subscription.updated_at = datetime.utcnow()

            await db.commit()

            await self._subscription_service.update_remnawave_user(db, subscription)

            await db.refresh(user)
            await db.refresh(subscription)

            logger.info(
                f"Пользователь {user.telegram_id} подключил модем, списано: {price / 100}₽"
            )

            return ModemEnableResult(
                success=True,
                charged_amount=price,
                new_device_limit=subscription.device_limit
            )

        except Exception as e:
            logger.error(f"Ошибка подключения модема для пользователя {user.telegram_id}: {e}")
            await db.rollback()
            return ModemEnableResult(
                success=False,
                error=ModemError.UPDATE_ERROR
            )

    async def disable_modem(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription
    ) -> ModemDisableResult:
        """
        Отключает модем от подписки.

        Возврат средств не производится.

        Args:
            db: Сессия базы данных
            user: Пользователь
            subscription: Подписка

        Returns:
            ModemDisableResult с результатом операции
        """
        try:
            subscription.modem_enabled = False
            if subscription.device_limit and subscription.device_limit > 1:
                subscription.device_limit = subscription.device_limit - 1
            subscription.updated_at = datetime.utcnow()

            await db.commit()

            await self._subscription_service.update_remnawave_user(db, subscription)

            await db.refresh(user)
            await db.refresh(subscription)

            logger.info(f"Пользователь {user.telegram_id} отключил модем")

            return ModemDisableResult(
                success=True,
                new_device_limit=subscription.device_limit
            )

        except Exception as e:
            logger.error(f"Ошибка отключения модема для пользователя {user.telegram_id}: {e}")
            await db.rollback()
            return ModemDisableResult(
                success=False,
                error=ModemError.UPDATE_ERROR
            )

    @staticmethod
    def get_period_warning_level(remaining_days: int) -> Optional[str]:
        """
        Определяет уровень предупреждения о сроке действия.

        Args:
            remaining_days: Оставшиеся дни подписки

        Returns:
            "critical" если <= 7 дней
            "info" если <= 30 дней
            None если больше 30 дней
        """
        if remaining_days <= MODEM_WARNING_DAYS_CRITICAL:
            return "critical"
        if remaining_days <= MODEM_WARNING_DAYS_INFO:
            return "info"
        return None


# Singleton instance для использования в хендлерах
_modem_service: Optional[ModemService] = None


def get_modem_service() -> ModemService:
    """Возвращает singleton экземпляр ModemService."""
    global _modem_service
    if _modem_service is None:
        _modem_service = ModemService()
    return _modem_service
