"""Интеграция с API Wata Pay."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Dict, Optional

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import settings

logger = logging.getLogger(__name__)


class WataService:
    """Обёртка над REST API Wata Pay."""

    def __init__(self) -> None:
        self.base_url = (settings.WATA_BASE_URL or "https://api.wata.pro/api/h2h").rstrip("/")
        self.access_token = settings.WATA_ACCESS_TOKEN
        self._public_key_cache: Optional[tuple[str, float]] = None
        self._public_key_lock = asyncio.Lock()

    @property
    def is_configured(self) -> bool:
        return bool(settings.is_wata_enabled() and self.access_token)

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            logger.error("Wata service is not configured")
            return None

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=settings.WATA_TIMEOUT_SECONDS or 60)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_data,
                    params=params,
                ) as response:
                    text = await response.text()
                    if not text:
                        data: Dict[str, Any] = {}
                    else:
                        try:
                            data = await response.json(content_type=None)
                        except Exception:
                            logger.error(
                                "Wata API вернул не-JSON ответ %s: %s", response.status, text
                            )
                            return None

                    if response.status >= 400:
                        logger.error(
                            "Wata API error %s %s: %s", response.status, endpoint, data
                        )
                        return None

                    return data
        except aiohttp.ClientError as error:
            logger.error("Wata API request error: %s", error)
            return None
        except Exception as error:  # pragma: no cover - непредвиденные ошибки сети
            logger.error("Unexpected Wata error: %s", error, exc_info=True)
            return None

    async def create_payment_link(self, **payload: Any) -> Optional[Dict[str, Any]]:
        return await self._request("POST", "/links", json_data=payload)

    async def get_payment_link(self, link_id: str) -> Optional[Dict[str, Any]]:
        return await self._request("GET", f"/links/{link_id}")

    async def search_transactions(
        self,
        *,
        order_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if order_id:
            params["orderId"] = order_id
        return await self._request("GET", "/transactions/", params=params or None)

    async def get_public_key(self, *, force: bool = False) -> Optional[str]:
        if not force and self._public_key_cache:
            value, expires_at = self._public_key_cache
            if time.time() < expires_at:
                return value

        async with self._public_key_lock:
            if not force and self._public_key_cache:
                value, expires_at = self._public_key_cache
                if time.time() < expires_at:
                    return value

            response = await self._request("GET", "/public-key")
            if not response:
                return None

            value = response.get("value")
            if not isinstance(value, str):
                logger.error("Некорректный публичный ключ Wata: %s", response)
                return None

            self._public_key_cache = (value, time.time() + 3600)
            return value

    async def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        if not signature:
            logger.error("Отсутствует подпись Wata webhook")
            return False

        public_key_pem = await self.get_public_key()
        if not public_key_pem:
            logger.error("Не удалось получить публичный ключ Wata")
            return False

        try:
            public_key = serialization.load_pem_public_key(public_key_pem.encode())
            signature_bytes = base64.b64decode(signature)
            public_key.verify(
                signature_bytes,
                raw_body,
                padding.PKCS1v15(),
                hashes.SHA512(),
            )
            return True
        except Exception as error:  # pragma: no cover - безопасность
            logger.error("Ошибка проверки подписи Wata: %s", error)
            return False
