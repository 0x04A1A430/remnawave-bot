"""High level service for interacting with WATA payment API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)


class WataAPIError(RuntimeError):
    """Raised when the WATA API returns an error response."""


class WataService:
    """Thin wrapper around the WATA REST API used for balance top-ups."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        access_token: Optional[str] = None,
        request_timeout: Optional[int] = None,
    ) -> None:
        self.base_url = (base_url or settings.WATA_BASE_URL or "").rstrip("/")
        self.access_token = access_token or settings.WATA_ACCESS_TOKEN
        self.request_timeout = request_timeout or int(settings.WATA_REQUEST_TIMEOUT)

    @property
    def is_configured(self) -> bool:
        return bool(
            settings.is_wata_enabled()
            and self.base_url
            and self.access_token
        )

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _build_headers(self) -> Dict[str, str]:
        if not self.access_token:
            raise WataAPIError("WATA access token is not configured")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.is_configured:
            raise WataAPIError("WATA service is not configured")

        url = self._build_url(path)
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers=self._build_headers(),
                ) as response:
                    response_text = await response.text()
                    if response.status >= 400:
                        logger.error(
                            "WATA API error %s: %s", response.status, response_text
                        )
                        raise WataAPIError(
                            f"WATA API returned status {response.status}: {response_text}"
                        )

                    if not response_text:
                        return {}

                    try:
                        data = await response.json()
                    except aiohttp.ContentTypeError as error:
                        logger.error("WATA API returned non-JSON response: %s", error)
                        raise WataAPIError("WATA API returned invalid JSON") from error

                    return data
        except aiohttp.ClientError as error:
            logger.error("Error communicating with WATA API: %s", error)
            raise WataAPIError("Failed to communicate with WATA API") from error

    @staticmethod
    def _amount_from_kopeks(amount_kopeks: int) -> float:
        return round(amount_kopeks / 100, 2)

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        if value.tzinfo is None:
            aware = value.replace(tzinfo=timezone.utc)
        else:
            aware = value.astimezone(timezone.utc)
        return aware.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_datetime(raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        try:
            normalized = raw.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                return parsed
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, TypeError):
            logger.debug("Failed to parse WATA datetime: %s", raw)
            return None

    async def create_payment_link(
        self,
        *,
        amount_kopeks: int,
        currency: str,
        description: str,
        order_id: str,
        success_url: Optional[str] = None,
        fail_url: Optional[str] = None,
        link_type: Optional[str] = None,
        expiration_minutes: Optional[int] = None,
        allow_arbitrary_amount: bool = False,
        arbitrary_amount_prompts: Optional[list[int]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "amount": self._amount_from_kopeks(amount_kopeks),
            "currency": currency,
            "description": description,
            "orderId": order_id,
        }

        payload["type"] = link_type or settings.WATA_PAYMENT_TYPE or "OneTime"

        if success_url or settings.WATA_SUCCESS_REDIRECT_URL:
            payload["successRedirectUrl"] = success_url or settings.WATA_SUCCESS_REDIRECT_URL
        if fail_url or settings.WATA_FAIL_REDIRECT_URL:
            payload["failRedirectUrl"] = fail_url or settings.WATA_FAIL_REDIRECT_URL

        if expiration_minutes is None:
            ttl = settings.WATA_LINK_TTL_MINUTES
            expiration_minutes = int(ttl) if ttl is not None else None

        if expiration_minutes:
            expiration_time = datetime.utcnow() + timedelta(minutes=expiration_minutes)
            payload["expirationDateTime"] = self._format_datetime(expiration_time)

        if allow_arbitrary_amount:
            payload["isArbitraryAmountAllowed"] = True
            if arbitrary_amount_prompts:
                payload["arbitraryAmountPrompts"] = arbitrary_amount_prompts

        logger.info(
            "Создаем WATA платежную ссылку: order_id=%s, amount=%s %s",
            order_id,
            payload["amount"],
            currency,
        )

        response = await self._request("POST", "/links", json=payload)
        logger.debug("WATA create link response: %s", response)
        return response

    async def get_payment_link(self, payment_link_id: str) -> Dict[str, Any]:
        logger.debug("Запрашиваем WATA ссылку %s", payment_link_id)
        return await self._request("GET", f"/links/{payment_link_id}")

    async def search_transactions(
        self,
        *,
        order_id: Optional[str] = None,
        payment_link_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "skipCount": 0,
            "maxResultCount": max(1, min(limit, 1000)),
        }
        if order_id:
            params["orderId"] = order_id
        if status:
            params["statuses"] = status
        if payment_link_id:
            params["paymentLinkId"] = payment_link_id

        logger.debug(
            "Ищем WATA транзакции: order_id=%s, payment_link_id=%s", order_id, payment_link_id
        )
        return await self._request("GET", "/transactions", params=params)

    async def get_transaction(self, transaction_id: str) -> Dict[str, Any]:
        logger.debug("Получаем WATA транзакцию %s", transaction_id)
        return await self._request("GET", f"/transactions/{transaction_id}")
