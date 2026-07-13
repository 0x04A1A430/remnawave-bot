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
    AVAILABLE_CALLBACKS,
    BUILTIN_BUTTONS_INFO,
    DEFAULT_MENU_CONFIG,
    DYNAMIC_PLACEHOLDERS,
    MENU_LAYOUT_CONFIG_KEY,
)
from .context import MenuContext
from .history_service import MenuLayoutHistoryService
from .service import MenuLayoutService
from .stats_service import MenuLayoutStatsService


__all__ = [
    'AVAILABLE_CALLBACKS',
    'BUILTIN_BUTTONS_INFO',
    'DEFAULT_MENU_CONFIG',
    'DYNAMIC_PLACEHOLDERS',
    # Константы
    'MENU_LAYOUT_CONFIG_KEY',
    # Классы
    'MenuContext',
    'MenuLayoutHistoryService',
    'MenuLayoutService',
    'MenuLayoutStatsService',
]
