"""HTTP client for Heleket payment API."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)


class HeleketService:
    """Minimal wrapper around Heleket API endpoints."""

    def __init__(self) -> None:
        self.base_url = settings.HELEKET_BASE_URL.rstrip("/")
        self.merchant_id = settings.HELEKET_MERCHANT_ID
        self.api_key = settings.HELEKET_API_KEY

    @property
    def is_configured(self) -> bool:
        return bool(self.merchant_id and self.api_key)

    def _prepare_body(
        self,
        payload: Dict[str, Any],
        *,
        ignore_none: bool,
        sort_keys: bool,
    ) -> str:
        if ignore_none:
            cleaned = {key: value for key, value in payload.items() if value is not None}
        else:
            cleaned = dict(payload)

        serialized = json.dumps(
            cleaned,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=sort_keys,
        )

        if "/" in serialized:
            serialized = serialized.replace("/", "\\/")

        return serialized

    def _generate_signature(self, body: str) -> str:
        api_key = self.api_key or ""
        encoded = base64.b64encode(body.encode("utf-8")).decode("utf-8")
        raw = f"{encoded}{api_key}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    async def _request(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            logger.error("Heleket сервис не настроен: merchant или api_key отсутствуют")
            return None

        body = self._prepare_body(payload, ignore_none=True, sort_keys=True)
        signature = self._generate_signature(body)

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "merchant": self.merchant_id or "",
            "sign": signature,
            "Content-Type": "application/json",
        }

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    data=body.encode("utf-8"),
                    headers=headers,
                    params=params,
                ) as response:
                    text = await response.text()
                    if response.content_type != "application/json":
                        logger.error("Ответ Heleket не JSON (%s): %s", response.content_type, text)
                        return None

                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        logger.error("Ошибка парсинга Heleket JSON: %s", text)
                        return None

                    if response.status >= 400:
                        logger.error("Heleket API %s вернул статус %s: %s", endpoint, response.status, data)
                        return None

                    if isinstance(data, dict) and data.get("state") == 0:
                        return data

                    logger.error("Heleket API вернул ошибку: %s", data)
                    return None
        except Exception as error:  # pragma: no cover - defensive
            logger.error("Ошибка запроса к Heleket API: %s", error)
            return None

    async def create_payment(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return await self._request("payment", payload)

    async def get_payment_info(
        self,
        *,
        uuid: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not uuid and not order_id:
            raise ValueError("Нужно указать uuid или order_id для Heleket payment/info")

        payload: Dict[str, Any] = {}
        if uuid:
            payload["uuid"] = uuid
        if order_id:
            payload["order_id"] = order_id

        return await self._request("payment/info", payload)

    async def list_payments(
        self,
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        if date_from:
            payload["date_from"] = date_from
        if date_to:
            payload["date_to"] = date_to

        params = {"cursor": cursor} if cursor else None
        return await self._request("payment/list", payload, params=params)

    def verify_webhook_signature(self, payload: Dict[str, Any]) -> bool:
        if not self.is_configured:
            logger.warning("Heleket сервис не настроен, подпись пропускается")
            return True

        if not isinstance(payload, dict):
            logger.error("Heleket webhook payload не dict: %s", payload)
            return False

        signature = payload.get("sign")
        if not signature:
            logger.error("Heleket webhook без подписи")
            return False

        data = dict(payload)
        data.pop("sign", None)
        body = self._prepare_body(data, ignore_none=False, sort_keys=False)
        expected = self._generate_signature(body)

        is_valid = hmac.compare_digest(expected, str(signature))

        if not is_valid:
            logger.error("Неверная подпись Heleket webhook: ожидается %s, получено %s", expected, signature)
        return is_valid
