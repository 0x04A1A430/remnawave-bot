import hashlib
import logging
from typing import Optional, Dict, Any

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)


class MulenPayService:
    """Интеграция с Mulen Pay API."""

    def __init__(self) -> None:
        self.api_key = settings.MULENPAY_API_KEY
        self.shop_id = settings.MULENPAY_SHOP_ID
        self.secret_key = settings.MULENPAY_SECRET_KEY
        self.base_url = settings.MULENPAY_BASE_URL.rstrip("/")

    @property
    def is_configured(self) -> bool:
        return bool(
            settings.is_mulenpay_enabled()
            and self.api_key
            and self.shop_id
            and self.secret_key
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            logger.error("MulenPay service is not configured")
            return None

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_data,
                    params=params,
                ) as response:
                    data = await response.json(content_type=None)

                    if response.status >= 400:
                        logger.error(
                            "MulenPay API error %s %s: %s", response.status, endpoint, data
                        )
                        return None

                    return data
        except aiohttp.ClientError as error:
            logger.error("MulenPay API request error: %s", error)
            return None
        except Exception as error:  # pragma: no cover - safety
            logger.error("Unexpected MulenPay error: %s", error, exc_info=True)
            return None

    @staticmethod
    def _format_amount(amount_kopeks: int) -> str:
        return f"{amount_kopeks / 100:.2f}"

    def _build_signature(self, currency: str, amount_str: str) -> str:
        raw_string = f"{currency}{amount_str}{self.shop_id}{self.secret_key}".encode()
        return hashlib.sha1(raw_string).hexdigest()

    async def create_payment(
        self,
        *,
        amount_kopeks: int,
        description: str,
        uuid: str,
        items: list,
        language: str = "ru",
        subscribe: Optional[str] = None,
        hold_time: Optional[int] = None,
        website_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            logger.error("MulenPay service is not configured")
            return None

        amount_str = self._format_amount(amount_kopeks)
        currency = "rub"
        payload = {
            "currency": currency,
            "amount": amount_str,
            "uuid": uuid,
            "shopId": self.shop_id,
            "description": description,
            "items": items,
            "language": language,
            "sign": self._build_signature(currency, amount_str),
        }

        if subscribe:
            payload["subscribe"] = subscribe
        if hold_time is not None:
            payload["holdTime"] = hold_time
        if website_url:
            payload["website_url"] = website_url

        response = await self._request("POST", "/v2/payments", json_data=payload)
        if not response or not response.get("success"):
            logger.error("Failed to create MulenPay payment: %s", response)
            return None

        return response

    async def get_payment(self, payment_id: int) -> Optional[Dict[str, Any]]:
        return await self._request("GET", f"/v2/payments/{payment_id}")
