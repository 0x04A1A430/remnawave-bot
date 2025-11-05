from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from app.config import settings
from app.localization.loader import (
    DEFAULT_LANGUAGE,
    clear_locale_cache,
    load_locale,
)

_logger = logging.getLogger(__name__)

_cached_rules: Dict[str, str] = {}


def _get_cached_rules_value(language: str) -> str:
    if language in _cached_rules:
        return _cached_rules[language]

    default = _get_default_rules(language)
    _cached_rules[language] = default
    return default


def _build_dynamic_values(language: str) -> Dict[str, Any]:
    language_code = (language or DEFAULT_LANGUAGE).split("-")[0].lower()

    if language_code == "ru":
        return {
            "TRAFFIC_5GB": f"📊 5 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_5GB)}",
            "TRAFFIC_10GB": f"📊 10 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_10GB)}",
            "TRAFFIC_25GB": f"📊 25 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_25GB)}",
            "TRAFFIC_50GB": f"📊 50 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_50GB)}",
            "TRAFFIC_100GB": f"📊 100 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_100GB)}",
            "TRAFFIC_250GB": f"📊 250 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_250GB)}",
            "TRAFFIC_UNLIMITED": f"📊 Безлимит - {settings.format_price(settings.PRICE_TRAFFIC_UNLIMITED)}",
            "SUPPORT_INFO": (
                "\n🛟 <b>Поддержка</b>\n\n"
                "Это центр тикетов: создавайте обращения, просматривайте ответы и историю.\n\n"
                "• 🎫 Создать тикет — опишите проблему или вопрос\n"
                "• 📋 Мои тикеты — статус и переписка\n"
                "• 💬 Связаться — написать напрямую (если нужно)\n\n"
                "Старайтесь использовать тикеты — так мы быстрее поможем и ничего не потеряется.\n"
            ),
        }

    if language_code == "en":
        return {
            "TRAFFIC_5GB": f"📊 5 GB - {settings.format_price(settings.PRICE_TRAFFIC_5GB)}",
            "TRAFFIC_10GB": f"📊 10 GB - {settings.format_price(settings.PRICE_TRAFFIC_10GB)}",
            "TRAFFIC_25GB": f"📊 25 GB - {settings.format_price(settings.PRICE_TRAFFIC_25GB)}",
            "TRAFFIC_50GB": f"📊 50 GB - {settings.format_price(settings.PRICE_TRAFFIC_50GB)}",
            "TRAFFIC_100GB": f"📊 100 GB - {settings.format_price(settings.PRICE_TRAFFIC_100GB)}",
            "TRAFFIC_250GB": f"📊 250 GB - {settings.format_price(settings.PRICE_TRAFFIC_250GB)}",
            "TRAFFIC_UNLIMITED": f"📊 Unlimited - {settings.format_price(settings.PRICE_TRAFFIC_UNLIMITED)}",
            "SUPPORT_INFO": (
                "\n🛟 <b>RemnaWave Support</b>\n\n"
                "This is the ticket center: create requests, view replies and history.\n\n"
                "• 🎫 Create ticket — describe your issue or question\n"
                "• 📋 My tickets — status and conversation\n"
                "• 💬 Contact — message directly if needed\n\n"
                "Prefer tickets — it helps us respond faster and keep context.\n"
            ),
        }

    return {}


class Texts:
    def __init__(self, language: str = DEFAULT_LANGUAGE):
        self.language = language or DEFAULT_LANGUAGE
        raw_data = load_locale(self.language)
        self._values = {key: value for key, value in raw_data.items()}

        if self.language != DEFAULT_LANGUAGE:
            fallback_data = load_locale(DEFAULT_LANGUAGE)
        else:
            fallback_data = self._values

        self._fallback_values = {
            key: value for key, value in fallback_data.items() if key not in self._values
        }

        self._values.update(_build_dynamic_values(self.language))

    def __getattr__(self, item: str) -> Any:
        if item == "language":
            return super().__getattribute__(item)
        try:
            return self._get_value(item)
        except KeyError as error:
            raise AttributeError(item) from error

    def __getitem__(self, item: str) -> Any:
        return self._get_value(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self._get_value(item)
        except KeyError:
            return default

    def t(self, key: str, default: Any = None) -> Any:
        try:
            return self._get_value(key)
        except KeyError:
            if default is not None:
                return default
            raise

    def _get_value(self, item: str) -> Any:
        if item == "RULES_TEXT":
            return _get_cached_rules_value(self.language)

        if item in self._values:
            return self._values[item]

        if item in self._fallback_values:
            return self._fallback_values[item]

        _logger.warning(
            "Missing localization key '%s' for language '%s'",
            item,
            self.language,
        )
        raise KeyError(item)

    @staticmethod
    def format_price(kopeks: int) -> str:
        return settings.format_price(kopeks)

    @staticmethod
    def format_traffic(gb: float) -> str:
        if gb == 0:
            return "∞ (безлимит)"
        if gb >= 1024:
            return f"{gb / 1024:.1f} ТБ"
        return f"{gb:.0f} ГБ"


def get_texts(language: str = DEFAULT_LANGUAGE) -> Texts:
    return Texts(language)


async def get_rules_from_db(language: str = DEFAULT_LANGUAGE) -> str:
    try:
        from app.database.database import get_db
        from app.database.crud.rules import get_current_rules_content

        async for db in get_db():
            rules = await get_current_rules_content(db, language)
            if rules:
                _cached_rules[language] = rules
                return rules
            break

    except Exception as error:  # pragma: no cover - defensive logging
        _logger.warning("Failed to load rules from DB for %s: %s", language, error)

    default = _get_default_rules(language)
    _cached_rules[language] = default
    return default


def _get_default_rules(language: str = DEFAULT_LANGUAGE) -> str:
    default_key = "RULES_TEXT_DEFAULT"
    locale = load_locale(language)
    if default_key in locale:
        return locale[default_key]
    fallback = load_locale(DEFAULT_LANGUAGE)
    return fallback.get(default_key, "")


def get_rules_sync(language: str = DEFAULT_LANGUAGE) -> str:
    if language in _cached_rules:
        return _cached_rules[language]

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(get_rules(language))

    loop.create_task(get_rules(language))
    return _get_cached_rules_value(language)


async def get_rules(language: str = DEFAULT_LANGUAGE) -> str:
    if language in _cached_rules:
        return _cached_rules[language]

    return await get_rules_from_db(language)


async def refresh_rules_cache(language: str = DEFAULT_LANGUAGE) -> None:
    if language in _cached_rules:
        del _cached_rules[language]
    await get_rules_from_db(language)


def clear_rules_cache() -> None:
    _cached_rules.clear()


def reload_locales() -> None:
    clear_locale_cache()
