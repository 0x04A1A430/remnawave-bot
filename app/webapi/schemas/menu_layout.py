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
    CALLBACK = "callback"  # Кастомная кнопка с любым callback_data


class ButtonVisibility(str, Enum):
    """Видимость кнопки."""

    ALL = "all"  # Видна всем
    ADMINS = "admins"  # Только админам
    MODERATORS = "moderators"  # Только модераторам
    SUBSCRIBERS = "subscribers"  # Только подписчикам


class ButtonOpenMode(str, Enum):
    """Режим открытия кнопки."""

    CALLBACK = "callback"  # Отправляет callback_data боту (по умолчанию)
    DIRECT = "direct"  # Сразу открывает Mini App через WebAppInfo


class ButtonConditions(BaseModel):
    """Условия показа кнопки."""

    # Существующие условия
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

    # Расширенные условия
    min_balance_kopeks: Optional[int] = Field(
        default=None, ge=0, description="Минимальный баланс в копейках"
    )
    max_balance_kopeks: Optional[int] = Field(
        default=None, ge=0, description="Максимальный баланс в копейках"
    )
    min_registration_days: Optional[int] = Field(
        default=None, ge=0, description="Минимум дней с регистрации"
    )
    max_registration_days: Optional[int] = Field(
        default=None, ge=0, description="Максимум дней с регистрации"
    )
    min_referrals: Optional[int] = Field(
        default=None, ge=0, description="Минимальное количество рефералов"
    )
    has_referrals: Optional[bool] = Field(
        default=None, description="Есть рефералы"
    )
    promo_group_ids: Optional[List[str]] = Field(
        default=None, description="Список ID промо-групп (пользователь должен быть в одной из них)"
    )
    exclude_promo_group_ids: Optional[List[str]] = Field(
        default=None, description="Исключить пользователей из этих промо-групп"
    )
    has_subscription_days_left: Optional[int] = Field(
        default=None, ge=0, description="Минимум дней до окончания подписки"
    )
    max_subscription_days_left: Optional[int] = Field(
        default=None, ge=0, description="Максимум дней до окончания подписки"
    )
    is_trial_user: Optional[bool] = Field(
        default=None, description="Пользователь на пробном периоде"
    )
    has_autopay: Optional[bool] = Field(
        default=None, description="Автоплатёж включён"
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
    icon: Optional[str] = Field(
        default=None, max_length=10, description="Эмодзи/иконка кнопки (отдельно от текста)"
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
        default=False, description="Текст содержит плейсхолдеры ({balance}, {username} и т.д.)"
    )
    open_mode: ButtonOpenMode = Field(
        default=ButtonOpenMode.CALLBACK,
        description="Режим открытия: callback (через бота) или direct (сразу Mini App)",
    )
    webapp_url: Optional[str] = Field(
        default=None,
        description="URL для Mini App при open_mode=direct",
    )
    description: Optional[str] = Field(
        default=None, max_length=200, description="Описание кнопки для админ-панели"
    )
    sort_order: Optional[int] = Field(
        default=None, description="Порядок сортировки (для отображения в админке)"
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
    supports_direct_open: bool = Field(
        default=False, description="Поддерживает ли прямое открытие Mini App"
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
    icon: Optional[str] = Field(
        default=None, max_length=10, description="Эмодзи/иконка кнопки"
    )
    enabled: Optional[bool] = Field(default=None, description="Включить/выключить")
    visibility: Optional[ButtonVisibility] = Field(
        default=None, description="Новая видимость"
    )
    conditions: Optional[ButtonConditions] = Field(
        default=None, description="Новые условия показа"
    )
    action: Optional[str] = Field(
        default=None, description="Новый action (callback_data или URL)"
    )
    dynamic_text: Optional[bool] = Field(
        default=None, description="Текст содержит плейсхолдеры"
    )
    open_mode: Optional[ButtonOpenMode] = Field(
        default=None, description="Режим открытия: callback или direct"
    )
    webapp_url: Optional[str] = Field(
        default=None, description="URL для Mini App при open_mode=direct"
    )
    description: Optional[str] = Field(
        default=None, max_length=200, description="Описание кнопки"
    )
    sort_order: Optional[int] = Field(
        default=None, description="Порядок сортировки"
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
    type: ButtonType = Field(..., description="Тип кнопки (url, mini_app или callback)")
    text: Dict[str, str] = Field(..., description="Локализованные тексты")
    icon: Optional[str] = Field(
        default=None, max_length=10, description="Эмодзи/иконка кнопки"
    )
    action: str = Field(..., min_length=1, description="URL или callback_data")
    visibility: ButtonVisibility = Field(
        default=ButtonVisibility.ALL, description="Видимость"
    )
    conditions: Optional[ButtonConditions] = Field(
        default=None, description="Условия показа"
    )
    dynamic_text: bool = Field(
        default=False, description="Текст содержит плейсхолдеры"
    )
    row_id: Optional[str] = Field(
        default=None, description="ID строки для добавления кнопки"
    )
    description: Optional[str] = Field(
        default=None, max_length=200, description="Описание кнопки для админ-панели"
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


# --- Схемы для доступных callback_data ---


class AvailableCallback(BaseModel):
    """Информация о доступном callback_data."""

    callback_data: str = Field(description="callback_data для кнопки")
    name: str = Field(description="Человекочитаемое название")
    description: Optional[str] = Field(default=None, description="Описание действия")
    category: str = Field(description="Категория: menu, subscription, balance, referral, support, etc.")
    default_text: Optional[Dict[str, str]] = Field(default=None, description="Текст по умолчанию")
    default_icon: Optional[str] = Field(default=None, description="Иконка по умолчанию")
    requires_subscription: bool = Field(default=False, description="Требует активную подписку")
    is_in_menu: bool = Field(default=False, description="Уже добавлена в меню")


class AvailableCallbacksResponse(BaseModel):
    """Список всех доступных callback_data."""

    items: List[AvailableCallback]
    total: int
    categories: List[str] = Field(description="Список всех категорий")


# --- Схемы для импорта/экспорта ---


class MenuLayoutExportResponse(BaseModel):
    """Экспорт конфигурации меню."""

    version: int
    rows: List[MenuRowConfig]
    buttons: Dict[str, MenuButtonConfig]
    exported_at: datetime
    bot_version: Optional[str] = None


class MenuLayoutImportRequest(BaseModel):
    """Импорт конфигурации меню."""

    version: int
    rows: List[MenuRowConfig]
    buttons: Dict[str, MenuButtonConfig]
    merge_mode: str = Field(
        default="replace",
        description="Режим импорта: replace (заменить всё), merge (объединить)"
    )

    model_config = ConfigDict(extra="forbid")


class MenuLayoutImportResponse(BaseModel):
    """Результат импорта."""

    success: bool
    imported_rows: int
    imported_buttons: int
    warnings: List[str] = Field(default_factory=list)


# --- Схемы для истории изменений ---


class MenuLayoutHistoryEntry(BaseModel):
    """Запись в истории изменений."""

    id: int
    created_at: datetime
    action: str = Field(description="Тип действия: update, reset, import")
    changes_summary: str = Field(description="Краткое описание изменений")
    user_info: Optional[str] = Field(default=None, description="Информация о пользователе")


class MenuLayoutHistoryResponse(BaseModel):
    """История изменений."""

    items: List[MenuLayoutHistoryEntry]
    total: int


class MenuLayoutRollbackRequest(BaseModel):
    """Запрос на откат к предыдущей версии."""

    history_id: int = Field(description="ID записи в истории для отката")

    model_config = ConfigDict(extra="forbid")


# --- Схемы для валидации ---


class ValidationError(BaseModel):
    """Ошибка валидации."""

    field: str
    message: str
    severity: str = Field(description="error или warning")


class MenuLayoutValidateRequest(BaseModel):
    """Запрос на валидацию конфигурации."""

    rows: Optional[List[MenuRowConfig]] = None
    buttons: Optional[Dict[str, MenuButtonConfig]] = None

    model_config = ConfigDict(extra="forbid")


class MenuLayoutValidateResponse(BaseModel):
    """Результат валидации."""

    is_valid: bool
    errors: List[ValidationError] = Field(default_factory=list)
    warnings: List[ValidationError] = Field(default_factory=list)


# --- Схемы для статистики кликов ---


class ButtonClickStats(BaseModel):
    """Статистика кликов по кнопке."""

    button_id: str
    clicks_total: int = Field(default=0)
    clicks_today: int = Field(default=0)
    clicks_week: int = Field(default=0)
    clicks_month: int = Field(default=0)
    last_click_at: Optional[datetime] = None
    unique_users: int = Field(default=0, description="Уникальные пользователи")


class ButtonClickStatsResponse(BaseModel):
    """Статистика кликов для одной кнопки."""

    button_id: str
    stats: ButtonClickStats
    clicks_by_day: List[Dict[str, Any]] = Field(
        default_factory=list, description="Клики по дням [{date, count}]"
    )


class MenuClickStatsResponse(BaseModel):
    """Общая статистика кликов по всем кнопкам."""

    items: List[ButtonClickStats]
    total_clicks: int
    period_start: datetime
    period_end: datetime


class ButtonTypeStats(BaseModel):
    """Статистика по типу кнопки."""

    button_type: str
    clicks_total: int
    unique_users: int


class ButtonTypeStatsResponse(BaseModel):
    """Статистика кликов по типам кнопок."""

    items: List[ButtonTypeStats]
    total_clicks: int


class HourlyStats(BaseModel):
    """Статистика по часам."""

    hour: int
    count: int


class HourlyStatsResponse(BaseModel):
    """Статистика кликов по часам дня."""

    items: List[HourlyStats]
    button_id: Optional[str] = None


class WeekdayStats(BaseModel):
    """Статистика по дням недели."""

    weekday: int
    weekday_name: str
    count: int


class WeekdayStatsResponse(BaseModel):
    """Статистика кликов по дням недели."""

    items: List[WeekdayStats]
    button_id: Optional[str] = None


class TopUserStats(BaseModel):
    """Статистика пользователя."""

    user_id: int
    clicks_count: int
    last_click_at: Optional[datetime] = None


class TopUsersResponse(BaseModel):
    """Топ пользователей по кликам."""

    items: List[TopUserStats]
    button_id: Optional[str] = None
    limit: int


class PeriodComparisonResponse(BaseModel):
    """Сравнение периодов."""

    current_period: Dict[str, Any]
    previous_period: Dict[str, Any]
    change: Dict[str, Any]
    button_id: Optional[str] = None


class UserClickSequence(BaseModel):
    """Последовательность кликов пользователя."""

    button_id: str
    button_text: Optional[str] = None
    clicked_at: datetime


class UserClickSequencesResponse(BaseModel):
    """Последовательности кликов пользователя."""

    user_id: int
    items: List[UserClickSequence]
    total: int


# --- Схемы для плейсхолдеров ---


class DynamicPlaceholder(BaseModel):
    """Информация о динамическом плейсхолдере."""

    placeholder: str = Field(description="Плейсхолдер, например {balance}")
    description: str = Field(description="Описание")
    example: str = Field(description="Пример значения")
    category: str = Field(description="Категория: user, subscription, referral, etc.")


class DynamicPlaceholdersResponse(BaseModel):
    """Список доступных плейсхолдеров."""

    items: List[DynamicPlaceholder]
    total: int
