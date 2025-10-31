from __future__ import annotations

import asyncio

import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

import aiohttp

from app.config import settings


logger = logging.getLogger(__name__)


_RATE_KEYS: Tuple[str, ...] = (
    "rub_per_star",
    "rubperstar",
    "price_per_star",
    "per_star_price",
    "perstarprice",
    "priceperstar",
    "per_star",
    "perstar",
    "rate",
    "exchange_rate",
    "exchangerate",
)

_STAR_KEYS: Tuple[str, ...] = (
    "stars",
    "star",
    "star_count",
    "stars_count",
    "starcount",
    "starscount",
    "starQuantity",
    "starquantity",
)

_PRICE_KEYS: Tuple[str, ...] = (
    "price",
    "amount",
    "total",
    "total_price",
    "totalPrice",
    "total_amount",
    "totalAmount",
    "value",
)


@dataclass(frozen=True, slots=True)
class _RequestSpec:
    method: str
    url: str
    params: Optional[dict[str, Any]] = None
    json_payload: Optional[dict[str, Any]] = None


class TelegramStarsRateService:
    """Получает и кэширует актуальный курс Telegram Stars."""

    _REQUESTS: Sequence[_RequestSpec] = (
        _RequestSpec(
            method="POST",
            url="https://pay.telegram.org/api/index",
            json_payload={
                "method": "getStarsExchangeRates",
                "params": {"currency": "RUB"},
            },
        ),
        _RequestSpec(
            method="GET",
            url="https://pay.telegram.org/api/index",
            params={"act": "pack", "type": "stars"},
        ),
    )

    _REQUEST_HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; RemnawaveBot/1.0)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://pay.telegram.org/",
        "Origin": "https://pay.telegram.org",
    }

    _REFRESH_INTERVAL_SECONDS = 15 * 60
    _MIN_RETRY_INTERVAL_SECONDS = 60
    _MIN_REASONABLE_RATE = 0.01
    _MAX_REASONABLE_RATE = 100.0

    def __init__(self) -> None:
        self._rate: Optional[float] = float(settings.TELEGRAM_STARS_RATE_RUB or 0)
        self._last_update: float = 0.0
        self._last_attempt: float = 0.0
        self._lock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task[Optional[float]]] = None

    def get_cached_rate(self) -> Optional[float]:
        """Возвращает закэшированный курс Stars."""

        if self._rate and self._rate >= self._MIN_REASONABLE_RATE:
            return self._rate
        return None

    def ensure_refresh(self, force: bool = False) -> None:
        """Гарантирует запуск обновления курса в фоне при необходимости."""

        if settings.TELEGRAM_STARS_CUSTOM_RATE_ENABLED:
            return

        if not force and not self._is_refresh_needed():
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(self.refresh_rate(force=force))
            except RuntimeError:
                logger.debug("Не удалось синхронно обновить курс Stars")
            return

        if self._background_task and not self._background_task.done():
            return

        self._background_task = loop.create_task(
            self.refresh_rate(force=force),
            name="telegram-stars-rate-refresh",
        )

    async def refresh_rate(self, force: bool = False) -> Optional[float]:
        """Асинхронно обновляет курс Stars."""

        if settings.TELEGRAM_STARS_CUSTOM_RATE_ENABLED:
            return float(settings.TELEGRAM_STARS_RATE_RUB)

        if not force and not self._is_refresh_needed():
            return self.get_cached_rate()

        async with self._lock:
            if not force and not self._is_refresh_needed():
                return self.get_cached_rate()

            self._last_attempt = time.monotonic()

            try:
                rate = await self._fetch_rate()
            except Exception as error:
                logger.warning("Не удалось получить курс Telegram Stars: %s", error)
                return self.get_cached_rate()

            if rate is None:
                logger.debug("API Telegram Stars не вернуло валидный курс")
                return self.get_cached_rate()

            self._rate = rate
            self._last_update = time.monotonic()
            settings.TELEGRAM_STARS_RATE_RUB = rate
            logger.info("Актуальный курс Telegram Stars обновлён: %.4f ₽/⭐", rate)
            return rate

    def _is_refresh_needed(self) -> bool:
        now = time.monotonic()

        if self._rate is None or self._rate < self._MIN_REASONABLE_RATE:
            return now - self._last_attempt >= self._MIN_RETRY_INTERVAL_SECONDS

        if now - self._last_update >= self._REFRESH_INTERVAL_SECONDS:
            return now - self._last_attempt >= self._MIN_RETRY_INTERVAL_SECONDS

        return False

    async def _fetch_rate(self) -> Optional[float]:
        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for request in self._REQUESTS:
                try:
                    response = await session.request(
                        method=request.method,
                        url=request.url,
                        headers=self._REQUEST_HEADERS,
                        params=request.params,
                        json=request.json_payload,
                    )
                except aiohttp.ClientError as error:
                    logger.debug(
                        "Ошибка запроса курса Stars (%s %s): %s",
                        request.method,
                        request.url,
                        error,
                    )
                    continue

                if response.status >= 400:
                    body = await response.text()
                    logger.debug(
                        "API Telegram Stars ответило %s: %s",
                        response.status,
                        _truncate(body, 200),
                    )
                    continue

                data = await self._read_json(response)
                if data is None:
                    continue

                rate = self._extract_rate(data)
                if rate is not None:
                    return rate

        return None

    async def _read_json(self, response: aiohttp.ClientResponse) -> Optional[Any]:
        try:
            return await response.json(content_type=None)
        except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError):
            text = await response.text()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Не удалось распарсить ответ Telegram Stars как JSON")
            return None

    @classmethod
    def _extract_rate(cls, data: Any) -> Optional[float]:
        candidates: list[Tuple[float, float]] = []
        cls._collect_rate_candidates(data, candidates)

        if not candidates:
            return None

        best_rate, _ = max(
            candidates,
            key=lambda item: (item[1], -item[0]),
        )
        return best_rate

    @classmethod
    def _collect_rate_candidates(
        cls,
        payload: Any,
        result: list[Tuple[float, float]],
    ) -> None:
        if isinstance(payload, dict):
            direct_rate = cls._parse_direct_rate(payload)
            if direct_rate is not None:
                result.append((direct_rate, math.inf))

            pack_rate = cls._parse_pack_rate(payload)
            if pack_rate is not None:
                result.append(pack_rate)

            for value in payload.values():
                cls._collect_rate_candidates(value, result)

        elif isinstance(payload, list):
            for item in payload:
                cls._collect_rate_candidates(item, result)

    @classmethod
    def _parse_direct_rate(cls, data: dict[str, Any]) -> Optional[float]:
        for key, value in data.items():
            normalized_key = key.lower()
            if normalized_key in _RATE_KEYS:
                if isinstance(value, dict):
                    price = cls._parse_price_value(value)
                    normalized_rate = cls._normalize_rate(price)
                    if normalized_rate is not None:
                        return normalized_rate
                    continue

                numeric = cls._coerce_number(value)
                normalized_rate = cls._normalize_rate(numeric)
                if normalized_rate is not None:
                    return normalized_rate

        return None

    @classmethod
    def _parse_pack_rate(cls, data: dict[str, Any]) -> Optional[Tuple[float, float]]:
        stars = cls._parse_stars_value(data)
        if stars is None or stars <= 0:
            return None

        price = cls._parse_price_value(data)
        if price is None or price <= 0:
            return None

        rate = cls._normalize_rate(price / stars)
        if rate is None:
            return None

        return rate, float(stars)

    @classmethod
    def _parse_stars_value(cls, data: dict[str, Any]) -> Optional[float]:
        for key in data.keys():
            normalized_key = key.lower()
            if normalized_key in _STAR_KEYS:
                candidate = cls._coerce_number(data[key])
                if candidate and candidate > 0:
                    return candidate
        return None

    @classmethod
    def _parse_price_value(cls, value: Any) -> Optional[float]:
        if isinstance(value, dict):
            currency = value.get("currency") or value.get("code")
            if currency and str(currency).upper() not in {"RUB", "RUR"}:
                return None

            for key in _PRICE_KEYS:
                if key in value:
                    candidate = cls._parse_price_value(value[key])
                    if candidate is not None:
                        return candidate

            return None

        if isinstance(value, list):
            for item in value:
                candidate = cls._parse_price_value(item)
                if candidate is not None:
                    return candidate
            return None

        return cls._coerce_number(value)

    @classmethod
    def _coerce_number(cls, value: Any) -> Optional[float]:
        if value is None:
            return None

        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            cleaned = value.strip().replace(" ", "").replace(" ", "")
            if not cleaned:
                return None

            match = re.search(r"-?\d+[\.,]?\d*", cleaned)
            if not match:
                return None

            normalized = match.group(0).replace(",", ".")
            try:
                return float(normalized)
            except ValueError:
                return None

        return None

    @classmethod
    def _normalize_rate(cls, value: Optional[float]) -> Optional[float]:
        if value is None or value <= 0:
            return None

        rate = float(value)
        attempts = 0
        while rate > cls._MAX_REASONABLE_RATE and attempts < 3:
            rate /= 100
            attempts += 1

        if cls._MIN_REASONABLE_RATE <= rate <= cls._MAX_REASONABLE_RATE:
            return round(rate, 4)

        return None


def _truncate(value: str, max_len: int) -> str:
    value = value.strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


telegram_stars_rate_service = TelegramStarsRateService()

