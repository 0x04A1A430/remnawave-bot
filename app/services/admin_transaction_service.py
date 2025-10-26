"""Utilities for aggregating and inspecting top-up payments in the admin panel."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import (
    CryptoBotPayment,
    HeleketPayment,
    MulenPayPayment,
    Pal24Payment,
    WataPayment,
    YooKassaPayment,
)
from app.services.payment_service import PaymentService


MANUAL_CHECK_SUPPORTED_METHODS = {"yookassa", "mulenpay", "pal24", "wata", "heleket"}

STATUS_EMOJI = {
    "pending": "⏳",
    "paid": "✅",
    "failed": "❌",
    "expired": "⌛",
    "unknown": "❔",
}


@dataclass(slots=True)
class AdminPaymentRecord:
    """Normalized representation of a payment across different providers."""

    method: str
    local_id: int
    external_id: Optional[str]
    status: str
    status_raw: Optional[str]
    amount_kopeks: Optional[int]
    amount_display: str
    amount_secondary: Optional[str]
    currency: Optional[str]
    description: Optional[str]
    payment_method_type: Optional[str]
    transaction_id: Optional[int]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    paid_at: Optional[datetime]
    expires_at: Optional[datetime]
    metadata: Optional[Any]
    callback_payload: Optional[Any]
    url: Optional[str]
    user: Any
    supports_manual_check: bool
    can_manual_check: bool
    manual_check_reason: Optional[str]
    raw_payment: Any

    def status_emoji(self) -> str:
        return STATUS_EMOJI.get(self.status, STATUS_EMOJI["unknown"])


class AdminTransactionService:
    """Collects payment information for the admin transactions section."""

    PER_PAGE_DEFAULT = 10
    MAX_PER_PROVIDER = 200
    MANUAL_CHECK_TIMEOUT = timedelta(hours=24)

    def __init__(self, payment_service: Optional[PaymentService] = None) -> None:
        self.payment_service = payment_service

    async def list_payments(
        self,
        db: AsyncSession,
        *,
        page: int = 1,
        per_page: int = PER_PAGE_DEFAULT,
    ) -> Dict[str, Any]:
        """Return paginated payments aggregated from all enabled providers."""

        limit = min(self.MAX_PER_PROVIDER, max(per_page * max(page, 1) * 3, per_page))
        payments: List[AdminPaymentRecord] = []
        for fetcher in (
            self._fetch_yookassa_payments,
            self._fetch_mulenpay_payments,
            self._fetch_pal24_payments,
            self._fetch_wata_payments,
            self._fetch_heleket_payments,
            self._fetch_cryptobot_payments,
        ):
            payments.extend(await fetcher(db, limit))

        payments.sort(key=lambda item: item.created_at or datetime.min, reverse=True)

        total = len(payments)
        pending_count = sum(1 for item in payments if item.status == "pending")

        start_index = max(page - 1, 0) * per_page
        end_index = start_index + per_page
        page_items = payments[start_index:end_index]

        return {
            "items": page_items,
            "total": total,
            "pending": pending_count,
            "page": page,
            "per_page": per_page,
        }

    async def get_payment_details(
        self,
        db: AsyncSession,
        method: str,
        local_id: int,
    ) -> Optional[AdminPaymentRecord]:
        """Fetch a single payment and normalize it for rendering."""

        method_normalized = method.lower()
        if method_normalized == "yookassa":
            payment = await self._fetch_single(db, YooKassaPayment, local_id)
        elif method_normalized == "mulenpay":
            payment = await self._fetch_single(db, MulenPayPayment, local_id)
        elif method_normalized == "pal24":
            payment = await self._fetch_single(db, Pal24Payment, local_id)
        elif method_normalized == "wata":
            payment = await self._fetch_single(db, WataPayment, local_id)
        elif method_normalized == "heleket":
            payment = await self._fetch_single(db, HeleketPayment, local_id)
        elif method_normalized == "cryptobot":
            payment = await self._fetch_single(db, CryptoBotPayment, local_id)
        else:
            payment = None

        if not payment:
            return None

        return self._normalize_payment(payment, method_normalized)

    async def run_manual_check(
        self,
        db: AsyncSession,
        method: str,
        local_id: int,
    ) -> Dict[str, Any]:
        """Execute provider-specific manual status refresh."""

        if not self.payment_service:
            self.payment_service = PaymentService()

        method_normalized = method.lower()

        current_state = await self.get_payment_details(db, method_normalized, local_id)
        if not current_state:
            return {"ok": False, "error": "not_found"}
        if not current_state.can_manual_check:
            reason = current_state.manual_check_reason or "not_pending"
            return {"ok": False, "error": reason}

        if method_normalized == "yookassa":
            result = await self.payment_service.sync_yookassa_payment_status(
                db, local_payment_id=local_id
            )
        elif method_normalized == "mulenpay":
            result = await self.payment_service.get_mulenpay_payment_status(
                db, local_id
            )
        elif method_normalized == "pal24":
            result = await self.payment_service.get_pal24_payment_status(
                db, local_id
            )
        elif method_normalized == "wata":
            result = await self.payment_service.get_wata_payment_status(
                db, local_id
            )
        elif method_normalized == "heleket":
            result = await self.payment_service.sync_heleket_payment_status(
                db, local_payment_id=local_id
            )
        else:
            return {"ok": False, "error": "unsupported"}

        if not result:
            return {"ok": False, "error": "not_found"}

        if isinstance(result, dict) and result.get("error"):
            return {"ok": False, "error": result.get("error")}

        return {"ok": True, "result": result}

    async def _fetch_single(
        self,
        db: AsyncSession,
        model: Any,
        local_id: int,
    ) -> Any:
        stmt: Select[Any] = (
            select(model)
            .options(selectinload(model.user))
            .where(model.id == local_id)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _fetch_yookassa_payments(
        self,
        db: AsyncSession,
        limit: int,
    ) -> Iterable[AdminPaymentRecord]:
        if not settings.is_yookassa_enabled():
            return []
        stmt = (
            select(YooKassaPayment)
            .options(selectinload(YooKassaPayment.user))
            .order_by(YooKassaPayment.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        payments = result.scalars().all()
        return [self._normalize_payment(payment, "yookassa") for payment in payments]

    async def _fetch_mulenpay_payments(
        self,
        db: AsyncSession,
        limit: int,
    ) -> Iterable[AdminPaymentRecord]:
        if not settings.is_mulenpay_enabled():
            return []
        stmt = (
            select(MulenPayPayment)
            .options(selectinload(MulenPayPayment.user))
            .order_by(MulenPayPayment.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        payments = result.scalars().all()
        return [self._normalize_payment(payment, "mulenpay") for payment in payments]

    async def _fetch_pal24_payments(
        self,
        db: AsyncSession,
        limit: int,
    ) -> Iterable[AdminPaymentRecord]:
        if not settings.is_pal24_enabled():
            return []
        stmt = (
            select(Pal24Payment)
            .options(selectinload(Pal24Payment.user))
            .order_by(Pal24Payment.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        payments = result.scalars().all()
        return [self._normalize_payment(payment, "pal24") for payment in payments]

    async def _fetch_wata_payments(
        self,
        db: AsyncSession,
        limit: int,
    ) -> Iterable[AdminPaymentRecord]:
        if not settings.is_wata_enabled():
            return []
        stmt = (
            select(WataPayment)
            .options(selectinload(WataPayment.user))
            .order_by(WataPayment.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        payments = result.scalars().all()
        return [self._normalize_payment(payment, "wata") for payment in payments]

    async def _fetch_heleket_payments(
        self,
        db: AsyncSession,
        limit: int,
    ) -> Iterable[AdminPaymentRecord]:
        if not settings.is_heleket_enabled():
            return []
        stmt = (
            select(HeleketPayment)
            .options(selectinload(HeleketPayment.user))
            .order_by(HeleketPayment.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        payments = result.scalars().all()
        return [self._normalize_payment(payment, "heleket") for payment in payments]

    async def _fetch_cryptobot_payments(
        self,
        db: AsyncSession,
        limit: int,
    ) -> Iterable[AdminPaymentRecord]:
        if not settings.is_cryptobot_enabled():
            return []
        stmt = (
            select(CryptoBotPayment)
            .options(selectinload(CryptoBotPayment.user))
            .order_by(CryptoBotPayment.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        payments = result.scalars().all()
        return [self._normalize_payment(payment, "cryptobot") for payment in payments]

    def _normalize_payment(self, payment: Any, method: str) -> AdminPaymentRecord:
        amount_kopeks = self._extract_amount_kopeks(payment)
        amount_display, amount_secondary = self._build_amount_display(payment, amount_kopeks, method)

        status = self._classify_status(method, payment)
        status_raw = getattr(payment, "status", None)

        created_at = getattr(payment, "created_at", None)
        if not created_at and hasattr(payment, "yookassa_created_at"):
            created_at = getattr(payment, "yookassa_created_at")

        supports_manual = method in MANUAL_CHECK_SUPPORTED_METHODS
        can_manual, reason = self._can_manual_check(
            method,
            status,
            created_at,
            supports_manual,
        )

        return AdminPaymentRecord(
            method=method,
            local_id=getattr(payment, "id"),
            external_id=self._extract_external_id(method, payment),
            status=status,
            status_raw=status_raw,
            amount_kopeks=amount_kopeks,
            amount_display=amount_display,
            amount_secondary=amount_secondary,
            currency=getattr(payment, "currency", getattr(payment, "asset", None)),
            description=getattr(payment, "description", None),
            payment_method_type=getattr(payment, "payment_method_type", None),
            transaction_id=getattr(payment, "transaction_id", None),
            created_at=created_at,
            updated_at=getattr(payment, "updated_at", None),
            paid_at=getattr(payment, "paid_at", None) or getattr(payment, "captured_at", None),
            expires_at=getattr(payment, "expires_at", None),
            metadata=self._safe_json(getattr(payment, "metadata_json", None)),
            callback_payload=self._safe_json(getattr(payment, "callback_payload", None)),
            url=self._extract_url(method, payment),
            user=getattr(payment, "user", None),
            supports_manual_check=supports_manual,
            can_manual_check=can_manual,
            manual_check_reason=reason,
            raw_payment=payment,
        )

    def _extract_amount_kopeks(self, payment: Any) -> Optional[int]:
        if hasattr(payment, "amount_kopeks") and payment.amount_kopeks is not None:
            try:
                return int(payment.amount_kopeks)
            except (TypeError, ValueError):
                return None
        if hasattr(payment, "amount_float"):
            try:
                return int(round(payment.amount_float * 100))
            except (TypeError, ValueError):
                return None
        if hasattr(payment, "amount"):
            try:
                return int(round(float(payment.amount) * 100))
            except (TypeError, ValueError):
                return None
        return None

    def _build_amount_display(
        self,
        payment: Any,
        amount_kopeks: Optional[int],
        method: str,
    ) -> Tuple[str, Optional[str]]:
        if amount_kopeks is not None:
            primary = settings.format_price(amount_kopeks)
        else:
            primary = str(getattr(payment, "amount", "—"))

        secondary: Optional[str] = None
        if method == "cryptobot":
            amount = getattr(payment, "amount", None)
            asset = getattr(payment, "asset", None)
            if amount and asset:
                secondary = f"{amount} {asset}"
        elif method == "heleket":
            payer_amount = getattr(payment, "payer_amount", None)
            payer_currency = getattr(payment, "payer_currency", None)
            if payer_amount:
                secondary = f"{payer_amount} {payer_currency or ''}".strip()

        return primary, secondary

    def _extract_external_id(self, method: str, payment: Any) -> Optional[str]:
        if method == "yookassa":
            return getattr(payment, "yookassa_payment_id", None)
        if method == "mulenpay":
            return str(getattr(payment, "mulen_payment_id", None) or getattr(payment, "uuid", None) or "") or None
        if method == "pal24":
            return getattr(payment, "bill_id", None)
        if method == "wata":
            return getattr(payment, "payment_link_id", None)
        if method == "heleket":
            return getattr(payment, "uuid", None)
        if method == "cryptobot":
            return getattr(payment, "invoice_id", None)
        return None

    def _extract_url(self, method: str, payment: Any) -> Optional[str]:
        if method == "yookassa":
            return getattr(payment, "confirmation_url", None)
        if method == "mulenpay":
            return getattr(payment, "payment_url", None)
        if method == "pal24":
            return getattr(payment, "link_url", None) or getattr(payment, "link_page_url", None)
        if method == "wata":
            return getattr(payment, "url", None)
        if method == "heleket":
            return getattr(payment, "payment_url", None)
        if method == "cryptobot":
            return (
                getattr(payment, "web_app_invoice_url", None)
                or getattr(payment, "mini_app_invoice_url", None)
                or getattr(payment, "bot_invoice_url", None)
            )
        return None

    def _classify_status(self, method: str, payment: Any) -> str:
        status_raw = (getattr(payment, "status", None) or "").lower()

        if method == "yookassa":
            if status_raw in {"pending", "waiting_for_capture"} and not getattr(payment, "is_paid", False):
                return "pending"
            if status_raw == "succeeded" or getattr(payment, "is_paid", False):
                return "paid"
            if status_raw in {"canceled", "cancelled", "refunded", "failed"}:
                return "failed"
            return "unknown"

        if method == "mulenpay":
            if status_raw in {"created", "processing", "hold"} and not getattr(payment, "is_paid", False):
                return "pending"
            if status_raw == "success" or getattr(payment, "is_paid", False):
                return "paid"
            if status_raw in {"canceled", "cancelled", "error"}:
                return "failed"
            return "unknown"

        if method == "pal24":
            status_upper = status_raw.upper()
            if status_upper in {"NEW", "PROCESS"} and not getattr(payment, "is_paid", False):
                return "pending"
            if status_upper in {"SUCCESS"} or getattr(payment, "is_paid", False):
                return "paid"
            if status_upper in {"EXPIRED"}:
                return "expired"
            if status_upper in {"FAIL", "FAILED", "CANCELED", "CANCELLED"}:
                return "failed"
            return "unknown"

        if method == "wata":
            status_lower = status_raw.lower()
            if status_lower in {"opened", "pending", "processing", "new"} and not getattr(payment, "is_paid", False):
                return "pending"
            if status_lower in {"paid", "closed"} or getattr(payment, "is_paid", False):
                return "paid"
            if status_lower in {"expired"}:
                return "expired"
            if status_lower in {"canceled", "cancelled", "declined", "failed"}:
                return "failed"
            return "unknown"

        if method == "heleket":
            status_lower = status_raw.lower()
            if status_lower in {"paid", "paid_over", "success"} or getattr(payment, "is_paid", False):
                return "paid"
            if status_lower in {"cancelled", "canceled", "failed", "error"}:
                return "failed"
            if status_lower in {"expired"}:
                return "expired"
            return "pending"

        if method == "cryptobot":
            if status_raw == "paid":
                return "paid"
            if status_raw == "expired":
                return "expired"
            if status_raw == "active":
                return "pending"
            return "unknown"

        return "unknown"

    def _can_manual_check(
        self,
        method: str,
        status: str,
        created_at: Optional[datetime],
        supported: bool,
    ) -> Tuple[bool, Optional[str]]:
        if not supported:
            return False, "unsupported"
        if status != "pending":
            return False, "not_pending"
        if created_at is None:
            return True, None

        if datetime.utcnow() - created_at > self.MANUAL_CHECK_TIMEOUT:
            return False, "too_old"

        return True, None

    def _safe_json(self, payload: Any) -> Any:
        if payload is None:
            return None
        if isinstance(payload, (dict, list)):
            return payload
        try:
            return json.loads(payload)
        except Exception:
            return payload
