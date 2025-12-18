"""Сервис конструктора меню - управление конфигурацией через API."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import SystemSetting
from app.database.crud.system_setting import upsert_system_setting
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)

# Ключ для хранения конфигурации в SystemSetting
MENU_LAYOUT_CONFIG_KEY = "menu_layout_config"


@dataclass
class MenuContext:
    """Контекст пользователя для построения меню."""

    language: str = "ru"
    is_admin: bool = False
    is_moderator: bool = False
    has_active_subscription: bool = False
    subscription_is_active: bool = False
    has_had_paid_subscription: bool = False
    balance_kopeks: int = 0
    subscription: Optional[Any] = None
    show_resume_checkout: bool = False
    has_saved_cart: bool = False
    custom_buttons: List[InlineKeyboardButton] = field(default_factory=list)


# Дефолтная конфигурация меню
DEFAULT_MENU_CONFIG: Dict[str, Any] = {
    "version": 1,
    "rows": [
        {
            "id": "connect_row",
            "buttons": ["connect"],
            "conditions": {"has_active_subscription": True, "subscription_is_active": True},
            "max_per_row": 1,
        },
        {
            "id": "happ_row",
            "buttons": ["happ_download"],
            "conditions": {"has_active_subscription": True, "happ_enabled": True},
            "max_per_row": 1,
        },
        {
            "id": "subscription_traffic_row",
            "buttons": ["subscription", "buy_traffic"],
            "conditions": {"has_active_subscription": True},
            "max_per_row": 2,
        },
        {
            "id": "balance_row",
            "buttons": ["balance"],
            "conditions": None,
            "max_per_row": 1,
        },
        {
            "id": "trial_buy_row",
            "buttons": ["trial", "buy_subscription"],
            "conditions": None,
            "max_per_row": 2,
        },
        {
            "id": "simple_subscription_row",
            "buttons": ["simple_subscription"],
            "conditions": {"simple_subscription_enabled": True},
            "max_per_row": 1,
        },
        {
            "id": "resume_row",
            "buttons": ["resume_checkout"],
            "conditions": {"has_saved_cart": True},
            "max_per_row": 1,
        },
        {
            "id": "promo_referral_row",
            "buttons": ["promocode", "referrals"],
            "conditions": None,
            "max_per_row": 2,
        },
        {
            "id": "contests_row",
            "buttons": ["contests"],
            "conditions": {"contests_visible": True},
            "max_per_row": 2,
        },
        {
            "id": "support_info_row",
            "buttons": ["support", "info"],
            "conditions": None,
            "max_per_row": 2,
        },
        {
            "id": "language_row",
            "buttons": ["language"],
            "conditions": {"language_selection_enabled": True},
            "max_per_row": 2,
        },
        {
            "id": "admin_row",
            "buttons": ["admin_panel"],
            "conditions": {"is_admin": True},
            "max_per_row": 1,
        },
        {
            "id": "moderator_row",
            "buttons": ["moderator_panel"],
            "conditions": {"is_moderator": True},
            "max_per_row": 1,
        },
    ],
    "buttons": {
        "connect": {
            "type": "builtin",
            "builtin_id": "connect",
            "text": {"ru": "🔗 Подключиться", "en": "🔗 Connect"},
            "action": "subscription_connect",
            "enabled": True,
            "visibility": "subscribers",
            "conditions": None,
            "dynamic_text": False,
        },
        "happ_download": {
            "type": "builtin",
            "builtin_id": "happ_download",
            "text": {"ru": "⬇️ Скачать Happ", "en": "⬇️ Download Happ"},
            "action": "subscription_happ_download",
            "enabled": True,
            "visibility": "subscribers",
            "conditions": None,
            "dynamic_text": False,
        },
        "subscription": {
            "type": "builtin",
            "builtin_id": "subscription",
            "text": {"ru": "📊 Подписка", "en": "📊 Subscription"},
            "action": "menu_subscription",
            "enabled": True,
            "visibility": "subscribers",
            "conditions": None,
            "dynamic_text": False,
        },
        "buy_traffic": {
            "type": "builtin",
            "builtin_id": "buy_traffic",
            "text": {"ru": "📈 Докупить трафик", "en": "📈 Buy traffic"},
            "action": "buy_traffic",
            "enabled": True,
            "visibility": "subscribers",
            "conditions": {"has_traffic_limit": True},
            "dynamic_text": False,
        },
        "balance": {
            "type": "builtin",
            "builtin_id": "balance",
            "text": {"ru": "💰 Баланс: {balance}", "en": "💰 Balance: {balance}"},
            "action": "menu_balance",
            "enabled": True,
            "visibility": "all",
            "conditions": None,
            "dynamic_text": True,
        },
        "trial": {
            "type": "builtin",
            "builtin_id": "trial",
            "text": {"ru": "🎁 Пробный период", "en": "🎁 Free trial"},
            "action": "menu_trial",
            "enabled": True,
            "visibility": "all",
            "conditions": {"show_trial": True},
            "dynamic_text": False,
        },
        "buy_subscription": {
            "type": "builtin",
            "builtin_id": "buy_subscription",
            "text": {"ru": "🛒 Купить подписку", "en": "🛒 Buy subscription"},
            "action": "menu_buy",
            "enabled": True,
            "visibility": "all",
            "conditions": {"show_buy": True},
            "dynamic_text": False,
        },
        "simple_subscription": {
            "type": "builtin",
            "builtin_id": "simple_subscription",
            "text": {"ru": "💳 Простая подписка", "en": "💳 Simple subscription"},
            "action": "simple_subscription_purchase",
            "enabled": True,
            "visibility": "all",
            "conditions": None,
            "dynamic_text": False,
        },
        "resume_checkout": {
            "type": "builtin",
            "builtin_id": "resume_checkout",
            "text": {"ru": "↩️ Вернуться к оформлению", "en": "↩️ Resume checkout"},
            "action": "return_to_saved_cart",
            "enabled": True,
            "visibility": "all",
            "conditions": None,
            "dynamic_text": False,
        },
        "promocode": {
            "type": "builtin",
            "builtin_id": "promocode",
            "text": {"ru": "🎟️ Промокод", "en": "🎟️ Promo code"},
            "action": "menu_promocode",
            "enabled": True,
            "visibility": "all",
            "conditions": None,
            "dynamic_text": False,
        },
        "referrals": {
            "type": "builtin",
            "builtin_id": "referrals",
            "text": {"ru": "👥 Рефералы", "en": "👥 Referrals"},
            "action": "menu_referrals",
            "enabled": True,
            "visibility": "all",
            "conditions": {"referral_enabled": True},
            "dynamic_text": False,
        },
        "contests": {
            "type": "builtin",
            "builtin_id": "contests",
            "text": {"ru": "🎲 Конкурсы", "en": "🎲 Contests"},
            "action": "contests_menu",
            "enabled": True,
            "visibility": "all",
            "conditions": None,
            "dynamic_text": False,
        },
        "support": {
            "type": "builtin",
            "builtin_id": "support",
            "text": {"ru": "💬 Поддержка", "en": "💬 Support"},
            "action": "menu_support",
            "enabled": True,
            "visibility": "all",
            "conditions": {"support_enabled": True},
            "dynamic_text": False,
        },
        "info": {
            "type": "builtin",
            "builtin_id": "info",
            "text": {"ru": "ℹ️ Инфо", "en": "ℹ️ Info"},
            "action": "menu_info",
            "enabled": True,
            "visibility": "all",
            "conditions": None,
            "dynamic_text": False,
        },
        "language": {
            "type": "builtin",
            "builtin_id": "language",
            "text": {"ru": "🌐 Язык", "en": "🌐 Language"},
            "action": "menu_language",
            "enabled": True,
            "visibility": "all",
            "conditions": None,
            "dynamic_text": False,
        },
        "admin_panel": {
            "type": "builtin",
            "builtin_id": "admin_panel",
            "text": {"ru": "⚙️ Админ панель", "en": "⚙️ Admin panel"},
            "action": "admin_panel",
            "enabled": True,
            "visibility": "admins",
            "conditions": None,
            "dynamic_text": False,
        },
        "moderator_panel": {
            "type": "builtin",
            "builtin_id": "moderator_panel",
            "text": {"ru": "🧑‍⚖️ Модерация", "en": "🧑‍⚖️ Moderation"},
            "action": "moderator_panel",
            "enabled": True,
            "visibility": "moderators",
            "conditions": None,
            "dynamic_text": False,
        },
    },
}


# Информация о встроенных кнопках для API
BUILTIN_BUTTONS_INFO = [
    {
        "id": "connect",
        "default_text": {"ru": "🔗 Подключиться", "en": "🔗 Connect"},
        "callback_data": "subscription_connect",
        "default_conditions": {"has_active_subscription": True, "subscription_is_active": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "happ_download",
        "default_text": {"ru": "⬇️ Скачать Happ", "en": "⬇️ Download Happ"},
        "callback_data": "subscription_happ_download",
        "default_conditions": {"happ_enabled": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "subscription",
        "default_text": {"ru": "📊 Подписка", "en": "📊 Subscription"},
        "callback_data": "menu_subscription",
        "default_conditions": {"has_active_subscription": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "buy_traffic",
        "default_text": {"ru": "📈 Докупить трафик", "en": "📈 Buy traffic"},
        "callback_data": "buy_traffic",
        "default_conditions": {"has_traffic_limit": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "balance",
        "default_text": {"ru": "💰 Баланс: {balance}", "en": "💰 Balance: {balance}"},
        "callback_data": "menu_balance",
        "default_conditions": None,
        "supports_dynamic_text": True,
    },
    {
        "id": "trial",
        "default_text": {"ru": "🎁 Пробный период", "en": "🎁 Free trial"},
        "callback_data": "menu_trial",
        "default_conditions": {"show_trial": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "buy_subscription",
        "default_text": {"ru": "🛒 Купить подписку", "en": "🛒 Buy subscription"},
        "callback_data": "menu_buy",
        "default_conditions": {"show_buy": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "simple_subscription",
        "default_text": {"ru": "💳 Простая подписка", "en": "💳 Simple subscription"},
        "callback_data": "simple_subscription_purchase",
        "default_conditions": {"simple_subscription_enabled": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "resume_checkout",
        "default_text": {"ru": "↩️ Вернуться к оформлению", "en": "↩️ Resume checkout"},
        "callback_data": "return_to_saved_cart",
        "default_conditions": {"has_saved_cart": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "promocode",
        "default_text": {"ru": "🎟️ Промокод", "en": "🎟️ Promo code"},
        "callback_data": "menu_promocode",
        "default_conditions": None,
        "supports_dynamic_text": False,
    },
    {
        "id": "referrals",
        "default_text": {"ru": "👥 Рефералы", "en": "👥 Referrals"},
        "callback_data": "menu_referrals",
        "default_conditions": {"referral_enabled": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "contests",
        "default_text": {"ru": "🎲 Конкурсы", "en": "🎲 Contests"},
        "callback_data": "contests_menu",
        "default_conditions": {"contests_visible": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "support",
        "default_text": {"ru": "💬 Поддержка", "en": "💬 Support"},
        "callback_data": "menu_support",
        "default_conditions": {"support_enabled": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "info",
        "default_text": {"ru": "ℹ️ Инфо", "en": "ℹ️ Info"},
        "callback_data": "menu_info",
        "default_conditions": None,
        "supports_dynamic_text": False,
    },
    {
        "id": "language",
        "default_text": {"ru": "🌐 Язык", "en": "🌐 Language"},
        "callback_data": "menu_language",
        "default_conditions": {"language_selection_enabled": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "admin_panel",
        "default_text": {"ru": "⚙️ Админ панель", "en": "⚙️ Admin panel"},
        "callback_data": "admin_panel",
        "default_conditions": {"is_admin": True},
        "supports_dynamic_text": False,
    },
    {
        "id": "moderator_panel",
        "default_text": {"ru": "🧑‍⚖️ Модерация", "en": "🧑‍⚖️ Moderation"},
        "callback_data": "moderator_panel",
        "default_conditions": {"is_moderator": True},
        "supports_dynamic_text": False,
    },
]


class MenuLayoutService:
    """Сервис для управления конфигурацией меню."""

    _cache: Optional[Dict[str, Any]] = None
    _cache_updated_at: Optional[datetime] = None
    _lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    def invalidate_cache(cls) -> None:
        """Инвалидировать кеш конфигурации."""
        cls._cache = None
        cls._cache_updated_at = None

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Получить дефолтную конфигурацию."""
        return DEFAULT_MENU_CONFIG.copy()

    @classmethod
    def get_builtin_buttons_info(cls) -> List[Dict[str, Any]]:
        """Получить информацию о встроенных кнопках."""
        return BUILTIN_BUTTONS_INFO.copy()

    @classmethod
    async def get_config(cls, db: AsyncSession) -> Dict[str, Any]:
        """Получить конфигурацию меню."""
        if cls._cache is not None:
            return cls._cache

        async with cls._lock:
            if cls._cache is not None:
                return cls._cache

            result = await db.execute(
                select(SystemSetting).where(SystemSetting.key == MENU_LAYOUT_CONFIG_KEY)
            )
            setting = result.scalar_one_or_none()

            if setting and setting.value:
                try:
                    cls._cache = json.loads(setting.value)
                    cls._cache_updated_at = setting.updated_at
                except json.JSONDecodeError:
                    logger.warning("Invalid menu layout config JSON, using default")
                    cls._cache = cls.get_default_config()
                    cls._cache_updated_at = None
            else:
                cls._cache = cls.get_default_config()
                cls._cache_updated_at = None

            return cls._cache

    @classmethod
    async def get_config_updated_at(cls, db: AsyncSession) -> Optional[datetime]:
        """Получить время последнего обновления конфигурации."""
        await cls.get_config(db)  # Ensure cache is loaded
        return cls._cache_updated_at

    @classmethod
    async def save_config(cls, db: AsyncSession, config: Dict[str, Any]) -> None:
        """Сохранить конфигурацию меню."""
        config_json = json.dumps(config, ensure_ascii=False, indent=2)
        await upsert_system_setting(
            db,
            MENU_LAYOUT_CONFIG_KEY,
            config_json,
            description="Конфигурация конструктора меню",
        )
        await db.commit()
        cls.invalidate_cache()

    @classmethod
    async def reset_to_default(cls, db: AsyncSession) -> Dict[str, Any]:
        """Сбросить конфигурацию к дефолтной."""
        default_config = cls.get_default_config()
        await cls.save_config(db, default_config)
        return default_config

    @classmethod
    async def update_button(
        cls,
        db: AsyncSession,
        button_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Обновить конфигурацию кнопки."""
        config = await cls.get_config(db)
        config = config.copy()
        buttons = config.get("buttons", {})

        if button_id not in buttons:
            raise KeyError(f"Button '{button_id}' not found")

        button = buttons[button_id].copy()

        # Применяем обновления
        if "text" in updates and updates["text"] is not None:
            button["text"] = updates["text"]
        if "enabled" in updates and updates["enabled"] is not None:
            button["enabled"] = updates["enabled"]
        if "visibility" in updates and updates["visibility"] is not None:
            button["visibility"] = updates["visibility"]
        if "conditions" in updates:
            button["conditions"] = updates["conditions"]
        if "action" in updates and updates["action"] is not None:
            # Только для URL/MiniApp кнопок
            if button.get("type") in ("url", "mini_app"):
                button["action"] = updates["action"]

        buttons[button_id] = button
        config["buttons"] = buttons

        await cls.save_config(db, config)
        return button

    @classmethod
    async def reorder_rows(
        cls,
        db: AsyncSession,
        ordered_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """Изменить порядок строк."""
        config = await cls.get_config(db)
        config = config.copy()
        rows = config.get("rows", [])

        # Создаем словарь для быстрого поиска
        rows_map = {row["id"]: row for row in rows}

        # Проверяем что все ID существуют
        for row_id in ordered_ids:
            if row_id not in rows_map:
                raise KeyError(f"Row '{row_id}' not found")

        # Переупорядочиваем
        new_rows = [rows_map[row_id] for row_id in ordered_ids]

        # Добавляем строки которые не были в списке (в конец)
        for row in rows:
            if row["id"] not in ordered_ids:
                new_rows.append(row)

        config["rows"] = new_rows
        await cls.save_config(db, config)
        return new_rows

    @classmethod
    async def add_row(
        cls,
        db: AsyncSession,
        row_config: Dict[str, Any],
        position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Добавить новую строку."""
        config = await cls.get_config(db)
        config = config.copy()
        rows = config.get("rows", [])

        # Проверяем уникальность ID
        existing_ids = {row["id"] for row in rows}
        if row_config["id"] in existing_ids:
            raise ValueError(f"Row with id '{row_config['id']}' already exists")

        new_row = {
            "id": row_config["id"],
            "buttons": row_config.get("buttons", []),
            "conditions": row_config.get("conditions"),
            "max_per_row": row_config.get("max_per_row", 2),
        }

        if position is not None and 0 <= position < len(rows):
            rows.insert(position, new_row)
        else:
            rows.append(new_row)

        config["rows"] = rows
        await cls.save_config(db, config)
        return new_row

    @classmethod
    async def delete_row(cls, db: AsyncSession, row_id: str) -> None:
        """Удалить строку."""
        config = await cls.get_config(db)
        config = config.copy()
        rows = config.get("rows", [])

        new_rows = [row for row in rows if row["id"] != row_id]
        if len(new_rows) == len(rows):
            raise KeyError(f"Row '{row_id}' not found")

        config["rows"] = new_rows
        await cls.save_config(db, config)

    @classmethod
    async def add_custom_button(
        cls,
        db: AsyncSession,
        button_id: str,
        button_config: Dict[str, Any],
        row_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Добавить кастомную кнопку."""
        config = await cls.get_config(db)
        config = config.copy()
        buttons = config.get("buttons", {})

        if button_id in buttons:
            raise ValueError(f"Button with id '{button_id}' already exists")

        # Только URL и MiniApp кнопки могут быть добавлены
        if button_config.get("type") not in ("url", "mini_app"):
            raise ValueError("Only 'url' and 'mini_app' buttons can be added")

        buttons[button_id] = {
            "type": button_config["type"],
            "builtin_id": None,
            "text": button_config["text"],
            "action": button_config["action"],
            "enabled": button_config.get("enabled", True),
            "visibility": button_config.get("visibility", "all"),
            "conditions": button_config.get("conditions"),
            "dynamic_text": False,
        }

        config["buttons"] = buttons

        # Добавляем в строку если указана
        if row_id:
            rows = config.get("rows", [])
            for row in rows:
                if row["id"] == row_id:
                    row["buttons"].append(button_id)
                    break

        await cls.save_config(db, config)
        return buttons[button_id]

    @classmethod
    async def delete_custom_button(cls, db: AsyncSession, button_id: str) -> None:
        """Удалить кастомную кнопку."""
        config = await cls.get_config(db)
        config = config.copy()
        buttons = config.get("buttons", {})

        if button_id not in buttons:
            raise KeyError(f"Button '{button_id}' not found")

        # Нельзя удалять встроенные кнопки
        if buttons[button_id].get("type") == "builtin":
            raise ValueError("Cannot delete builtin buttons")

        del buttons[button_id]
        config["buttons"] = buttons

        # Удаляем из всех строк
        rows = config.get("rows", [])
        for row in rows:
            if button_id in row.get("buttons", []):
                row["buttons"].remove(button_id)

        await cls.save_config(db, config)

    @classmethod
    def _find_button_row(cls, config: Dict[str, Any], button_id: str) -> Optional[int]:
        """Найти индекс строки содержащей кнопку."""
        rows = config.get("rows", [])
        for i, row in enumerate(rows):
            if button_id in row.get("buttons", []):
                return i
        return None

    @classmethod
    async def move_button_up(cls, db: AsyncSession, button_id: str) -> Dict[str, Any]:
        """Переместить кнопку на строку выше."""
        config = await cls.get_config(db)
        config = config.copy()
        rows = config.get("rows", [])

        current_row_idx = cls._find_button_row(config, button_id)
        if current_row_idx is None:
            raise KeyError(f"Button '{button_id}' not found in any row")

        if current_row_idx == 0:
            raise ValueError("Button is already in the top row")

        # Удаляем из текущей строки
        rows[current_row_idx]["buttons"].remove(button_id)

        # Добавляем в строку выше
        rows[current_row_idx - 1]["buttons"].append(button_id)

        # Удаляем пустые строки
        config["rows"] = [row for row in rows if row.get("buttons")]

        await cls.save_config(db, config)
        return {"button_id": button_id, "new_row_index": current_row_idx - 1}

    @classmethod
    async def move_button_down(cls, db: AsyncSession, button_id: str) -> Dict[str, Any]:
        """Переместить кнопку на строку ниже."""
        config = await cls.get_config(db)
        config = config.copy()
        rows = config.get("rows", [])

        current_row_idx = cls._find_button_row(config, button_id)
        if current_row_idx is None:
            raise KeyError(f"Button '{button_id}' not found in any row")

        if current_row_idx >= len(rows) - 1:
            raise ValueError("Button is already in the bottom row")

        # Удаляем из текущей строки
        rows[current_row_idx]["buttons"].remove(button_id)

        # Добавляем в строку ниже
        rows[current_row_idx + 1]["buttons"].append(button_id)

        # Удаляем пустые строки
        config["rows"] = [row for row in rows if row.get("buttons")]

        await cls.save_config(db, config)
        return {"button_id": button_id, "new_row_index": current_row_idx + 1}

    @classmethod
    async def move_button_to_row(
        cls,
        db: AsyncSession,
        button_id: str,
        target_row_id: str,
        position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Переместить кнопку в указанную строку."""
        config = await cls.get_config(db)
        config = config.copy()
        rows = config.get("rows", [])

        # Находим целевую строку
        target_row_idx = None
        for i, row in enumerate(rows):
            if row["id"] == target_row_id:
                target_row_idx = i
                break

        if target_row_idx is None:
            raise KeyError(f"Row '{target_row_id}' not found")

        # Удаляем кнопку из текущей строки
        current_row_idx = cls._find_button_row(config, button_id)
        if current_row_idx is not None:
            rows[current_row_idx]["buttons"].remove(button_id)

        # Добавляем в целевую строку
        target_buttons = rows[target_row_idx]["buttons"]
        if position is not None and 0 <= position <= len(target_buttons):
            target_buttons.insert(position, button_id)
        else:
            target_buttons.append(button_id)

        # Удаляем пустые строки
        config["rows"] = [row for row in rows if row.get("buttons")]

        await cls.save_config(db, config)
        return {"button_id": button_id, "target_row_id": target_row_id, "position": position}

    @classmethod
    async def reorder_buttons_in_row(
        cls,
        db: AsyncSession,
        row_id: str,
        ordered_button_ids: List[str],
    ) -> Dict[str, Any]:
        """Изменить порядок кнопок внутри строки."""
        config = await cls.get_config(db)
        config = config.copy()
        rows = config.get("rows", [])

        # Находим строку
        target_row = None
        for row in rows:
            if row["id"] == row_id:
                target_row = row
                break

        if target_row is None:
            raise KeyError(f"Row '{row_id}' not found")

        # Проверяем что все кнопки принадлежат строке
        current_buttons = set(target_row["buttons"])
        ordered_buttons = set(ordered_button_ids)

        if current_buttons != ordered_buttons:
            missing = current_buttons - ordered_buttons
            extra = ordered_buttons - current_buttons
            errors = []
            if missing:
                errors.append(f"missing: {missing}")
            if extra:
                errors.append(f"extra: {extra}")
            raise ValueError(f"Button mismatch: {', '.join(errors)}")

        target_row["buttons"] = ordered_button_ids

        await cls.save_config(db, config)
        return {"row_id": row_id, "buttons": ordered_button_ids}

    @classmethod
    async def swap_buttons(
        cls,
        db: AsyncSession,
        button_id_1: str,
        button_id_2: str,
    ) -> Dict[str, Any]:
        """Поменять две кнопки местами."""
        config = await cls.get_config(db)
        config = config.copy()
        rows = config.get("rows", [])

        # Находим позиции обеих кнопок
        pos1 = None
        pos2 = None

        for row_idx, row in enumerate(rows):
            buttons = row.get("buttons", [])
            for btn_idx, btn_id in enumerate(buttons):
                if btn_id == button_id_1:
                    pos1 = (row_idx, btn_idx)
                elif btn_id == button_id_2:
                    pos2 = (row_idx, btn_idx)

        if pos1 is None:
            raise KeyError(f"Button '{button_id_1}' not found")
        if pos2 is None:
            raise KeyError(f"Button '{button_id_2}' not found")

        # Меняем местами
        rows[pos1[0]]["buttons"][pos1[1]] = button_id_2
        rows[pos2[0]]["buttons"][pos2[1]] = button_id_1

        await cls.save_config(db, config)
        return {
            "button_1": {"id": button_id_1, "new_row": pos2[0], "new_position": pos2[1]},
            "button_2": {"id": button_id_2, "new_row": pos1[0], "new_position": pos1[1]},
        }

    @classmethod
    def _evaluate_conditions(
        cls,
        conditions: Optional[Dict[str, Any]],
        context: MenuContext,
    ) -> bool:
        """Проверить условия показа."""
        if not conditions:
            return True

        # has_active_subscription
        if conditions.get("has_active_subscription") is True:
            if not context.has_active_subscription:
                return False

        # subscription_is_active
        if conditions.get("subscription_is_active") is True:
            if not context.subscription_is_active:
                return False

        # has_traffic_limit - подписка с лимитом трафика
        if conditions.get("has_traffic_limit") is True:
            if not context.subscription:
                return False
            traffic_limit = getattr(context.subscription, "traffic_limit_gb", 0)
            is_trial = getattr(context.subscription, "is_trial", False)
            if is_trial or traffic_limit <= 0:
                return False

        # is_admin
        if conditions.get("is_admin") is True:
            if not context.is_admin:
                return False

        # is_moderator
        if conditions.get("is_moderator") is True:
            if context.is_admin:  # Админ не показывает кнопку модератора
                return False
            if not context.is_moderator:
                return False

        # referral_enabled
        if conditions.get("referral_enabled") is True:
            if not settings.is_referral_program_enabled():
                return False

        # contests_visible
        if conditions.get("contests_visible") is True:
            if not settings.CONTESTS_BUTTON_VISIBLE:
                return False

        # support_enabled
        if conditions.get("support_enabled") is True:
            try:
                from app.services.support_settings_service import SupportSettingsService
                if not SupportSettingsService.is_support_menu_enabled():
                    return False
            except Exception:
                if not settings.SUPPORT_MENU_ENABLED:
                    return False

        # language_selection_enabled
        if conditions.get("language_selection_enabled") is True:
            if not settings.is_language_selection_enabled():
                return False

        # happ_enabled
        if conditions.get("happ_enabled") is True:
            if not settings.is_happ_download_button_enabled():
                return False

        # simple_subscription_enabled
        if conditions.get("simple_subscription_enabled") is True:
            if not settings.SIMPLE_SUBSCRIPTION_ENABLED:
                return False

        # show_trial
        if conditions.get("show_trial") is True:
            if context.has_had_paid_subscription or context.has_active_subscription:
                return False

        # show_buy
        if conditions.get("show_buy") is True:
            if context.has_active_subscription and context.subscription_is_active:
                return False

        # has_saved_cart
        if conditions.get("has_saved_cart") is True:
            if not context.has_saved_cart and not context.show_resume_checkout:
                return False

        return True

    @classmethod
    def _check_visibility(
        cls,
        visibility: str,
        context: MenuContext,
    ) -> bool:
        """Проверить видимость кнопки."""
        if visibility == "all":
            return True
        if visibility == "admins":
            return context.is_admin
        if visibility == "moderators":
            return context.is_moderator and not context.is_admin
        if visibility == "subscribers":
            return context.has_active_subscription and context.subscription_is_active
        return True

    @classmethod
    def _get_localized_text(
        cls,
        text_config: Dict[str, str],
        language: str,
        fallback_language: str = "en",
    ) -> str:
        """Получить локализованный текст."""
        # Пробуем запрошенный язык
        if language in text_config:
            return text_config[language]
        # Пробуем fallback
        if fallback_language in text_config:
            return text_config[fallback_language]
        # Возвращаем первый доступный
        if text_config:
            return next(iter(text_config.values()))
        return ""

    @classmethod
    def _format_dynamic_text(
        cls,
        text: str,
        context: MenuContext,
        texts: Any,
    ) -> str:
        """Форматировать динамический текст."""
        if "{balance}" in text:
            formatted_balance = texts.format_price(context.balance_kopeks)
            text = text.replace("{balance}", formatted_balance)
        return text

    @classmethod
    def _build_button(
        cls,
        button_config: Dict[str, Any],
        context: MenuContext,
        texts: Any,
    ) -> Optional[InlineKeyboardButton]:
        """Построить кнопку из конфигурации."""
        button_type = button_config.get("type", "builtin")
        text_config = button_config.get("text", {})
        action = button_config.get("action", "")

        # Получаем текст
        text = cls._get_localized_text(text_config, context.language)
        if not text:
            return None

        # Форматируем динамический текст
        if button_config.get("dynamic_text"):
            text = cls._format_dynamic_text(text, context, texts)

        # Строим кнопку в зависимости от типа
        if button_type == "url":
            return InlineKeyboardButton(text=text, url=action)
        elif button_type == "mini_app":
            return InlineKeyboardButton(
                text=text, web_app=types.WebAppInfo(url=action)
            )
        else:
            # builtin - callback_data
            return InlineKeyboardButton(text=text, callback_data=action)

    @classmethod
    async def build_keyboard(
        cls,
        db: AsyncSession,
        context: MenuContext,
    ) -> InlineKeyboardMarkup:
        """Построить клавиатуру меню на основе конфигурации."""
        config = await cls.get_config(db)
        texts = get_texts(context.language)

        keyboard_rows: List[List[InlineKeyboardButton]] = []
        rows_config = config.get("rows", [])
        buttons_config = config.get("buttons", {})

        for row_config in rows_config:
            # Проверяем условия строки
            row_conditions = row_config.get("conditions")
            if not cls._evaluate_conditions(row_conditions, context):
                continue

            row_buttons: List[InlineKeyboardButton] = []
            max_per_row = row_config.get("max_per_row", 2)

            for button_id in row_config.get("buttons", []):
                if button_id not in buttons_config:
                    continue

                button_cfg = buttons_config[button_id]

                # Проверяем включена ли кнопка
                if not button_cfg.get("enabled", True):
                    continue

                # Проверяем видимость
                visibility = button_cfg.get("visibility", "all")
                if not cls._check_visibility(visibility, context):
                    continue

                # Проверяем условия кнопки
                button_conditions = button_cfg.get("conditions")
                if not cls._evaluate_conditions(button_conditions, context):
                    continue

                # Строим кнопку
                button = cls._build_button(button_cfg, context, texts)
                if button:
                    row_buttons.append(button)

            # Добавляем кнопки с учетом max_per_row
            if row_buttons:
                for i in range(0, len(row_buttons), max_per_row):
                    keyboard_rows.append(row_buttons[i : i + max_per_row])

        return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    @classmethod
    async def preview_keyboard(
        cls,
        db: AsyncSession,
        context: MenuContext,
    ) -> List[Dict[str, Any]]:
        """Предпросмотр меню (возвращает структуру для API)."""
        config = await cls.get_config(db)
        texts = get_texts(context.language)

        preview_rows: List[Dict[str, Any]] = []
        rows_config = config.get("rows", [])
        buttons_config = config.get("buttons", {})

        for row_config in rows_config:
            row_conditions = row_config.get("conditions")
            if not cls._evaluate_conditions(row_conditions, context):
                continue

            row_buttons: List[Dict[str, Any]] = []
            max_per_row = row_config.get("max_per_row", 2)

            for button_id in row_config.get("buttons", []):
                if button_id not in buttons_config:
                    continue

                button_cfg = buttons_config[button_id]

                if not button_cfg.get("enabled", True):
                    continue

                visibility = button_cfg.get("visibility", "all")
                if not cls._check_visibility(visibility, context):
                    continue

                button_conditions = button_cfg.get("conditions")
                if not cls._evaluate_conditions(button_conditions, context):
                    continue

                text_config = button_cfg.get("text", {})
                text = cls._get_localized_text(text_config, context.language)

                if button_cfg.get("dynamic_text"):
                    text = cls._format_dynamic_text(text, context, texts)

                row_buttons.append({
                    "text": text,
                    "action": button_cfg.get("action", ""),
                    "type": button_cfg.get("type", "builtin"),
                })

            if row_buttons:
                for i in range(0, len(row_buttons), max_per_row):
                    preview_rows.append({"buttons": row_buttons[i : i + max_per_row]})

        return preview_rows
