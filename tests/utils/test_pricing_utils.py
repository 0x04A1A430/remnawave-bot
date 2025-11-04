"""
Тесты для утилит ценообразования и форматирования цен.

Этот модуль тестирует функции из app/utils/pricing_utils.py и app/localization/texts.py,
особенно функции отображения цен со скидками на кнопках подписки.
"""

import pytest
from unittest.mock import patch, MagicMock
from typing import Dict, Any

from app.utils.pricing_utils import format_period_option_label
from app.localization.texts import _build_dynamic_values


class TestFormatPeriodOptionLabel:
    """Тесты для функции format_period_option_label."""

    def test_format_with_price_only_no_discount(self) -> None:
        """Цена без скидки должна отображаться в простом формате."""
        result = format_period_option_label("📅 30 дней", 99000)
        assert result == "📅 30 дней - 990 ₽"

    def test_format_with_discount_shows_strikethrough(self) -> None:
        """Цена со скидкой должна показывать зачёркнутую оригинальную цену."""
        result = format_period_option_label(
            "📅 30 дней",
            price=69300,
            original_price=99000,
            discount_percent=30
        )
        assert result == "📅 30 дней - <s>990 ₽</s> 693 ₽ (-30%)"

    def test_format_with_zero_price_returns_label_only(self) -> None:
        """Нулевая цена должна возвращать только метку без цены."""
        result = format_period_option_label("📅 30 дней", 0)
        assert result == "📅 30 дней"

    def test_format_with_negative_price_returns_label_only(self) -> None:
        """Отрицательная цена должна возвращать только метку."""
        result = format_period_option_label("📅 30 дней", -1000)
        assert result == "📅 30 дней"

    def test_format_with_zero_discount_percent_shows_simple_price(self) -> None:
        """Нулевая скидка должна отображать простую цену без зачёркивания."""
        result = format_period_option_label(
            "📅 30 дней",
            price=99000,
            original_price=99000,
            discount_percent=0
        )
        assert result == "📅 30 дней - 990 ₽"

    def test_format_with_original_price_equal_to_final_shows_simple(self) -> None:
        """Если оригинальная цена равна финальной, показывать простой формат."""
        result = format_period_option_label(
            "📅 30 дней",
            price=99000,
            original_price=99000,
            discount_percent=10  # Указана скидка, но цены равны
        )
        assert result == "📅 30 дней - 990 ₽"

    def test_format_with_original_price_less_than_final_shows_simple(self) -> None:
        """Если оригинальная цена меньше финальной (некорректно), показывать простой формат."""
        result = format_period_option_label(
            "📅 30 дней",
            price=99000,
            original_price=50000,
            discount_percent=10
        )
        assert result == "📅 30 дней - 990 ₽"

    @pytest.mark.parametrize(
        "label,price,original,discount,expected",
        [
            # Базовые случаи
            ("📅 14 дней", 50000, 0, 0, "📅 14 дней - 500 ₽"),
            ("📅 30 дней", 99000, 0, 0, "📅 30 дней - 990 ₽"),
            ("📅 360 дней", 899000, 0, 0, "📅 360 дней - 8990 ₽"),

            # Со скидками
            ("📅 30 дней", 69300, 99000, 30, "📅 30 дней - <s>990 ₽</s> 693 ₽ (-30%)"),
            ("📅 90 дней", 188300, 269000, 30, "📅 90 дней - <s>2690 ₽</s> 1883 ₽ (-30%)"),
            ("📅 360 дней", 629300, 899000, 30, "📅 360 дней - <s>8990 ₽</s> 6293 ₽ (-30%)"),

            # Разные проценты скидок
            ("📅 30 дней", 89100, 99000, 10, "📅 30 дней - <s>990 ₽</s> 891 ₽ (-10%)"),
            ("📅 30 дней", 49500, 99000, 50, "📅 30 дней - <s>990 ₽</s> 495 ₽ (-50%)"),

            # Цены с копейками
            ("📅 7 дней", 12345, 0, 0, "📅 7 дней - 123.45 ₽"),
            ("📅 7 дней", 12350, 0, 0, "📅 7 дней - 123.5 ₽"),
        ],
    )
    def test_format_various_scenarios(
        self,
        label: str,
        price: int,
        original: int,
        discount: int,
        expected: str
    ) -> None:
        """Различные сценарии форматирования должны работать корректно."""
        result = format_period_option_label(label, price, original, discount)
        assert result == expected

    def test_format_with_100_percent_discount(self) -> None:
        """100% скидка должна корректно отображаться."""
        result = format_period_option_label(
            "📅 30 дней",
            price=0,
            original_price=99000,
            discount_percent=100
        )
        # Цена 0, поэтому возвращается только label
        assert result == "📅 30 дней"

    def test_format_preserves_label_emojis(self) -> None:
        """Эмодзи в метке должны сохраняться."""
        result = format_period_option_label("🔥 📅 360 дней 🔥", 899000)
        assert result == "🔥 📅 360 дней 🔥 - 8990 ₽"

    def test_format_with_large_prices(self) -> None:
        """Большие цены должны корректно форматироваться."""
        result = format_period_option_label(
            "📅 720 дней",
            price=150000000,  # 1,500,000 рублей
            original_price=200000000,
            discount_percent=25
        )
        assert result == "📅 720 дней - <s>2000000 ₽</s> 1500000 ₽ (-25%)"

    def test_format_with_small_prices_kopeks(self) -> None:
        """Маленькие цены с копейками должны корректно отображаться."""
        result = format_period_option_label(
            "📅 1 день",
            price=5050,  # 50.50 рублей
            original_price=10000,
            discount_percent=50
        )
        assert result == "📅 1 день - <s>100 ₽</s> 50.5 ₽ (-50%)"

    def test_format_without_optional_params_uses_defaults(self) -> None:
        """Вызов без опциональных параметров должен использовать значения по умолчанию."""
        result = format_period_option_label("📅 30 дней", 99000)
        assert result == "📅 30 дней - 990 ₽"


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
