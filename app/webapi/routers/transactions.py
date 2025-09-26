from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Transaction
from app.webapi.dependencies import get_db, require_permission
from app.webapi.schemas import Pagination, TransactionListResponse, TransactionSchema

router = APIRouter(prefix="/transactions")


@router.get("", response_model=TransactionListResponse)
async def list_transactions(
    user_id: Optional[int] = Query(default=None, ge=1),
    tx_type: Optional[str] = Query(default=None, alias="type"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.transactions:read")),
) -> TransactionListResponse:
    stmt = select(Transaction)
    count_stmt = select(func.count(Transaction.id))

    if user_id:
        stmt = stmt.where(Transaction.user_id == user_id)
        count_stmt = count_stmt.where(Transaction.user_id == user_id)

    if tx_type:
        stmt = stmt.where(Transaction.type == tx_type)
        count_stmt = count_stmt.where(Transaction.type == tx_type)

    stmt = stmt.order_by(Transaction.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(stmt)
    transactions = result.scalars().all()

    total = await db.scalar(count_stmt) or 0

    pagination = Pagination(total=total, limit=limit, offset=offset)
    items = [TransactionSchema.model_validate(tx) for tx in transactions]
    return TransactionListResponse(pagination=pagination, items=items)
