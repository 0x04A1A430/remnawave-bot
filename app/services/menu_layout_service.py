"""
Сервис конструктора меню - управление конфигурацией через API.

ВНИМАНИЕ: Этот файл оставлен для обратной совместимости.
Фактическая реализация находится в app/services/menu_layout/

Структура модуля:
- app/services/menu_layout/constants.py - константы
- app/services/menu_layout/context.py - MenuContext
- app/services/menu_layout/history_service.py - история изменений
- app/services/menu_layout/stats_service.py - статистика кликов
- app/services/menu_layout/service.py - основной сервис
"""

# Реэкспорт для обратной совместимости
from app.services.menu_layout import (
    # Константы
    MENU_LAYOUT_CONFIG_KEY,
    DEFAULT_MENU_CONFIG,
    BUILTIN_BUTTONS_INFO,
    AVAILABLE_CALLBACKS,
    DYNAMIC_PLACEHOLDERS,
    # Классы
    MenuContext,
    MenuLayoutService,
    MenuLayoutHistoryService,
    MenuLayoutStatsService,
)

__all__ = [
    "MENU_LAYOUT_CONFIG_KEY",
    "DEFAULT_MENU_CONFIG",
    "BUILTIN_BUTTONS_INFO",
    "AVAILABLE_CALLBACKS",
    "DYNAMIC_PLACEHOLDERS",
    "MenuContext",
    "MenuLayoutService",
    "MenuLayoutHistoryService",
    "MenuLayoutStatsService",
]
