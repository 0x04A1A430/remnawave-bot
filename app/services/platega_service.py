"""HTTP-интеграция с Platega API."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)


class PlategaService:
    """Обертка над Platega API с базовой повторной отправкой запросов."""

    def __init__(self) -> None:
        self.base_url = (settings.PLATEGA_BASE_URL or "https://app.platega.io").rstrip("/")
        self.merchant_id = settings.PLATEGA_MERCHANT_ID
        self.secret = settings.PLATEGA_SECRET
        self._timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=25)
        self._max_retries = 3
        self._retry_delay = 0.5
        self._retryable_statuses = {500, 502, 503, 504}
        self._description_max_length = 64

    @property
    def is_configured(self) -> bool:
        return settings.is_platega_enabled()

    async def create_payment(
        self,
        *,
        payment_method: int,
        amount: float,
        currency: str,
        description: Optional[str] = None,
        return_url: Optional[str] = None,
        failed_url: Optional[str] = None,
        payload: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        body: Dict[str, Any] = {
            "paymentMethod": payment_method,
            "paymentDetails": {
                "amount": round(amount, 2),
                "currency": currency,
            },
        }

        if description:
            sanitized_description = self._sanitize_description(
                description, self._description_max_length
            )
            body["description"] = sanitized_description
        if return_url:
            body["return"] = return_url
        if failed_url:
            body["failedUrl"] = failed_url
        if payload:
            body["payload"] = payload

        return await self._request("POST", "/transaction/process", json_data=body)

    async def get_transaction(self, transaction_id: str) -> Optional[Dict[str, Any]]:
        endpoint = f"/transaction/{transaction_id}"
        return await self._request("GET", endpoint)

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            logger.error("Platega service is not configured")
            return None

        url = f"{self.base_url}{endpoint}"
        headers = {
            "X-MerchantId": self.merchant_id or "",
            "X-Secret": self.secret or "",
            "Content-Type": "application/json",
        }

        last_error: Optional[BaseException] = None

        for attempt in range(1, self._max_retries + 1):
            try:
                async with aiohttp.ClientSession(timeout=self._timeout) as session:
                    async with session.request(
                        method,
                        url,
                        json=json_data,
                        params=params,
                        headers=headers,
                    ) as response:
                        data, raw_text = await self._deserialize_response(response)

                        if response.status >= 400:
                            logger.error(
                                "Platega API error %s %s: %s",
                                response.status,
                                endpoint,
                                raw_text,
                            )
                            if (
                                response.status in self._retryable_statuses
                                and attempt < self._max_retries
                            ):
                                await asyncio.sleep(self._retry_delay * attempt)
                                continue
                            return None

                        return data
            except asyncio.CancelledError:
                logger.debug("Platega request cancelled: %s %s", method, endpoint)
                raise
            except asyncio.TimeoutError as error:
                last_error = error
                logger.warning(
                    "Platega request timeout (%s %s) attempt %s/%s",
                    method,
                    endpoint,
                    attempt,
                    self._max_retries,
                )
            except aiohttp.ClientError as error:
                last_error = error
                logger.warning(
                    "Platega client error (%s %s) attempt %s/%s: %s",
                    method,
                    endpoint,
                    attempt,
                    self._max_retries,
                    error,
                )
            except Exception as error:  # pragma: no cover - safety
                logger.exception("Unexpected Platega error: %s", error)
                return None

            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_delay * attempt)

        if last_error is not None:
            logger.error(
                "Platega request failed after %s attempts (%s %s): %s",
                self._max_retries,
                method,
                endpoint,
                last_error,
            )

        return None

    @staticmethod
    async def _deserialize_response(
        response: aiohttp.ClientResponse,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        raw_text = await response.text()
        if not raw_text:
            return None, ""

        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type.lower() or not content_type:
            try:
                return json.loads(raw_text), raw_text
            except json.JSONDecodeError as error:
                logger.error(
                    "Failed to decode Platega JSON response %s: %s",
                    response.url,
                    error,
                )
                return None, raw_text

        return None, raw_text

    @staticmethod
    def _sanitize_description(description: str, max_bytes: int) -> str:
        """Обрезает описание с учётом байтового лимита Platega."""

        cleaned = (description or "").strip()
        if not max_bytes:
            return cleaned

        encoded = cleaned.encode("utf-8")
        if len(encoded) <= max_bytes:
            return cleaned

        logger.debug(
            "Platega description trimmed from %s to %s bytes",
            len(encoded),
            max_bytes,
        )

        trimmed_bytes = encoded[:max_bytes]
        while True:
            try:
                return trimmed_bytes.decode("utf-8")
            except UnicodeDecodeError:
                trimmed_bytes = trimmed_bytes[:-1]


    @staticmethod
    def parse_expires_at(expires_in: Optional[str]) -> Optional[datetime]:
        if not expires_in:
            return None

        try:
            hours, minutes, seconds = [int(part) for part in expires_in.split(":", 2)]
            delta = timedelta(hours=hours, minutes=minutes, seconds=seconds)
            return datetime.utcnow() + delta
        except Exception:
            logger.warning("Failed to parse Platega expiresIn value: %s", expires_in)
            return None
