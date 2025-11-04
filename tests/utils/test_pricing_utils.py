"""
Тесты для утилит ценообразования и форматирования цен.

Этот модуль тестирует функции из app/utils/pricing_utils.py и app/localization/texts.py,
особенно функции отображения цен со скидками на кнопках подписки.
"""

import pytest
from unittest.mock import patch, MagicMock
from typing import Dict, Any

from app.localization.texts import _build_dynamic_values


# DEPRECATED: format_period_option_label tests removed - function replaced with unified price_display system


class TestBuildDynamicValues:
    """Тесты для функции _build_dynamic_values из texts.py."""

    @patch('app.localization.texts.settings')
    def test_russian_language_generates_period_keys(self, mock_settings: MagicMock) -> None:
        """Русский язык должен генерировать все ключи периодов."""
        # Настройка моков
        mock_settings.PRICE_14_DAYS = 50000
        mock_settings.PRICE_30_DAYS = 99000
        mock_settings.PRICE_60_DAYS = 189000
        mock_settings.PRICE_90_DAYS = 269000
        mock_settings.PRICE_180_DAYS = 499000
        mock_settings.PRICE_360_DAYS = 899000
        mock_settings.get_base_promo_group_period_discount.return_value = 0
        mock_settings.format_price = lambda x: f"{x // 100} ₽"

        # Мок для traffic цен
        mock_settings.PRICE_TRAFFIC_5GB = 10000
        mock_settings.PRICE_TRAFFIC_10GB = 20000
        mock_settings.PRICE_TRAFFIC_25GB = 30000
        mock_settings.PRICE_TRAFFIC_50GB = 40000
        mock_settings.PRICE_TRAFFIC_100GB = 50000
        mock_settings.PRICE_TRAFFIC_250GB = 60000
        mock_settings.PRICE_TRAFFIC_UNLIMITED = 70000

        result = _build_dynamic_values("ru-RU")

        assert "PERIOD_14_DAYS" in result
        assert "PERIOD_30_DAYS" in result
        assert "PERIOD_60_DAYS" in result
        assert "PERIOD_90_DAYS" in result
        assert "PERIOD_180_DAYS" in result
        assert "PERIOD_360_DAYS" in result

    @patch('app.localization.texts.settings')
    def test_english_language_generates_period_keys(self, mock_settings: MagicMock) -> None:
        """Английский язык должен генерировать все ключи периодов."""
        # Настройка моков
        mock_settings.PRICE_14_DAYS = 50000
        mock_settings.PRICE_30_DAYS = 99000
        mock_settings.PRICE_60_DAYS = 189000
        mock_settings.PRICE_90_DAYS = 269000
        mock_settings.PRICE_180_DAYS = 499000
        mock_settings.PRICE_360_DAYS = 899000
        mock_settings.get_base_promo_group_period_discount.return_value = 0
        mock_settings.format_price = lambda x: f"{x // 100} ₽"

        # Мок для traffic цен
        mock_settings.PRICE_TRAFFIC_5GB = 10000
        mock_settings.PRICE_TRAFFIC_10GB = 20000
        mock_settings.PRICE_TRAFFIC_25GB = 30000
        mock_settings.PRICE_TRAFFIC_50GB = 40000
        mock_settings.PRICE_TRAFFIC_100GB = 50000
        mock_settings.PRICE_TRAFFIC_250GB = 60000
        mock_settings.PRICE_TRAFFIC_UNLIMITED = 70000

        result = _build_dynamic_values("en-US")

        assert "PERIOD_14_DAYS" in result
        assert "PERIOD_30_DAYS" in result
        assert "PERIOD_360_DAYS" in result
        # Проверяем, что используется "days" а не "дней"
        assert "days" in result["PERIOD_30_DAYS"]

    @patch('app.localization.texts.settings')
    @patch('app.utils.pricing_utils.apply_percentage_discount')
    def test_period_with_discount_shows_strikethrough(
        self,
        mock_apply_discount: MagicMock,
        mock_settings: MagicMock
    ) -> None:
        """Период со скидкой должен показывать зачёркнутую цену."""
        # Настройка моков
        mock_settings.PRICE_30_DAYS = 99000
        mock_settings.get_base_promo_group_period_discount.return_value = 30
        mock_apply_discount.return_value = (69300, 29700)  # 30% скидка
        mock_settings.format_price = lambda x: f"{x // 100} ₽"

        # Остальные цены
        mock_settings.PRICE_14_DAYS = 50000
        mock_settings.PRICE_60_DAYS = 189000
        mock_settings.PRICE_90_DAYS = 269000
        mock_settings.PRICE_180_DAYS = 499000
        mock_settings.PRICE_360_DAYS = 899000
        mock_settings.PRICE_TRAFFIC_5GB = 10000
        mock_settings.PRICE_TRAFFIC_10GB = 20000
        mock_settings.PRICE_TRAFFIC_25GB = 30000
        mock_settings.PRICE_TRAFFIC_50GB = 40000
        mock_settings.PRICE_TRAFFIC_100GB = 50000
        mock_settings.PRICE_TRAFFIC_250GB = 60000
        mock_settings.PRICE_TRAFFIC_UNLIMITED = 70000

        result = _build_dynamic_values("ru-RU")

        # Проверяем, что есть зачёркивание и процент скидки
        assert "<s>990 ₽</s>" in result["PERIOD_30_DAYS"]
        assert "(-30%)" in result["PERIOD_30_DAYS"]

    @patch('app.localization.texts.settings')
    def test_period_360_with_discount_has_fire_emojis(self, mock_settings: MagicMock) -> None:
        """Период 360 дней со скидкой должен иметь огоньки 🔥."""
        # Настройка моков для 360 дней со скидкой
        mock_settings.PRICE_360_DAYS = 899000

        def get_discount(period_days: int) -> int:
            return 30 if period_days == 360 else 0

        mock_settings.get_base_promo_group_period_discount.side_effect = get_discount
        mock_settings.format_price = lambda x: f"{x // 100} ₽"

        # Остальные цены
        mock_settings.PRICE_14_DAYS = 50000
        mock_settings.PRICE_30_DAYS = 99000
        mock_settings.PRICE_60_DAYS = 189000
        mock_settings.PRICE_90_DAYS = 269000
        mock_settings.PRICE_180_DAYS = 499000
        mock_settings.PRICE_TRAFFIC_5GB = 10000
        mock_settings.PRICE_TRAFFIC_10GB = 20000
        mock_settings.PRICE_TRAFFIC_25GB = 30000
        mock_settings.PRICE_TRAFFIC_50GB = 40000
        mock_settings.PRICE_TRAFFIC_100GB = 50000
        mock_settings.PRICE_TRAFFIC_250GB = 60000
        mock_settings.PRICE_TRAFFIC_UNLIMITED = 70000

        result = _build_dynamic_values("ru-RU")

        # Проверяем наличие огоньков
        assert result["PERIOD_360_DAYS"].startswith("🔥")
        assert result["PERIOD_360_DAYS"].endswith("🔥")
        assert result["PERIOD_360_DAYS"].count("🔥") == 2

    @patch('app.localization.texts.settings')
    def test_period_360_without_discount_no_fire_emojis(self, mock_settings: MagicMock) -> None:
        """Период 360 дней без скидки НЕ должен иметь огоньки 🔥."""
        # Настройка моков для 360 дней БЕЗ скидки
        mock_settings.PRICE_360_DAYS = 899000
        mock_settings.get_base_promo_group_period_discount.return_value = 0  # Нет скидки
        mock_settings.format_price = lambda x: f"{x // 100} ₽"

        # Остальные цены
        mock_settings.PRICE_14_DAYS = 50000
        mock_settings.PRICE_30_DAYS = 99000
        mock_settings.PRICE_60_DAYS = 189000
        mock_settings.PRICE_90_DAYS = 269000
        mock_settings.PRICE_180_DAYS = 499000
        mock_settings.PRICE_TRAFFIC_5GB = 10000
        mock_settings.PRICE_TRAFFIC_10GB = 20000
        mock_settings.PRICE_TRAFFIC_25GB = 30000
        mock_settings.PRICE_TRAFFIC_50GB = 40000
        mock_settings.PRICE_TRAFFIC_100GB = 50000
        mock_settings.PRICE_TRAFFIC_250GB = 60000
        mock_settings.PRICE_TRAFFIC_UNLIMITED = 70000

        result = _build_dynamic_values("ru-RU")

        # Проверяем отсутствие огоньков
        assert "🔥" not in result["PERIOD_360_DAYS"]
        # Но должна быть просто цена
        assert "8990 ₽" in result["PERIOD_360_DAYS"]

    @patch('app.localization.texts.settings')
    def test_other_periods_never_have_fire_emojis(self, mock_settings: MagicMock) -> None:
        """Другие периоды (не 360) никогда не должны иметь огоньки, даже со скидкой."""
        # Настройка моков - 30 дней со скидкой
        mock_settings.PRICE_30_DAYS = 99000

        def get_discount(period_days: int) -> int:
            return 30 if period_days == 30 else 0

        mock_settings.get_base_promo_group_period_discount.side_effect = get_discount
        mock_settings.format_price = lambda x: f"{x // 100} ₽"

        # Остальные цены
        mock_settings.PRICE_14_DAYS = 50000
        mock_settings.PRICE_60_DAYS = 189000
        mock_settings.PRICE_90_DAYS = 269000
        mock_settings.PRICE_180_DAYS = 499000
        mock_settings.PRICE_360_DAYS = 899000
        mock_settings.PRICE_TRAFFIC_5GB = 10000
        mock_settings.PRICE_TRAFFIC_10GB = 20000
        mock_settings.PRICE_TRAFFIC_25GB = 30000
        mock_settings.PRICE_TRAFFIC_50GB = 40000
        mock_settings.PRICE_TRAFFIC_100GB = 50000
        mock_settings.PRICE_TRAFFIC_250GB = 60000
        mock_settings.PRICE_TRAFFIC_UNLIMITED = 70000

        result = _build_dynamic_values("ru-RU")

        # 30 дней со скидкой не должно иметь огоньков
        assert "🔥" not in result["PERIOD_30_DAYS"]
        # Но должна быть скидка
        assert "<s>" in result["PERIOD_30_DAYS"]

    @patch('app.localization.texts.settings')
    def test_returns_empty_dict_for_unknown_language(self, mock_settings: MagicMock) -> None:
        """Неизвестный язык должен возвращать пустой словарь."""
        result = _build_dynamic_values("fr-FR")  # Французский не поддерживается
        assert result == {}

    @patch('app.localization.texts.settings')
    def test_language_code_extraction_works(self, mock_settings: MagicMock) -> None:
        """Должна корректно извлекаться языковая часть из locale."""
        # Настройка моков
        mock_settings.PRICE_14_DAYS = 50000
        mock_settings.PRICE_30_DAYS = 99000
        mock_settings.PRICE_60_DAYS = 189000
        mock_settings.PRICE_90_DAYS = 269000
        mock_settings.PRICE_180_DAYS = 499000
        mock_settings.PRICE_360_DAYS = 899000
        mock_settings.get_base_promo_group_period_discount.return_value = 0
        mock_settings.format_price = lambda x: f"{x // 100} ₽"
        mock_settings.PRICE_TRAFFIC_5GB = 10000
        mock_settings.PRICE_TRAFFIC_10GB = 20000
        mock_settings.PRICE_TRAFFIC_25GB = 30000
        mock_settings.PRICE_TRAFFIC_50GB = 40000
        mock_settings.PRICE_TRAFFIC_100GB = 50000
        mock_settings.PRICE_TRAFFIC_250GB = 60000
        mock_settings.PRICE_TRAFFIC_UNLIMITED = 70000

        # Тест с полным locale кодом
        result1 = _build_dynamic_values("ru-RU")
        result2 = _build_dynamic_values("ru")
        result3 = _build_dynamic_values("RU-ru")

        # Все должны вернуть русские значения
        assert "дней" in result1["PERIOD_30_DAYS"]
        assert "дней" in result2["PERIOD_30_DAYS"]
        assert "дней" in result3["PERIOD_30_DAYS"]

    @patch('app.localization.texts.settings')
    def test_traffic_keys_also_generated(self, mock_settings: MagicMock) -> None:
        """Должны генерироваться не только периоды, но и ключи трафика."""
        # Настройка моков
        mock_settings.PRICE_14_DAYS = 50000
        mock_settings.PRICE_30_DAYS = 99000
        mock_settings.PRICE_60_DAYS = 189000
        mock_settings.PRICE_90_DAYS = 269000
        mock_settings.PRICE_180_DAYS = 499000
        mock_settings.PRICE_360_DAYS = 899000
        mock_settings.get_base_promo_group_period_discount.return_value = 0
        mock_settings.format_price = lambda x: f"{x // 100} ₽"
        mock_settings.PRICE_TRAFFIC_5GB = 10000
        mock_settings.PRICE_TRAFFIC_10GB = 20000
        mock_settings.PRICE_TRAFFIC_25GB = 30000
        mock_settings.PRICE_TRAFFIC_50GB = 40000
        mock_settings.PRICE_TRAFFIC_100GB = 50000
        mock_settings.PRICE_TRAFFIC_250GB = 60000
        mock_settings.PRICE_TRAFFIC_UNLIMITED = 70000

        result = _build_dynamic_values("ru-RU")

        # Проверяем наличие ключей трафика
        assert "TRAFFIC_5GB" in result
        assert "TRAFFIC_10GB" in result
        assert "TRAFFIC_UNLIMITED" in result
        assert "SUPPORT_INFO" in result
