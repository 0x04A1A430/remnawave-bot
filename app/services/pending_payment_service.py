"""Utilities for aggregating and manually checking pending top-up payments."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PaymentMethod
from app.database.crud import (
    cryptobot as cryptobot_crud,
    heleket as heleket_crud,
    mulenpay as mulenpay_crud,
    pal24 as pal24_crud,
    wata as wata_crud,
    yookassa as yookassa_crud,
)
from app.services.payment_service import PaymentService

logger = logging.getLogger(__name__)


class PendingPaymentError(Exception):
    """Base error for pending payment operations."""


class PendingPaymentNotFoundError(PendingPaymentError):
    """Raised when a pending payment cannot be located."""


class PendingPaymentTooOldError(PendingPaymentError):
    """Raised when a payment is older than the allowed interval."""


class PendingPaymentNotPendingError(PendingPaymentError):
    """Raised when a payment is no longer in a pending state."""


class PendingPaymentUnsupportedError(PendingPaymentError):
    """Raised when manual verification is not implemented for a provider."""


@dataclass
class _PendingPaymentEntry:
    provider: PaymentMethod
    payment: Any


class PendingPaymentService:
    """Aggregates pending payments and provides manual verification helpers."""

    SUPPORTED_METHODS: tuple[PaymentMethod, ...] = (
        PaymentMethod.YOOKASSA,
        PaymentMethod.MULENPAY,
        PaymentMethod.PAL24,
        PaymentMethod.WATA,
        PaymentMethod.HELEKET,
    )

    def __init__(self, bot: Any | None = None) -> None:
        self._payment_service = PaymentService(bot)

    async def list_pending_payments(
        self,
        db: AsyncSession,
        *,
        max_age_hours: int = 24,
        provider: PaymentMethod | None = None,
    ) -> list[dict[str, Any]]:
        entries = await self._collect_pending_entries(
            db,
            max_age_hours=max_age_hours,
            provider=provider,
        )

        serialised = [
            self._serialise(entry.provider, entry.payment)
            for entry in entries
        ]

        serialised.sort(
            key=lambda item: item.get("created_at") or datetime.min,
            reverse=True,
        )

        return serialised

    async def get_payment(
        self,
        db: AsyncSession,
        provider: PaymentMethod,
        payment_id: int,
    ) -> dict[str, Any]:
        payment = await self._fetch_payment(db, provider, payment_id)

        if payment is None:
            raise PendingPaymentNotFoundError

        return self._serialise(provider, payment, include_details=True)

    async def run_manual_check(
        self,
        db: AsyncSession,
        provider: PaymentMethod,
        payment_id: int,
        *,
        max_age_hours: int = 24,
    ) -> dict[str, Any]:
        payment = await self._fetch_payment(db, provider, payment_id)

        if payment is None:
            raise PendingPaymentNotFoundError

        if not self._is_pending(provider, payment):
            raise PendingPaymentNotPendingError

        if not self._is_within_age(payment, max_age_hours):
            raise PendingPaymentTooOldError

        status_before = getattr(payment, "status", None)
        performed = await self._perform_manual_check(db, provider, payment_id)

        refreshed = await self._fetch_payment(db, provider, payment_id)
        if refreshed is None:
            raise PendingPaymentNotFoundError

        payload = self._serialise(provider, refreshed, include_details=True)

        payload.update(
            {
                "check_performed": performed,
                "status_before": status_before,
                "status_after": payload.get("status"),
                "completed": bool(payload.get("is_paid"))
                or bool(payload.get("transaction_id")),
            }
        )

        return payload

    async def run_bulk_check(
        self,
        db: AsyncSession,
        *,
        max_age_hours: int = 24,
        provider: PaymentMethod | None = None,
    ) -> dict[str, Any]:
        entries = await self._collect_pending_entries(
            db,
            max_age_hours=max_age_hours,
            provider=provider,
        )

        results: list[dict[str, Any]] = []
        checked = 0
        completed = 0
        skipped = 0

        for entry in entries:
            try:
                result = await self.run_manual_check(
                    db,
                    entry.provider,
                    entry.payment.id,
                    max_age_hours=max_age_hours,
                )
            except PendingPaymentUnsupportedError as error:
                skipped += 1
                logger.warning(
                    "Manual check is not supported for %s: %s",
                    entry.provider.value,
                    error,
                )
                continue
            except PendingPaymentError as error:
                skipped += 1
                logger.warning(
                    "Skipping payment %s/%s due to %s",
                    entry.provider.value,
                    entry.payment.id,
                    error,
                )
                continue

            checked += 1
            if result.get("completed"):
                completed += 1

            results.append(result)

        return {
            "total": len(entries),
            "checked": checked,
            "completed": completed,
            "skipped": skipped,
            "results": results,
        }

    async def _collect_pending_entries(
        self,
        db: AsyncSession,
        *,
        max_age_hours: int,
        provider: PaymentMethod | None,
    ) -> list[_PendingPaymentEntry]:
        providers: Iterable[PaymentMethod]
        if provider is None:
            providers = self.SUPPORTED_METHODS
        else:
            providers = (provider,)

        entries: list[_PendingPaymentEntry] = []

        for method in providers:
            payments = await self._fetch_pending_for_provider(
                db,
                method,
                max_age_hours=max_age_hours,
            )
            for payment in payments:
                if not self._is_pending(method, payment):
                    continue
                if not self._is_within_age(payment, max_age_hours):
                    continue
                entries.append(_PendingPaymentEntry(method, payment))

        return entries

    async def _fetch_pending_for_provider(
        self,
        db: AsyncSession,
        method: PaymentMethod,
        *,
        max_age_hours: int,
    ) -> list[Any]:
        if method == PaymentMethod.YOOKASSA:
            return await yookassa_crud.get_pending_yookassa_payments(
                db,
                max_age_hours=max_age_hours,
            )
        if method == PaymentMethod.MULENPAY:
            return await mulenpay_crud.get_pending_mulenpay_payments(
                db,
                max_age_hours=max_age_hours,
            )
        if method == PaymentMethod.PAL24:
            return await pal24_crud.get_pending_pal24_payments(
                db,
                max_age_hours=max_age_hours,
            )
        if method == PaymentMethod.WATA:
            return await wata_crud.get_pending_wata_payments(
                db,
                max_age_hours=max_age_hours,
            )
        if method == PaymentMethod.HELEKET:
            return await heleket_crud.get_pending_heleket_payments(
                db,
                max_age_hours=max_age_hours,
            )

        return []

    async def _fetch_payment(
        self,
        db: AsyncSession,
        provider: PaymentMethod,
        payment_id: int,
    ) -> Any | None:
        if provider == PaymentMethod.YOOKASSA:
            return await yookassa_crud.get_yookassa_payment_by_local_id(db, payment_id)
        if provider == PaymentMethod.MULENPAY:
            return await mulenpay_crud.get_mulenpay_payment_by_local_id(db, payment_id)
        if provider == PaymentMethod.PAL24:
            return await pal24_crud.get_pal24_payment_by_id(db, payment_id)
        if provider == PaymentMethod.WATA:
            return await wata_crud.get_wata_payment_by_id(db, payment_id)
        if provider == PaymentMethod.HELEKET:
            return await heleket_crud.get_heleket_payment_by_id(db, payment_id)

        if provider == PaymentMethod.CRYPTOBOT:
            return await cryptobot_crud.get_cryptobot_payment_by_id(db, payment_id)

        return None

    async def _perform_manual_check(
        self,
        db: AsyncSession,
        provider: PaymentMethod,
        payment_id: int,
    ) -> bool:
        if provider == PaymentMethod.YOOKASSA:
            result = await self._payment_service.get_yookassa_payment_status(
                db,
                payment_id,
            )
            return bool(result)
        if provider == PaymentMethod.MULENPAY:
            result = await self._payment_service.get_mulenpay_payment_status(
                db,
                payment_id,
            )
            return bool(result)
        if provider == PaymentMethod.PAL24:
            result = await self._payment_service.get_pal24_payment_status(
                db,
                payment_id,
            )
            return bool(result)
        if provider == PaymentMethod.WATA:
            result = await self._payment_service.get_wata_payment_status(
                db,
                payment_id,
            )
            return bool(result)
        if provider == PaymentMethod.HELEKET:
            result = await self._payment_service.sync_heleket_payment_status(
                db,
                local_payment_id=payment_id,
            )
            return bool(result)

        raise PendingPaymentUnsupportedError(
            f"Manual status check is not implemented for {provider.value}"
        )

    def _serialise(
        self,
        provider: PaymentMethod,
        payment: Any,
        *,
        include_details: bool = False,
    ) -> dict[str, Any]:
        amount_kopeks = int(getattr(payment, "amount_kopeks", 0) or 0)
        currency = getattr(payment, "currency", None) or "RUB"

        data: dict[str, Any] = {
            "id": getattr(payment, "id", None),
            "provider": provider.value,
            "user": self._extract_user(payment),
            "amount_kopeks": amount_kopeks,
            "amount_rubles": round(amount_kopeks / 100, 2),
            "currency": currency,
            "status": getattr(payment, "status", None),
            "is_paid": bool(getattr(payment, "is_paid", False)),
            "description": getattr(payment, "description", None),
            "payment_url": self._get_payment_url(provider, payment),
            "external_id": self._get_external_id(provider, payment),
            "transaction_id": getattr(payment, "transaction_id", None),
            "created_at": getattr(payment, "created_at", None),
            "updated_at": getattr(payment, "updated_at", None),
            "expires_at": getattr(payment, "expires_at", None),
            "is_pending": self._is_pending(provider, payment),
        }

        if include_details:
            data["metadata"] = self._collect_metadata(provider, payment)

        return data

    def _collect_metadata(self, provider: PaymentMethod, payment: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {}

        base_metadata = getattr(payment, "metadata_json", None)
        if base_metadata:
            metadata["metadata_json"] = base_metadata

        callback_payload = getattr(payment, "callback_payload", None)
        if callback_payload:
            metadata["callback_payload"] = callback_payload

        if provider == PaymentMethod.YOOKASSA:
            metadata.update(
                {
                    "confirmation_url": getattr(payment, "confirmation_url", None),
                    "payment_method_type": getattr(payment, "payment_method_type", None),
                    "refundable": getattr(payment, "refundable", None),
                    "test_mode": getattr(payment, "test_mode", None),
                }
            )
        elif provider == PaymentMethod.MULENPAY:
            metadata.update(
                {
                    "mulen_payment_id": getattr(payment, "mulen_payment_id", None),
                }
            )
        elif provider == PaymentMethod.PAL24:
            metadata.update(
                {
                    "payment_id": getattr(payment, "payment_id", None),
                    "payment_status": getattr(payment, "payment_status", None),
                    "payment_method": getattr(payment, "payment_method", None),
                    "balance_amount": getattr(payment, "balance_amount", None),
                    "balance_currency": getattr(payment, "balance_currency", None),
                    "payer_account": getattr(payment, "payer_account", None),
                    "last_status": getattr(payment, "last_status", None),
                }
            )
        elif provider == PaymentMethod.WATA:
            metadata.update(
                {
                    "terminal_public_id": getattr(payment, "terminal_public_id", None),
                    "last_status": getattr(payment, "last_status", None),
                    "success_redirect_url": getattr(payment, "success_redirect_url", None),
                    "fail_redirect_url": getattr(payment, "fail_redirect_url", None),
                }
            )
        elif provider == PaymentMethod.HELEKET:
            metadata.update(
                {
                    "order_id": getattr(payment, "order_id", None),
                    "payer_amount": getattr(payment, "payer_amount", None),
                    "payer_currency": getattr(payment, "payer_currency", None),
                    "exchange_rate": getattr(payment, "exchange_rate", None),
                    "discount_percent": getattr(payment, "discount_percent", None),
                }
            )

        # Remove empty values for cleanliness
        cleaned = {
            key: value
            for key, value in metadata.items()
            if value is not None and value != {}
        }

        return cleaned

    def _get_payment_url(self, provider: PaymentMethod, payment: Any) -> Optional[str]:
        if provider == PaymentMethod.YOOKASSA:
            return getattr(payment, "confirmation_url", None)
        if provider == PaymentMethod.MULENPAY:
            return getattr(payment, "payment_url", None)
        if provider == PaymentMethod.PAL24:
            return (
                getattr(payment, "link_url", None)
                or getattr(payment, "link_page_url", None)
            )
        if provider == PaymentMethod.WATA:
            return getattr(payment, "url", None)
        if provider == PaymentMethod.HELEKET:
            return getattr(payment, "payment_url", None)

        return None

    def _get_external_id(self, provider: PaymentMethod, payment: Any) -> Optional[str]:
        if provider == PaymentMethod.YOOKASSA:
            return getattr(payment, "yookassa_payment_id", None)
        if provider == PaymentMethod.MULENPAY:
            return getattr(payment, "uuid", None)
        if provider == PaymentMethod.PAL24:
            return getattr(payment, "bill_id", None)
        if provider == PaymentMethod.WATA:
            return getattr(payment, "payment_link_id", None)
        if provider == PaymentMethod.HELEKET:
            return getattr(payment, "uuid", None)

        return None

    def _extract_user(self, payment: Any) -> dict[str, Any]:
        user = getattr(payment, "user", None)
        if not user:
            return {}

        return {
            "id": getattr(user, "id", None),
            "telegram_id": getattr(user, "telegram_id", None),
            "username": getattr(user, "username", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
        }

    def _is_pending(self, provider: PaymentMethod, payment: Any) -> bool:
        status_raw = getattr(payment, "status", "") or ""
        status_lower = str(status_raw).lower()

        if provider == PaymentMethod.YOOKASSA:
            return status_lower in {"pending", "waiting_for_capture"} and not getattr(
                payment, "is_paid", False
            )
        if provider == PaymentMethod.MULENPAY:
            return status_lower in {"created", "processing", "hold"} and not getattr(
                payment, "is_paid", False
            )
        if provider == PaymentMethod.PAL24:
            return status_lower in {"new", "process", "underpaid"} and not getattr(
                payment, "is_paid", False
            )
        if provider == PaymentMethod.WATA:
            return status_lower not in {"closed", "paid"} and not getattr(
                payment, "is_paid", False
            )
        if provider == PaymentMethod.HELEKET:
            return status_lower not in {"paid", "paid_over"} and getattr(
                payment, "transaction_id", None
            ) is None
        if provider == PaymentMethod.CRYPTOBOT:
            return status_lower == "active"

        return False

    def _is_within_age(self, payment: Any, max_age_hours: int) -> bool:
        created_at = getattr(payment, "created_at", None)
        if created_at is None:
            return True

        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        return created_at >= cutoff
