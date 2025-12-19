"""
Модуль конструктора меню.

Структура модуля:
- constants.py - константы и дефолтная конфигурация
- context.py - MenuContext для построения меню
- history_service.py - сервис истории изменений
- stats_service.py - сервис статистики кликов
- service.py - основной MenuLayoutService
"""

from .constants import (
    MENU_LAYOUT_CONFIG_KEY,
    DEFAULT_MENU_CONFIG,
    BUILTIN_BUTTONS_INFO,
    AVAILABLE_CALLBACKS,
    DYNAMIC_PLACEHOLDERS,
)
from .context import MenuContext
from .history_service import MenuLayoutHistoryService
from .stats_service import MenuLayoutStatsService
from .service import MenuLayoutService

__all__ = [
    # Константы
    "MENU_LAYOUT_CONFIG_KEY",
    "DEFAULT_MENU_CONFIG",
    "BUILTIN_BUTTONS_INFO",
    "AVAILABLE_CALLBACKS",
    "DYNAMIC_PLACEHOLDERS",
    # Классы
    "MenuContext",
    "MenuLayoutService",
    "MenuLayoutHistoryService",
    "MenuLayoutStatsService",
]
