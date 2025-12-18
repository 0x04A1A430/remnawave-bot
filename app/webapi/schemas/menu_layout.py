"""Pydantic схемы для API конструктора меню."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ButtonType(str, Enum):
    """Тип кнопки меню."""

    BUILTIN = "builtin"  # Встроенная кнопка с callback_data
    URL = "url"  # Внешняя ссылка
    MINI_APP = "mini_app"  # Telegram Mini App


class ButtonVisibility(str, Enum):
    """Видимость кнопки."""

    ALL = "all"  # Видна всем
    ADMINS = "admins"  # Только админам
    MODERATORS = "moderators"  # Только модераторам
    SUBSCRIBERS = "subscribers"  # Только подписчикам


class ButtonConditions(BaseModel):
    """Условия показа кнопки."""

    has_active_subscription: Optional[bool] = Field(
        default=None, description="Требуется активная подписка"
    )
    subscription_is_active: Optional[bool] = Field(
        default=None, description="Подписка должна быть активна (не приостановлена)"
    )
    has_traffic_limit: Optional[bool] = Field(
        default=None, description="Подписка с лимитом трафика"
    )
    is_admin: Optional[bool] = Field(default=None, description="Пользователь - админ")
    is_moderator: Optional[bool] = Field(
        default=None, description="Пользователь - модератор"
    )
    referral_enabled: Optional[bool] = Field(
        default=None, description="Реферальная программа включена"
    )
    contests_visible: Optional[bool] = Field(
        default=None, description="Конкурсы видимы"
    )
    support_enabled: Optional[bool] = Field(
        default=None, description="Поддержка включена"
    )
    language_selection_enabled: Optional[bool] = Field(
        default=None, description="Выбор языка включен"
    )
    happ_enabled: Optional[bool] = Field(
        default=None, description="Кнопка Happ включена"
    )
    simple_subscription_enabled: Optional[bool] = Field(
        default=None, description="Простая подписка включена"
    )
    show_trial: Optional[bool] = Field(
        default=None, description="Показать пробный период"
    )
    show_buy: Optional[bool] = Field(
        default=None, description="Показать кнопку покупки"
    )
    has_saved_cart: Optional[bool] = Field(
        default=None, description="Есть сохраненная корзина"
    )

    model_config = ConfigDict(extra="forbid")


class MenuButtonConfig(BaseModel):
    """Конфигурация отдельной кнопки."""

    type: ButtonType = Field(..., description="Тип кнопки")
    builtin_id: Optional[str] = Field(
        default=None, description="ID встроенной кнопки (для type=builtin)"
    )
    text: Dict[str, str] = Field(
        ..., description="Локализованные тексты кнопки: {lang_code: text}"
    )
    action: str = Field(
        ..., description="callback_data или URL в зависимости от типа"
    )
    enabled: bool = Field(default=True, description="Кнопка активна")
    visibility: ButtonVisibility = Field(
        default=ButtonVisibility.ALL, description="Видимость кнопки"
    )
    conditions: Optional[ButtonConditions] = Field(
        default=None, description="Дополнительные условия показа"
    )
    dynamic_text: bool = Field(
        default=False, description="Текст содержит плейсхолдеры ({balance} и т.д.)"
    )

    model_config = ConfigDict(extra="forbid")


class MenuRowConfig(BaseModel):
    """Конфигурация строки меню."""

    id: str = Field(..., min_length=1, max_length=50, description="Уникальный ID строки")
    buttons: List[str] = Field(
        ..., description="Список ID кнопок в строке"
    )
    conditions: Optional[ButtonConditions] = Field(
        default=None, description="Условия показа всей строки"
    )
    max_per_row: int = Field(
        default=2, ge=1, le=4, description="Максимум кнопок в строке"
    )

    model_config = ConfigDict(extra="forbid")


class MenuLayoutConfig(BaseModel):
    """Полная конфигурация меню."""

    version: int = Field(default=1, description="Версия формата конфигурации")
    rows: List[MenuRowConfig] = Field(
        default_factory=list, description="Строки меню"
    )
    buttons: Dict[str, MenuButtonConfig] = Field(
        default_factory=dict, description="Конфигурации кнопок"
    )

    model_config = ConfigDict(extra="forbid")


# --- Response schemas ---


class MenuLayoutResponse(BaseModel):
    """Ответ с конфигурацией меню."""

    version: int
    rows: List[MenuRowConfig]
    buttons: Dict[str, MenuButtonConfig]
    is_enabled: bool = Field(description="Включен ли конструктор меню")
    updated_at: Optional[datetime] = None


class BuiltinButtonInfo(BaseModel):
    """Информация о встроенной кнопке."""

    id: str = Field(description="Идентификатор кнопки")
    default_text: Dict[str, str] = Field(description="Текст по умолчанию")
    callback_data: str = Field(description="callback_data кнопки")
    default_conditions: Optional[ButtonConditions] = Field(
        default=None, description="Условия показа по умолчанию"
    )
    supports_dynamic_text: bool = Field(
        default=False, description="Поддерживает ли динамический текст"
    )


class BuiltinButtonsListResponse(BaseModel):
    """Список встроенных кнопок."""

    items: List[BuiltinButtonInfo]
    total: int


# --- Request schemas ---


class MenuLayoutUpdateRequest(BaseModel):
    """Запрос на обновление конфигурации меню."""

    rows: Optional[List[MenuRowConfig]] = Field(
        default=None, description="Новая конфигурация строк"
    )
    buttons: Optional[Dict[str, MenuButtonConfig]] = Field(
        default=None, description="Новая конфигурация кнопок"
    )

    model_config = ConfigDict(extra="forbid")


class ButtonUpdateRequest(BaseModel):
    """Запрос на обновление отдельной кнопки."""

    text: Optional[Dict[str, str]] = Field(
        default=None, description="Новые локализованные тексты"
    )
    enabled: Optional[bool] = Field(default=None, description="Включить/выключить")
    visibility: Optional[ButtonVisibility] = Field(
        default=None, description="Новая видимость"
    )
    conditions: Optional[ButtonConditions] = Field(
        default=None, description="Новые условия показа"
    )
    action: Optional[str] = Field(
        default=None, description="Новый action (для URL/MiniApp кнопок)"
    )

    model_config = ConfigDict(extra="forbid")


class RowsReorderRequest(BaseModel):
    """Запрос на изменение порядка строк."""

    ordered_ids: List[str] = Field(
        ..., min_length=1, description="Список ID строк в новом порядке"
    )

    model_config = ConfigDict(extra="forbid")


class AddRowRequest(BaseModel):
    """Запрос на добавление новой строки."""

    id: str = Field(..., min_length=1, max_length=50, description="ID новой строки")
    buttons: List[str] = Field(..., description="Список ID кнопок")
    conditions: Optional[ButtonConditions] = Field(
        default=None, description="Условия показа"
    )
    max_per_row: int = Field(default=2, ge=1, le=4, description="Макс. кнопок в строке")
    position: Optional[int] = Field(
        default=None, ge=0, description="Позиция вставки (по умолчанию - в конец)"
    )

    model_config = ConfigDict(extra="forbid")


class AddCustomButtonRequest(BaseModel):
    """Запрос на добавление кастомной кнопки."""

    id: str = Field(
        ..., min_length=1, max_length=50, description="ID кнопки (уникальный)"
    )
    type: ButtonType = Field(..., description="Тип кнопки (url или mini_app)")
    text: Dict[str, str] = Field(..., description="Локализованные тексты")
    action: str = Field(..., min_length=1, description="URL или callback_data")
    visibility: ButtonVisibility = Field(
        default=ButtonVisibility.ALL, description="Видимость"
    )
    conditions: Optional[ButtonConditions] = Field(
        default=None, description="Условия показа"
    )
    row_id: Optional[str] = Field(
        default=None, description="ID строки для добавления кнопки"
    )

    model_config = ConfigDict(extra="forbid")


class MenuPreviewRequest(BaseModel):
    """Запрос на предпросмотр меню."""

    language: str = Field(default="ru", description="Язык для предпросмотра")
    is_admin: bool = Field(default=False, description="Режим админа")
    is_moderator: bool = Field(default=False, description="Режим модератора")
    has_active_subscription: bool = Field(
        default=False, description="Есть активная подписка"
    )
    subscription_is_active: bool = Field(
        default=False, description="Подписка активна"
    )
    balance_kopeks: int = Field(default=0, ge=0, description="Баланс в копейках")

    model_config = ConfigDict(extra="forbid")


class MenuPreviewButton(BaseModel):
    """Кнопка в предпросмотре."""

    text: str
    action: str
    type: ButtonType


class MenuPreviewRow(BaseModel):
    """Строка в предпросмотре."""

    buttons: List[MenuPreviewButton]


class MenuPreviewResponse(BaseModel):
    """Ответ с предпросмотром меню."""

    rows: List[MenuPreviewRow]
    total_buttons: int


# --- Схемы для перемещения кнопок ---


class MoveButtonToRowRequest(BaseModel):
    """Запрос на перемещение кнопки в другую строку."""

    target_row_id: str = Field(..., description="ID целевой строки")
    position: Optional[int] = Field(
        default=None, ge=0, description="Позиция в строке (по умолчанию - в конец)"
    )

    model_config = ConfigDict(extra="forbid")


class ReorderButtonsInRowRequest(BaseModel):
    """Запрос на изменение порядка кнопок в строке."""

    ordered_button_ids: List[str] = Field(
        ..., min_length=1, description="Список ID кнопок в новом порядке"
    )

    model_config = ConfigDict(extra="forbid")


class SwapButtonsRequest(BaseModel):
    """Запрос на обмен местами двух кнопок."""

    button_id_1: str = Field(..., description="ID первой кнопки")
    button_id_2: str = Field(..., description="ID второй кнопки")

    model_config = ConfigDict(extra="forbid")


class MoveButtonResponse(BaseModel):
    """Ответ на перемещение кнопки."""

    button_id: str
    new_row_index: Optional[int] = None
    target_row_id: Optional[str] = None
    position: Optional[int] = None


class SwapButtonsResponse(BaseModel):
    """Ответ на обмен кнопок."""

    button_1: Dict[str, Any]
    button_2: Dict[str, Any]


class ReorderButtonsResponse(BaseModel):
    """Ответ на изменение порядка кнопок."""

    row_id: str
    buttons: List[str]
