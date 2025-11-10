"""High level integration with PayPalych API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from app.config import settings
from app.external.pal24_client import Pal24Client, Pal24APIError

logger = logging.getLogger(__name__)


class Pal24Service:
    """Wrapper around :class:`Pal24Client` providing domain helpers."""

    BILL_SUCCESS_STATES = {"SUCCESS", "OVERPAID"}
    BILL_FAILED_STATES = {"FAIL", "CANCELLED"}
    BILL_PENDING_STATES = {"NEW", "PROCESS", "UNDERPAID"}

    def __init__(self, client: Optional[Pal24Client] = None) -> None:
        self.client = client or Pal24Client()

    @property
    def is_configured(self) -> bool:
        return self.client.is_configured and settings.is_pal24_enabled()

    async def create_bill(
        self,
        *,
        amount_kopeks: int,
        user_id: int,
        order_id: str,
        description: str,
        ttl_seconds: Optional[int] = None,
        custom_payload: Optional[Dict[str, Any]] = None,
        payer_email: Optional[str] = None,
        payment_method: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.is_configured:
            raise Pal24APIError("Pal24 service is not configured")

        amount_decimal = Pal24Client.normalize_amount(amount_kopeks)
        extra_payload: Dict[str, Any] = {
            "custom": custom_payload or {},
            "ttl": ttl_seconds,
        }

        if payer_email:
            extra_payload["payer_email"] = payer_email
        if payment_method:
            extra_payload["payment_method"] = payment_method

        filtered_payload = {k: v for k, v in extra_payload.items() if v not in (None, {})}

        logger.info(
            "Создаем Pal24 счет: user_id=%s, order_id=%s, amount=%s, ttl=%s",
            user_id,
            order_id,
            amount_decimal,
            ttl_seconds,
        )

        response = await self.client.create_bill(
            amount=amount_decimal,
            shop_id=settings.PAL24_SHOP_ID,
            order_id=order_id,
            description=description,
            type_="normal",
            **filtered_payload,
        )

        logger.info("Pal24 счет создан: %s", response)
        return response

    async def get_bill_status(self, bill_id: str) -> Dict[str, Any]:
        logger.debug("Запрашиваем статус Pal24 счета %s", bill_id)
        return await self.client.get_bill_status(bill_id)

    async def get_payment_status(self, payment_id: str) -> Dict[str, Any]:
        logger.debug("Запрашиваем статус Pal24 платежа %s", payment_id)
        return await self.client.get_payment_status(payment_id)

    async def get_bill_payments(self, bill_id: str) -> Dict[str, Any]:
        """Возвращает список платежей, связанных со счетом."""

        logger.debug("Запрашиваем платежи Pal24 счёта %s", bill_id)
        return await self.client.get_bill_payments(bill_id)

    @staticmethod
    def parse_callback(payload: Dict[str, Any]) -> Dict[str, Any]:
        required_fields = ["InvId", "OutSum", "Status", "SignatureValue"]
        missing = [field for field in required_fields if field not in payload]
        if missing:
            raise Pal24APIError(f"Pal24 callback missing fields: {', '.join(missing)}")

        inv_id = str(payload["InvId"])
        out_sum = str(payload["OutSum"])
        signature = str(payload["SignatureValue"])

        if not Pal24Client.verify_signature(out_sum, inv_id, signature):
            raise Pal24APIError("Pal24 callback signature mismatch")

        logger.info(
            "Получен Pal24 callback: InvId=%s, Status=%s, TrsId=%s",
            inv_id,
            payload.get("Status"),
            payload.get("TrsId"),
        )

        return payload

    @staticmethod
    def convert_to_kopeks(amount: str) -> int:
        decimal_amount = Decimal(str(amount))
        return int((decimal_amount * Decimal("100")).quantize(Decimal("1")))

    @staticmethod
    def get_expiration(ttl_seconds: Optional[int]) -> Optional[datetime]:
        if not ttl_seconds:
            return None
        return datetime.utcnow() + timedelta(seconds=ttl_seconds)

