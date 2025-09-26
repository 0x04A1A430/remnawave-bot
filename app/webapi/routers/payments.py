from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CryptoBotPayment, MulenPayPayment, Pal24Payment, YooKassaPayment
from app.webapi.dependencies import get_db, require_permission
from app.webapi.schemas import (
    CryptoBotPaymentListResponse,
    CryptoBotPaymentSchema,
    MulenPayPaymentListResponse,
    MulenPayPaymentSchema,
    Pal24PaymentListResponse,
    Pal24PaymentSchema,
    Pagination,
    YooKassaPaymentListResponse,
    YooKassaPaymentSchema,
)

router = APIRouter(prefix="/payments")


async def _paginate(
    db: AsyncSession,
    base_query,
    count_query,
    limit: int,
    offset: int,
):
    result = await db.execute(base_query.offset(offset).limit(limit))
    items = result.scalars().all()
    total = await db.scalar(count_query) or 0
    return items, total


@router.get("/yookassa", response_model=YooKassaPaymentListResponse)
async def list_yookassa_payments(
    user_id: Optional[int] = Query(default=None, ge=1),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    is_paid: Optional[bool] = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.payments:read")),
):
    stmt = select(YooKassaPayment)
    count_stmt = select(func.count(YooKassaPayment.id))

    if user_id:
        stmt = stmt.where(YooKassaPayment.user_id == user_id)
        count_stmt = count_stmt.where(YooKassaPayment.user_id == user_id)
    if status_filter:
        stmt = stmt.where(YooKassaPayment.status == status_filter)
        count_stmt = count_stmt.where(YooKassaPayment.status == status_filter)
    if is_paid is not None:
        stmt = stmt.where(YooKassaPayment.is_paid.is_(is_paid))
        count_stmt = count_stmt.where(YooKassaPayment.is_paid.is_(is_paid))

    items, total = await _paginate(db, stmt.order_by(YooKassaPayment.created_at.desc()), count_stmt, limit, offset)
    return YooKassaPaymentListResponse(
        pagination=Pagination(total=total, limit=limit, offset=offset),
        items=[YooKassaPaymentSchema.model_validate(item) for item in items],
    )


@router.get("/cryptobot", response_model=CryptoBotPaymentListResponse)
async def list_cryptobot_payments(
    user_id: Optional[int] = Query(default=None, ge=1),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.payments:read")),
):
    stmt = select(CryptoBotPayment)
    count_stmt = select(func.count(CryptoBotPayment.id))

    if user_id:
        stmt = stmt.where(CryptoBotPayment.user_id == user_id)
        count_stmt = count_stmt.where(CryptoBotPayment.user_id == user_id)
    if status_filter:
        stmt = stmt.where(CryptoBotPayment.status == status_filter)
        count_stmt = count_stmt.where(CryptoBotPayment.status == status_filter)

    items, total = await _paginate(db, stmt.order_by(CryptoBotPayment.created_at.desc()), count_stmt, limit, offset)
    return CryptoBotPaymentListResponse(
        pagination=Pagination(total=total, limit=limit, offset=offset),
        items=[CryptoBotPaymentSchema.model_validate(item) for item in items],
    )


@router.get("/mulenpay", response_model=MulenPayPaymentListResponse)
async def list_mulen_payments(
    user_id: Optional[int] = Query(default=None, ge=1),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    is_paid: Optional[bool] = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.payments:read")),
):
    stmt = select(MulenPayPayment)
    count_stmt = select(func.count(MulenPayPayment.id))

    if user_id:
        stmt = stmt.where(MulenPayPayment.user_id == user_id)
        count_stmt = count_stmt.where(MulenPayPayment.user_id == user_id)
    if status_filter:
        stmt = stmt.where(MulenPayPayment.status == status_filter)
        count_stmt = count_stmt.where(MulenPayPayment.status == status_filter)
    if is_paid is not None:
        stmt = stmt.where(MulenPayPayment.is_paid.is_(is_paid))
        count_stmt = count_stmt.where(MulenPayPayment.is_paid.is_(is_paid))

    items, total = await _paginate(db, stmt.order_by(MulenPayPayment.created_at.desc()), count_stmt, limit, offset)
    return MulenPayPaymentListResponse(
        pagination=Pagination(total=total, limit=limit, offset=offset),
        items=[MulenPayPaymentSchema.model_validate(item) for item in items],
    )


@router.get("/pal24", response_model=Pal24PaymentListResponse)
async def list_pal24_payments(
    user_id: Optional[int] = Query(default=None, ge=1),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    is_paid: Optional[bool] = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.payments:read")),
):
    stmt = select(Pal24Payment)
    count_stmt = select(func.count(Pal24Payment.id))

    if user_id:
        stmt = stmt.where(Pal24Payment.user_id == user_id)
        count_stmt = count_stmt.where(Pal24Payment.user_id == user_id)
    if status_filter:
        stmt = stmt.where(Pal24Payment.status == status_filter)
        count_stmt = count_stmt.where(Pal24Payment.status == status_filter)
    if is_paid is not None:
        stmt = stmt.where(Pal24Payment.is_paid.is_(is_paid))
        count_stmt = count_stmt.where(Pal24Payment.is_paid.is_(is_paid))

    items, total = await _paginate(db, stmt.order_by(Pal24Payment.created_at.desc()), count_stmt, limit, offset)
    return Pal24PaymentListResponse(
        pagination=Pagination(total=total, limit=limit, offset=offset),
        items=[Pal24PaymentSchema.model_validate(item) for item in items],
    )
