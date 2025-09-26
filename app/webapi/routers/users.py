from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.subscription import (
    add_subscription_devices,
    add_subscription_squad,
    add_subscription_traffic,
    extend_subscription,
    get_subscription_by_user_id,
    remove_subscription_squad,
)
from app.database.crud.user import (
    add_user_balance,
    create_user,
    get_user_by_id,
    update_user,
)
from app.database.models import Subscription, Transaction, User, UserStatus
from app.webapi.dependencies import get_db, require_permission
from app.webapi.schemas import (
    Pagination,
    SubscriptionDevicesRequest,
    SubscriptionExtendRequest,
    SubscriptionSchema,
    SubscriptionSquadRequest,
    SubscriptionTrafficRequest,
    TransactionListResponse,
    TransactionSchema,
    UserBalanceUpdateRequest,
    UserCreateRequest,
    UserDetailResponse,
    UserListItem,
    UserListResponse,
    UserStatusUpdateRequest,
    UserUpdateRequest,
)

router = APIRouter(prefix="/users")


def _user_to_list_item(user: User) -> UserListItem:
    subscription = user.subscription
    return UserListItem(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language=user.language,
        status=user.status,
        balance_kopeks=user.balance_kopeks,
        referral_code=user.referral_code,
        created_at=user.created_at,
        updated_at=user.updated_at,
        subscription_status=subscription.status if subscription else None,
        subscription_end_date=subscription.end_date if subscription else None,
    )


def _user_to_detail(user: User, transactions: List[Transaction]) -> UserDetailResponse:
    subscription = user.subscription
    return UserDetailResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language=user.language,
        status=user.status,
        balance_kopeks=user.balance_kopeks,
        referred_by_id=user.referred_by_id,
        referral_code=user.referral_code,
        promo_group_id=user.promo_group_id,
        created_at=user.created_at,
        updated_at=user.updated_at,
        subscription=SubscriptionSchema.model_validate(subscription)
        if subscription
        else None,
        transactions=[TransactionSchema.model_validate(tx) for tx in transactions],
    )


@router.get("", response_model=UserListResponse)
async def list_users(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.users:read")),
) -> UserListResponse:
    stmt = select(User).options(selectinload(User.subscription))
    count_stmt = select(func.count(User.id))

    filters = []

    if status_filter:
        valid_statuses = {status.value for status in UserStatus}
        if status_filter not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Недопустимый статус фильтра",
            )
        filters.append(User.status == status_filter)

    if search:
        search = search.strip()
        if search.startswith("@"):
            search = search[1:]
        if search.isdigit():
            filters.append(User.telegram_id == int(search))
        else:
            like_pattern = f"%{search.lower()}%"
            filters.append(
                or_(
                    func.lower(User.username).like(like_pattern),
                    func.lower(User.first_name).like(like_pattern),
                    func.lower(User.last_name).like(like_pattern),
                    func.lower(User.referral_code).like(like_pattern),
                )
            )

    for condition in filters:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = stmt.order_by(User.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(stmt)
    users = result.scalars().unique().all()

    total = await db.scalar(count_stmt) or 0

    items = [_user_to_list_item(user) for user in users]
    pagination = Pagination(total=total, limit=limit, offset=offset)
    return UserListResponse(pagination=pagination, items=items)


@router.get("/{user_id}", response_model=UserDetailResponse)
async def get_user_detail(
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.users:read")),
) -> UserDetailResponse:
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    transactions_stmt = (
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.created_at.desc())
        .limit(50)
    )
    tx_result = await db.execute(transactions_stmt)
    transactions = tx_result.scalars().all()

    return _user_to_detail(user, transactions)


@router.post("", response_model=UserDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_user_endpoint(
    payload: UserCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.users:write")),
) -> UserDetailResponse:
    existing_stmt = select(User).where(User.telegram_id == payload.telegram_id)
    existing = await db.scalar(existing_stmt)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пользователь уже существует")

    user = await create_user(
        db,
        telegram_id=payload.telegram_id,
        username=payload.username,
        first_name=payload.first_name,
        last_name=payload.last_name,
        language=payload.language or "ru",
        referred_by_id=payload.referred_by_id,
        referral_code=payload.referral_code,
    )

    refreshed = await get_user_by_id(db, user.id)
    return _user_to_detail(refreshed or user, [])


@router.patch("/{user_id}", response_model=UserDetailResponse)
async def update_user_endpoint(
    payload: UserUpdateRequest,
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.users:write")),
) -> UserDetailResponse:
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    updates = payload.model_dump(exclude_unset=True)

    await update_user(db, user, **updates)
    refreshed = await get_user_by_id(db, user_id)

    transactions_stmt = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.created_at.desc())
        .limit(50)
    )
    tx_result = await db.execute(transactions_stmt)
    transactions = tx_result.scalars().all()

    return _user_to_detail(refreshed or user, transactions)


@router.post("/{user_id}/balance", response_model=UserDetailResponse)
async def update_balance(
    payload: UserBalanceUpdateRequest,
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.users:write")),
) -> UserDetailResponse:
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    success = await add_user_balance(
        db,
        user,
        amount_kopeks=payload.amount_kopeks,
        description=payload.description or "Изменение баланса через API",
        create_transaction=True,
    )

    if not success:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось обновить баланс")

    refreshed = await get_user_by_id(db, user_id)
    transactions_stmt = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.created_at.desc())
        .limit(50)
    )
    tx_result = await db.execute(transactions_stmt)
    transactions = tx_result.scalars().all()

    return _user_to_detail(refreshed or user, transactions)


@router.post("/{user_id}/status", response_model=UserDetailResponse)
async def update_status(
    payload: UserStatusUpdateRequest,
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.users:write")),
) -> UserDetailResponse:
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    try:
        new_status = UserStatus(payload.status).value
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый статус")

    await update_user(db, user, status=new_status)
    refreshed = await get_user_by_id(db, user_id)

    transactions_stmt = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.created_at.desc())
        .limit(50)
    )
    tx_result = await db.execute(transactions_stmt)
    transactions = tx_result.scalars().all()

    return _user_to_detail(refreshed or user, transactions)


def _get_subscription_or_404(subscription: Optional[Subscription]) -> Subscription:
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="У пользователя нет активной подписки")
    return subscription


@router.post("/{user_id}/subscription/extend", response_model=SubscriptionSchema)
async def extend_user_subscription(
    payload: SubscriptionExtendRequest,
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.subscriptions:write")),
) -> SubscriptionSchema:
    subscription = await get_subscription_by_user_id(db, user_id)
    subscription = _get_subscription_or_404(subscription)
    updated = await extend_subscription(db, subscription, payload.days)
    return SubscriptionSchema.model_validate(updated)


@router.post("/{user_id}/subscription/traffic", response_model=SubscriptionSchema)
async def add_subscription_traffic_api(
    payload: SubscriptionTrafficRequest,
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.subscriptions:write")),
) -> SubscriptionSchema:
    subscription = await get_subscription_by_user_id(db, user_id)
    subscription = _get_subscription_or_404(subscription)
    updated = await add_subscription_traffic(db, subscription, payload.gb)
    return SubscriptionSchema.model_validate(updated)


@router.post("/{user_id}/subscription/devices", response_model=SubscriptionSchema)
async def add_subscription_devices_api(
    payload: SubscriptionDevicesRequest,
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.subscriptions:write")),
) -> SubscriptionSchema:
    subscription = await get_subscription_by_user_id(db, user_id)
    subscription = _get_subscription_or_404(subscription)
    updated = await add_subscription_devices(db, subscription, payload.devices)
    return SubscriptionSchema.model_validate(updated)


@router.post("/{user_id}/subscription/squads", response_model=SubscriptionSchema)
async def add_subscription_squad_api(
    payload: SubscriptionSquadRequest,
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.subscriptions:write")),
) -> SubscriptionSchema:
    subscription = await get_subscription_by_user_id(db, user_id)
    subscription = _get_subscription_or_404(subscription)
    updated = await add_subscription_squad(db, subscription, payload.squad_uuid)
    return SubscriptionSchema.model_validate(updated)


@router.delete("/{user_id}/subscription/squads/{squad_uuid}", response_model=SubscriptionSchema)
async def remove_subscription_squad_api(
    squad_uuid: str,
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.subscriptions:write")),
) -> SubscriptionSchema:
    subscription = await get_subscription_by_user_id(db, user_id)
    subscription = _get_subscription_or_404(subscription)
    updated = await remove_subscription_squad(db, subscription, squad_uuid)
    return SubscriptionSchema.model_validate(updated)


@router.get("/{user_id}/transactions", response_model=TransactionListResponse)
async def list_user_transactions(
    user_id: int = Path(..., ge=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.transactions:read")),
) -> TransactionListResponse:
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    total_stmt = select(func.count(Transaction.id)).where(Transaction.user_id == user_id)
    total = await db.scalar(total_stmt) or 0

    stmt = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    transactions = result.scalars().all()

    pagination = Pagination(total=total, limit=limit, offset=offset)
    items = [TransactionSchema.model_validate(tx) for tx in transactions]
    return TransactionListResponse(pagination=pagination, items=items)
