"""
Тесты для ModemService - управление модемом в подписке.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from app.services.modem_service import (
    ModemService,
    ModemError,
    ModemAvailabilityResult,
    ModemPriceResult,
    ModemEnableResult,
    ModemDisableResult,
    get_modem_service,
    MODEM_WARNING_DAYS_CRITICAL,
    MODEM_WARNING_DAYS_INFO,
)


def create_mock_settings():
    """Создаёт мок настроек приложения."""
    settings = MagicMock()
    settings.is_modem_enabled.return_value = True
    settings.get_modem_price_per_month.return_value = 10000  # 100 рублей
    settings.get_modem_period_discount.return_value = 0
    return settings


def create_sample_user():
    """Создаёт пример пользователя."""
    user = SimpleNamespace(
        id=1,
        telegram_id=123456789,
        balance_kopeks=50000,  # 500 рублей
        language="ru",
        subscription=None,
    )
    return user


def create_sample_subscription():
    """Создаёт пример подписки."""
    subscription = SimpleNamespace(
        id=1,
        user_id=1,
        is_trial=False,
        modem_enabled=False,
        device_limit=2,
        end_date=datetime.utcnow() + timedelta(days=30),
        updated_at=datetime.utcnow(),
    )
    return subscription


def create_trial_subscription():
    """Создаёт триальную подписку."""
    subscription = SimpleNamespace(
        id=2,
        user_id=1,
        is_trial=True,
        modem_enabled=False,
        device_limit=1,
        end_date=datetime.utcnow() + timedelta(days=7),
        updated_at=datetime.utcnow(),
    )
    return subscription


def create_modem_service(monkeypatch):
    """Создаёт ModemService с замоканными настройками."""
    mock_settings = create_mock_settings()
    monkeypatch.setattr('app.services.modem_service.settings', mock_settings)
    return ModemService(), mock_settings


class TestModemServiceAvailability:
    """Тесты проверки доступности модема."""

    def test_check_availability_no_subscription(self, monkeypatch):
        """Модем недоступен без подписки."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_user.subscription = None

        result = modem_service.check_availability(sample_user)

        assert not result.available
        assert result.error == ModemError.NO_SUBSCRIPTION
        assert not result.modem_enabled

    def test_check_availability_trial_subscription(self, monkeypatch):
        """Модем недоступен для триальной подписки."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        trial_subscription = create_trial_subscription()
        sample_user.subscription = trial_subscription

        result = modem_service.check_availability(sample_user)

        assert not result.available
        assert result.error == ModemError.TRIAL_SUBSCRIPTION
        assert not result.modem_enabled

    def test_check_availability_modem_disabled_in_settings(self, monkeypatch):
        """Модем недоступен, если отключён в настройках."""
        modem_service, mock_settings = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_subscription = create_sample_subscription()
        sample_user.subscription = sample_subscription
        mock_settings.is_modem_enabled.return_value = False

        result = modem_service.check_availability(sample_user)

        assert not result.available
        assert result.error == ModemError.MODEM_DISABLED

    def test_check_availability_success(self, monkeypatch):
        """Модем доступен для платной подписки."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_subscription = create_sample_subscription()
        sample_user.subscription = sample_subscription

        result = modem_service.check_availability(sample_user)

        assert result.available
        assert result.error is None
        assert not result.modem_enabled

    def test_check_availability_for_enable_already_enabled(self, monkeypatch):
        """Нельзя подключить уже подключенный модем."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_subscription = create_sample_subscription()
        sample_subscription.modem_enabled = True
        sample_user.subscription = sample_subscription

        result = modem_service.check_availability(sample_user, for_enable=True)

        assert not result.available
        assert result.error == ModemError.ALREADY_ENABLED
        assert result.modem_enabled

    def test_check_availability_for_disable_not_enabled(self, monkeypatch):
        """Нельзя отключить неподключенный модем."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_subscription = create_sample_subscription()
        sample_subscription.modem_enabled = False
        sample_user.subscription = sample_subscription

        result = modem_service.check_availability(sample_user, for_disable=True)

        assert not result.available
        assert result.error == ModemError.NOT_ENABLED
        assert not result.modem_enabled


class TestModemServicePricing:
    """Тесты расчёта цены модема."""

    def test_calculate_price_one_month(self, monkeypatch):
        """Расчёт цены на 1 месяц."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_subscription = create_sample_subscription()
        sample_subscription.end_date = datetime.utcnow() + timedelta(days=30)

        result = modem_service.calculate_price(sample_subscription)

        assert result.base_price == 10000
        assert result.final_price == 10000
        assert result.charged_months == 1
        assert result.discount_percent == 0
        assert not result.has_discount

    def test_calculate_price_three_months(self, monkeypatch):
        """Расчёт цены на 3 месяца."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_subscription = create_sample_subscription()
        sample_subscription.end_date = datetime.utcnow() + timedelta(days=90)

        result = modem_service.calculate_price(sample_subscription)

        assert result.base_price == 30000  # 3 * 10000
        assert result.charged_months == 3

    def test_calculate_price_with_discount(self, monkeypatch):
        """Расчёт цены со скидкой."""
        modem_service, mock_settings = create_modem_service(monkeypatch)
        sample_subscription = create_sample_subscription()
        sample_subscription.end_date = datetime.utcnow() + timedelta(days=90)
        mock_settings.get_modem_period_discount.return_value = 10  # 10% скидка

        result = modem_service.calculate_price(sample_subscription)

        assert result.base_price == 30000
        assert result.discount_percent == 10
        assert result.discount_amount == 3000
        assert result.final_price == 27000
        assert result.has_discount


class TestModemServiceBalance:
    """Тесты проверки баланса."""

    def test_check_balance_sufficient(self, monkeypatch):
        """Баланса достаточно."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_user.balance_kopeks = 50000

        has_funds, missing = modem_service.check_balance(sample_user, 10000)

        assert has_funds
        assert missing == 0

    def test_check_balance_insufficient(self, monkeypatch):
        """Баланса недостаточно."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_user.balance_kopeks = 5000

        has_funds, missing = modem_service.check_balance(sample_user, 10000)

        assert not has_funds
        assert missing == 5000

    def test_check_balance_zero_price(self, monkeypatch):
        """Нулевая цена - всегда достаточно."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_user.balance_kopeks = 0

        has_funds, missing = modem_service.check_balance(sample_user, 0)

        assert has_funds
        assert missing == 0


class TestModemServicePeriodWarning:
    """Тесты предупреждений о сроке действия."""

    def test_warning_critical(self, monkeypatch):
        """Критическое предупреждение при <= 7 днях."""
        modem_service, _ = create_modem_service(monkeypatch)
        assert modem_service.get_period_warning_level(7) == "critical"
        assert modem_service.get_period_warning_level(5) == "critical"
        assert modem_service.get_period_warning_level(1) == "critical"

    def test_warning_info(self, monkeypatch):
        """Информационное предупреждение при <= 30 днях."""
        modem_service, _ = create_modem_service(monkeypatch)
        assert modem_service.get_period_warning_level(30) == "info"
        assert modem_service.get_period_warning_level(15) == "info"
        assert modem_service.get_period_warning_level(8) == "info"

    def test_warning_none(self, monkeypatch):
        """Нет предупреждения при > 30 днях."""
        modem_service, _ = create_modem_service(monkeypatch)
        assert modem_service.get_period_warning_level(31) is None
        assert modem_service.get_period_warning_level(60) is None
        assert modem_service.get_period_warning_level(90) is None


class TestModemServiceEnable:
    """Тесты подключения модема."""

    async def test_enable_modem_success(self, monkeypatch):
        """Успешное подключение модема."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_subscription = create_sample_subscription()
        sample_user.subscription = sample_subscription
        sample_user.balance_kopeks = 50000

        mock_db = AsyncMock()
        mock_subtract = AsyncMock(return_value=True)
        mock_create_transaction = AsyncMock()
        mock_update_remnawave = AsyncMock()

        monkeypatch.setattr(
            'app.services.modem_service.subtract_user_balance',
            mock_subtract
        )
        monkeypatch.setattr(
            'app.services.modem_service.create_transaction',
            mock_create_transaction
        )
        modem_service._subscription_service.update_remnawave_user = mock_update_remnawave

        result = await modem_service.enable_modem(
            mock_db, sample_user, sample_subscription
        )

        assert result.success
        assert result.error is None
        assert result.charged_amount == 10000
        assert sample_subscription.modem_enabled is True
        assert sample_subscription.device_limit == 3  # было 2, стало 3

    async def test_enable_modem_insufficient_funds(self, monkeypatch):
        """Недостаточно средств для подключения."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_subscription = create_sample_subscription()
        sample_user.subscription = sample_subscription
        sample_user.balance_kopeks = 1000  # недостаточно

        mock_db = AsyncMock()

        result = await modem_service.enable_modem(
            mock_db, sample_user, sample_subscription
        )

        assert not result.success
        assert result.error == ModemError.INSUFFICIENT_FUNDS

    async def test_enable_modem_charge_error(self, monkeypatch):
        """Ошибка списания средств."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_subscription = create_sample_subscription()
        sample_user.subscription = sample_subscription
        sample_user.balance_kopeks = 50000

        mock_db = AsyncMock()
        mock_subtract = AsyncMock(return_value=False)  # ошибка списания

        monkeypatch.setattr(
            'app.services.modem_service.subtract_user_balance',
            mock_subtract
        )

        result = await modem_service.enable_modem(
            mock_db, sample_user, sample_subscription
        )

        assert not result.success
        assert result.error == ModemError.CHARGE_ERROR


class TestModemServiceDisable:
    """Тесты отключения модема."""

    async def test_disable_modem_success(self, monkeypatch):
        """Успешное отключение модема."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_user = create_sample_user()
        sample_subscription = create_sample_subscription()
        sample_subscription.modem_enabled = True
        sample_subscription.device_limit = 3
        sample_user.subscription = sample_subscription

        mock_db = AsyncMock()
        mock_update_remnawave = AsyncMock()
        modem_service._subscription_service.update_remnawave_user = mock_update_remnawave

        result = await modem_service.disable_modem(
            mock_db, sample_user, sample_subscription
        )

        assert result.success
        assert result.error is None
        assert sample_subscription.modem_enabled is False
        assert sample_subscription.device_limit == 2  # было 3, стало 2


class TestModemServiceSingleton:
    """Тесты singleton паттерна."""

    def test_get_modem_service_returns_same_instance(self, monkeypatch):
        """get_modem_service возвращает один и тот же экземпляр."""
        # Сбрасываем глобальный экземпляр
        import app.services.modem_service as modem_module
        modem_module._modem_service = None

        mock_settings = create_mock_settings()
        monkeypatch.setattr('app.services.modem_service.settings', mock_settings)

        service1 = get_modem_service()
        service2 = get_modem_service()

        assert service1 is service2


class TestModemEnabledGetter:
    """Тесты безопасного получения статуса модема."""

    def test_get_modem_enabled_true(self, monkeypatch):
        """Модем включён."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_subscription = create_sample_subscription()
        sample_subscription.modem_enabled = True

        assert modem_service.get_modem_enabled(sample_subscription) is True

    def test_get_modem_enabled_false(self, monkeypatch):
        """Модем выключен."""
        modem_service, _ = create_modem_service(monkeypatch)
        sample_subscription = create_sample_subscription()
        sample_subscription.modem_enabled = False

        assert modem_service.get_modem_enabled(sample_subscription) is False

    def test_get_modem_enabled_none_subscription(self, monkeypatch):
        """Подписка None."""
        modem_service, _ = create_modem_service(monkeypatch)
        assert modem_service.get_modem_enabled(None) is False

    def test_get_modem_enabled_no_attribute(self, monkeypatch):
        """У подписки нет атрибута modem_enabled."""
        modem_service, _ = create_modem_service(monkeypatch)
        subscription = SimpleNamespace(id=1)  # без modem_enabled

        assert modem_service.get_modem_enabled(subscription) is False
