from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PaymentMethod, Transaction
from app.services.pending_payment_service import PendingPaymentService, PendingPaymentError, PendingPaymentNotFoundError, PendingPaymentNotPendingError, PendingPaymentTooOldError, PendingPaymentUnsupportedError

from ..dependencies import get_db_session, require_api_token
from ..schemas.transactions import (
    PendingPaymentBulkCheckResponse,
    PendingPaymentCheckResponse,
    PendingPaymentDetailResponse,
    PendingPaymentListResponse,
    PendingPaymentResponse,
    PendingPaymentUserResponse,
    TransactionListResponse,
    TransactionResponse,
)

router = APIRouter()


def _serialize(transaction: Transaction) -> TransactionResponse:
    return TransactionResponse(
        id=transaction.id,
        user_id=transaction.user_id,
        type=transaction.type,
        amount_kopeks=transaction.amount_kopeks,
        amount_rubles=round(transaction.amount_kopeks / 100, 2),
        description=transaction.description,
        payment_method=transaction.payment_method,
        external_id=transaction.external_id,
        is_completed=transaction.is_completed,
        created_at=transaction.created_at,
        completed_at=transaction.completed_at,
    )


@router.get("", response_model=TransactionListResponse)
async def list_transactions(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: Optional[int] = Query(default=None),
    type_filter: Optional[str] = Query(default=None, alias="type"),
    payment_method: Optional[str] = Query(default=None),
    is_completed: Optional[bool] = Query(default=None),
    date_from: Optional[datetime] = Query(default=None),
    date_to: Optional[datetime] = Query(default=None),
) -> TransactionListResponse:
    base_query = select(Transaction)
    conditions = []

    if user_id:
        conditions.append(Transaction.user_id == user_id)
    if type_filter:
        conditions.append(Transaction.type == type_filter)
    if payment_method:
        conditions.append(Transaction.payment_method == payment_method)
    if is_completed is not None:
        conditions.append(Transaction.is_completed.is_(is_completed))
    if date_from:
        conditions.append(Transaction.created_at >= date_from)
    if date_to:
        conditions.append(Transaction.created_at <= date_to)

    if conditions:
        base_query = base_query.where(and_(*conditions))

    total_query = base_query.with_only_columns(func.count()).order_by(None)
    total = await db.scalar(total_query) or 0

    result = await db.execute(
        base_query.order_by(Transaction.created_at.desc()).offset(offset).limit(limit)
    )
    transactions = result.scalars().all()

    return TransactionListResponse(
        items=[_serialize(tx) for tx in transactions],
        total=int(total),
        limit=limit,
        offset=offset,
    )


def _parse_provider(service: PendingPaymentService, provider: str) -> PaymentMethod:
    try:
        method = PaymentMethod(provider)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown payment provider") from error

    if method not in service.SUPPORTED_METHODS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unsupported payment provider")

    return method


def _build_pending_summary(data: dict) -> PendingPaymentResponse:
    user_data = data.get("user") or {}
    return PendingPaymentResponse(
        id=data.get("id"),
        provider=data.get("provider"),
        user=PendingPaymentUserResponse(**user_data),
        amount_kopeks=data.get("amount_kopeks", 0),
        amount_rubles=data.get("amount_rubles", 0.0),
        currency=data.get("currency", "RUB"),
        status=data.get("status"),
        is_paid=data.get("is_paid", False),
        description=data.get("description"),
        payment_url=data.get("payment_url"),
        external_id=data.get("external_id"),
        transaction_id=data.get("transaction_id"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        expires_at=data.get("expires_at"),
        is_pending=data.get("is_pending", False),
    )


def _build_pending_detail(data: dict) -> PendingPaymentDetailResponse:
    summary = _build_pending_summary(data)
    return PendingPaymentDetailResponse(
        **summary.dict(),
        metadata=data.get("metadata"),
    )


@router.get("/pending-payments", response_model=PendingPaymentListResponse)
async def list_pending_payments(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    provider: Optional[str] = Query(default=None),
    max_age_hours: int = Query(default=24, ge=1, le=72),
) -> PendingPaymentListResponse:
    service = PendingPaymentService()
    payment_method = _parse_provider(service, provider) if provider else None

    raw_items = await service.list_pending_payments(
        db,
        max_age_hours=max_age_hours,
        provider=payment_method,
    )

    items = [_build_pending_summary(item) for item in raw_items]

    return PendingPaymentListResponse(
        items=items,
        total=len(items),
    )


@router.get(
    "/pending-payments/{provider}/{payment_id}",
    response_model=PendingPaymentDetailResponse,
)
async def get_pending_payment(
    provider: str,
    payment_id: int,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> PendingPaymentDetailResponse:
    service = PendingPaymentService()
    payment_method = _parse_provider(service, provider)

    try:
        payment = await service.get_payment(db, payment_method, payment_id)
    except PendingPaymentNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found") from error

    return _build_pending_detail(payment)


@router.post(
    "/pending-payments/{provider}/{payment_id}/check",
    response_model=PendingPaymentCheckResponse,
)
async def check_pending_payment(
    provider: str,
    payment_id: int,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    max_age_hours: int = Query(default=24, ge=1, le=72),
) -> PendingPaymentCheckResponse:
    service = PendingPaymentService()
    payment_method = _parse_provider(service, provider)

    try:
        result = await service.run_manual_check(
            db,
            payment_method,
            payment_id,
            max_age_hours=max_age_hours,
        )
    except PendingPaymentNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found") from error
    except PendingPaymentNotPendingError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is not pending") from error
    except PendingPaymentTooOldError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment is older than the allowed interval") from error
    except PendingPaymentUnsupportedError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except PendingPaymentError as error:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to check payment") from error

    detail = _build_pending_detail(result)

    return PendingPaymentCheckResponse(
        payment=detail,
        check_performed=bool(result.get("check_performed")),
        status_before=result.get("status_before"),
        status_after=result.get("status_after"),
        completed=bool(result.get("completed")),
    )


@router.post(
    "/pending-payments/check",
    response_model=PendingPaymentBulkCheckResponse,
)
async def check_all_pending_payments(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    provider: Optional[str] = Query(default=None),
    max_age_hours: int = Query(default=24, ge=1, le=72),
) -> PendingPaymentBulkCheckResponse:
    service = PendingPaymentService()
    payment_method = _parse_provider(service, provider) if provider else None

    summary = await service.run_bulk_check(
        db,
        max_age_hours=max_age_hours,
        provider=payment_method,
    )

    results = [
        PendingPaymentCheckResponse(
            payment=_build_pending_detail(item),
            check_performed=bool(item.get("check_performed")),
            status_before=item.get("status_before"),
            status_after=item.get("status_after"),
            completed=bool(item.get("completed")),
        )
        for item in summary.get("results", [])
    ]

    return PendingPaymentBulkCheckResponse(
        total=summary.get("total", 0),
        checked=summary.get("checked", 0),
        completed=summary.get("completed", 0),
        skipped=summary.get("skipped", 0),
        results=results,
    )
