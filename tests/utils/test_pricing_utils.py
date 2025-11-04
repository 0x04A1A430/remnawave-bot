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
    """
    Тесты для функции _build_dynamic_values из texts.py.

    NOTE: PERIOD_*_DAYS константы были удалены из _build_dynamic_values,
    так как теперь кнопки периодов генерируются динамически в get_subscription_period_keyboard()
    с учетом персональных скидок пользователя.
    """

    @patch('app.localization.texts.settings')
    def test_returns_empty_dict_for_unknown_language(self, mock_settings: MagicMock) -> None:
        """Неизвестный язык должен возвращать пустой словарь."""
        result = _build_dynamic_values("fr-FR")  # Французский не поддерживается
        assert result == {}

    @patch('app.localization.texts.settings')
    def test_traffic_keys_also_generated(self, mock_settings: MagicMock) -> None:
        """Должны генерироваться ключи трафика и другие динамические значения."""
        # Настройка моков для traffic цен
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
