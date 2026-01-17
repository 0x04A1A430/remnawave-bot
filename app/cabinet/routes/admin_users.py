"""Admin routes for managing users in cabinet."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, Integer

from app.database.models import (
    User,
    UserStatus,
    Subscription,
    SubscriptionStatus,
    Transaction,
    TransactionType,
    PromoGroup,
    Tariff,
)
from app.database.crud.user import (
    get_user_by_id,
    get_user_by_telegram_id,
    get_users_list,
    get_users_count,
    get_users_statistics,
    get_users_spending_stats,
    add_user_balance,
    subtract_user_balance,
    update_user,
    delete_user as soft_delete_user,
    get_referrals,
)
from app.database.crud.subscription import (
    get_subscription_by_user_id,
    extend_subscription,
)
from app.database.crud.tariff import get_tariff_by_id

from ..dependencies import get_cabinet_db, get_current_admin_user
from ..schemas.users import (
    UserListItem,
    UsersListResponse,
    UserDetailResponse,
    UserSubscriptionInfo,
    UserPromoGroupInfo,
    UserTransactionItem,
    UserReferralInfo,
    UpdateBalanceRequest,
    UpdateBalanceResponse,
    UpdateSubscriptionRequest,
    UpdateSubscriptionResponse,
    UpdateUserStatusRequest,
    UpdateUserStatusResponse,
    UpdateRestrictionsRequest,
    UpdateRestrictionsResponse,
    UpdatePromoGroupRequest,
    UpdatePromoGroupResponse,
    DeleteUserRequest,
    DeleteUserResponse,
    UsersStatsResponse,
    SortByEnum,
    UserStatusEnum,
    UserAvailableTariffItem,
    UserAvailableTariffsResponse,
    PeriodPriceInfo,
    PanelUserInfo,
    SyncFromPanelRequest,
    SyncFromPanelResponse,
    SyncToPanelRequest,
    SyncToPanelResponse,
    PanelSyncStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/users", tags=["Cabinet Admin Users"])


def _build_user_list_item(user: User, spending_stats: dict = None) -> UserListItem:
    """Build UserListItem from User model."""
    stats = spending_stats or {}
    user_stats = stats.get(user.id, {"total_spent": 0, "purchase_count": 0})

    subscription_status = None
    subscription_is_trial = False
    subscription_end_date = None
    has_subscription = False

    if user.subscription:
        has_subscription = True
        subscription_status = user.subscription.status
        subscription_is_trial = user.subscription.is_trial
        subscription_end_date = user.subscription.end_date

    return UserListItem(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        full_name=user.full_name,
        status=user.status,
        balance_kopeks=user.balance_kopeks,
        balance_rubles=user.balance_rubles,
        created_at=user.created_at,
        last_activity=user.last_activity,
        has_subscription=has_subscription,
        subscription_status=subscription_status,
        subscription_is_trial=subscription_is_trial,
        subscription_end_date=subscription_end_date,
        promo_group_id=user.promo_group_id,
        promo_group_name=user.promo_group.name if user.promo_group else None,
        total_spent_kopeks=user_stats.get("total_spent", 0),
        purchase_count=user_stats.get("purchase_count", 0),
        has_restrictions=user.has_restrictions,
        restriction_topup=user.restriction_topup,
        restriction_subscription=user.restriction_subscription,
    )


def _build_subscription_info(subscription: Subscription, tariff_name: Optional[str] = None) -> UserSubscriptionInfo:
    """Build UserSubscriptionInfo from Subscription model."""
    days_remaining = 0
    is_active = False

    if subscription.end_date:
        delta = subscription.end_date - datetime.utcnow()
        days_remaining = max(0, delta.days)
        is_active = (
            subscription.status == SubscriptionStatus.ACTIVE.value
            and subscription.end_date > datetime.utcnow()
        )

    return UserSubscriptionInfo(
        id=subscription.id,
        status=subscription.status,
        is_trial=subscription.is_trial,
        start_date=subscription.start_date,
        end_date=subscription.end_date,
        traffic_limit_gb=subscription.traffic_limit_gb,
        traffic_used_gb=subscription.traffic_used_gb or 0.0,
        device_limit=subscription.device_limit,
        tariff_id=subscription.tariff_id,
        tariff_name=tariff_name,
        autopay_enabled=subscription.autopay_enabled,
        is_active=is_active,
        days_remaining=days_remaining,
    )


async def _build_subscription_info_async(db: AsyncSession, subscription: Subscription) -> UserSubscriptionInfo:
    """Build UserSubscriptionInfo from Subscription model, fetching tariff name asynchronously."""
    tariff_name = None
    if subscription.tariff_id:
        tariff = await get_tariff_by_id(db, subscription.tariff_id)
        if tariff:
            tariff_name = tariff.name
    return _build_subscription_info(subscription, tariff_name=tariff_name)


async def _sync_subscription_to_panel(db: AsyncSession, user: User, subscription: Subscription) -> dict:
    """
    Sync user subscription to Remnawave panel.
    Creates user if not exists, updates if exists.
    Returns dict with changes/errors.
    """
    try:
        from app.services.remnawave_service import RemnaWaveService
        from app.external.remnawave_api import UserStatus as PanelUserStatus, TrafficLimitStrategy
        from app.config import settings
        from app.utils.subscription_utils import resolve_hwid_device_limit_for_payload

        service = RemnaWaveService()
        if not service.is_configured:
            logger.warning(f"Remnawave not configured, skipping panel sync for user {user.id}")
            return {"skipped": True, "reason": "Remnawave not configured"}

        is_active = (
            subscription.status in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIAL.value)
            and subscription.end_date
            and subscription.end_date > datetime.utcnow()
        )
        panel_status = PanelUserStatus.ACTIVE if is_active else PanelUserStatus.DISABLED

        expire_at = subscription.end_date
        if expire_at and expire_at <= datetime.utcnow():
            expire_at = datetime.utcnow() + timedelta(minutes=1)

        username = settings.format_remnawave_username(
            full_name=user.full_name,
            username=user.username,
            telegram_id=user.telegram_id,
        )

        description = settings.format_remnawave_user_description(
            full_name=user.full_name,
            username=user.username,
            telegram_id=user.telegram_id,
        )

        hwid_limit = resolve_hwid_device_limit_for_payload(subscription)
        traffic_limit_bytes = subscription.traffic_limit_gb * (1024**3) if subscription.traffic_limit_gb > 0 else 0

        changes = {}
        async with service.get_api_client() as api:
            panel_uuid = user.remnawave_uuid

            # Try to find existing user
            if not panel_uuid:
                existing_users = await api.get_user_by_telegram_id(user.telegram_id)
                if existing_users:
                    panel_uuid = existing_users[0].uuid
                    user.remnawave_uuid = panel_uuid
                    changes["remnawave_uuid_discovered"] = panel_uuid

            if panel_uuid:
                # Update existing user
                update_kwargs = {
                    "uuid": panel_uuid,
                    "status": panel_status,
                    "traffic_limit_bytes": traffic_limit_bytes,
                    "traffic_limit_strategy": TrafficLimitStrategy.MONTH,
                    "description": description,
                }
                if expire_at:
                    update_kwargs["expire_at"] = expire_at
                if subscription.connected_squads:
                    update_kwargs["active_internal_squads"] = subscription.connected_squads
                if hwid_limit is not None:
                    update_kwargs["hwid_device_limit"] = hwid_limit

                try:
                    await api.update_user(**update_kwargs)
                    changes["action"] = "updated"
                    logger.info(f"Updated user {user.id} in Remnawave panel")
                except Exception as update_error:
                    if hasattr(update_error, 'status_code') and update_error.status_code == 404:
                        panel_uuid = None  # Will create new
                    else:
                        raise

            if not panel_uuid:
                # Create new user
                create_kwargs = {
                    "username": username,
                    "expire_at": expire_at or (datetime.utcnow() + timedelta(days=30)),
                    "status": panel_status,
                    "traffic_limit_bytes": traffic_limit_bytes,
                    "traffic_limit_strategy": TrafficLimitStrategy.MONTH,
                    "telegram_id": user.telegram_id,
                    "description": description,
                    "active_internal_squads": subscription.connected_squads or [],
                }
                if hwid_limit is not None:
                    create_kwargs["hwid_device_limit"] = hwid_limit

                new_panel_user = await api.create_user(**create_kwargs)
                user.remnawave_uuid = new_panel_user.uuid
                subscription.remnawave_short_uuid = new_panel_user.short_uuid
                subscription.subscription_url = new_panel_user.subscription_url
                changes["action"] = "created"
                changes["panel_uuid"] = new_panel_user.uuid
                logger.info(f"Created user {user.id} in Remnawave panel: {new_panel_user.uuid}")

            user.last_remnawave_sync = datetime.utcnow()
            await db.commit()

        return changes

    except Exception as e:
        logger.error(f"Error syncing user {user.id} to panel: {e}")
        return {"error": str(e)}


# === List & Search ===

@router.get("", response_model=UsersListResponse)
async def list_users(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None, max_length=255),
    status: Optional[UserStatusEnum] = Query(None),
    sort_by: SortByEnum = Query(SortByEnum.CREATED_AT),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Get paginated list of users with filtering and sorting.

    - **offset**: Pagination offset
    - **limit**: Number of users per page (max 200)
    - **search**: Search by telegram_id, username, first_name, last_name
    - **status**: Filter by user status (active, blocked, deleted)
    - **sort_by**: Sort field (created_at, balance, traffic, last_activity, total_spent, purchase_count)
    """
    # Convert status enum to model enum
    user_status = None
    if status:
        user_status = UserStatus(status.value)

    # Map sort options
    order_by_balance = sort_by == SortByEnum.BALANCE
    order_by_traffic = sort_by == SortByEnum.TRAFFIC
    order_by_last_activity = sort_by == SortByEnum.LAST_ACTIVITY
    order_by_total_spent = sort_by == SortByEnum.TOTAL_SPENT
    order_by_purchase_count = sort_by == SortByEnum.PURCHASE_COUNT

    users = await get_users_list(
        db=db,
        offset=offset,
        limit=limit,
        search=search,
        status=user_status,
        order_by_balance=order_by_balance,
        order_by_traffic=order_by_traffic,
        order_by_last_activity=order_by_last_activity,
        order_by_total_spent=order_by_total_spent,
        order_by_purchase_count=order_by_purchase_count,
    )

    total = await get_users_count(db=db, status=user_status, search=search)

    # Get spending stats for all users
    user_ids = [u.id for u in users]
    spending_stats = await get_users_spending_stats(db, user_ids) if user_ids else {}

    items = [_build_user_list_item(u, spending_stats) for u in users]

    return UsersListResponse(
        users=items,
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/stats", response_model=UsersStatsResponse)
async def get_users_stats(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get overall users statistics."""
    stats = await get_users_statistics(db)

    # Get subscription stats
    sub_stats_query = select(
        func.count(Subscription.id).label("total"),
        func.sum(
            func.cast(
                and_(
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                    Subscription.end_date > datetime.utcnow(),
                ),
                Integer,
            )
        ).label("active"),
        func.sum(func.cast(Subscription.is_trial == True, Integer)).label("trial"),
        func.sum(
            func.cast(
                or_(
                    Subscription.status == SubscriptionStatus.EXPIRED.value,
                    Subscription.end_date <= datetime.utcnow(),
                ),
                Integer,
            )
        ).label("expired"),
    )
    sub_result = await db.execute(sub_stats_query)
    sub_row = sub_result.one_or_none()

    users_with_subscription = sub_row.total or 0 if sub_row else 0
    users_with_active = sub_row.active or 0 if sub_row else 0
    users_with_trial = sub_row.trial or 0 if sub_row else 0
    users_with_expired = sub_row.expired or 0 if sub_row else 0

    # Get balance stats
    balance_query = select(
        func.sum(User.balance_kopeks).label("total"),
        func.avg(User.balance_kopeks).label("avg"),
    ).where(User.status == UserStatus.ACTIVE.value)
    balance_result = await db.execute(balance_query)
    balance_row = balance_result.one_or_none()
    total_balance = balance_row.total or 0 if balance_row else 0
    avg_balance = int(balance_row.avg or 0) if balance_row else 0

    # Get activity stats
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    active_today_q = select(func.count(User.id)).where(
        User.last_activity >= today_start,
        User.status == UserStatus.ACTIVE.value,
    )
    active_week_q = select(func.count(User.id)).where(
        User.last_activity >= week_ago,
        User.status == UserStatus.ACTIVE.value,
    )
    active_month_q = select(func.count(User.id)).where(
        User.last_activity >= month_ago,
        User.status == UserStatus.ACTIVE.value,
    )

    active_today = (await db.execute(active_today_q)).scalar() or 0
    active_week = (await db.execute(active_week_q)).scalar() or 0
    active_month = (await db.execute(active_month_q)).scalar() or 0

    # Count deleted users
    deleted_q = select(func.count(User.id)).where(User.status == UserStatus.DELETED.value)
    deleted_count = (await db.execute(deleted_q)).scalar() or 0

    return UsersStatsResponse(
        total_users=stats["total_users"],
        active_users=stats["active_users"],
        blocked_users=stats["blocked_users"],
        deleted_users=deleted_count,
        new_today=stats["new_today"],
        new_week=stats["new_week"],
        new_month=stats["new_month"],
        users_with_subscription=users_with_subscription,
        users_with_active_subscription=users_with_active,
        users_with_trial=users_with_trial,
        users_with_expired_subscription=users_with_expired,
        total_balance_kopeks=total_balance,
        total_balance_rubles=total_balance / 100,
        avg_balance_kopeks=avg_balance,
        active_today=active_today,
        active_week=active_week,
        active_month=active_month,
    )


# === User Detail ===

@router.get("/{user_id}", response_model=UserDetailResponse)
async def get_user_detail(
    user_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get detailed user information by ID."""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get spending stats
    spending_stats = await get_users_spending_stats(db, [user.id])
    user_stats = spending_stats.get(user.id, {"total_spent": 0, "purchase_count": 0})

    # Build subscription info
    subscription_info = None
    if user.subscription:
        subscription_info = await _build_subscription_info_async(db, user.subscription)

    # Build promo group info
    promo_group_info = None
    if user.promo_group:
        promo_group_info = UserPromoGroupInfo(
            id=user.promo_group.id,
            name=user.promo_group.name,
            is_default=user.promo_group.is_default,
        )

    # Get referrals count
    referrals = await get_referrals(db, user.id)
    referrals_count = len(referrals)

    # Calculate total referral earnings
    referral_earnings_q = select(func.sum(Transaction.amount_kopeks)).where(
        Transaction.user_id == user.id,
        Transaction.type == TransactionType.REFERRAL_REWARD.value,
        Transaction.is_completed == True,
    )
    referral_earnings = (await db.execute(referral_earnings_q)).scalar() or 0

    # Get referrer info
    referred_by_username = None
    if user.referred_by_id:
        referrer_q = select(User).where(User.id == user.referred_by_id)
        referrer_result = await db.execute(referrer_q)
        referrer = referrer_result.scalar_one_or_none()
        if referrer:
            referred_by_username = referrer.username or referrer.full_name

    referral_info = UserReferralInfo(
        referral_code=user.referral_code or "",
        referrals_count=referrals_count,
        total_earnings_kopeks=referral_earnings,
        commission_percent=user.referral_commission_percent,
        referred_by_id=user.referred_by_id,
        referred_by_username=referred_by_username,
    )

    # Get recent transactions
    transactions_q = (
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.created_at.desc())
        .limit(20)
    )
    transactions_result = await db.execute(transactions_q)
    transactions = transactions_result.scalars().all()

    recent_transactions = [
        UserTransactionItem(
            id=t.id,
            type=t.type,
            amount_kopeks=t.amount_kopeks,
            amount_rubles=t.amount_kopeks / 100,
            description=t.description,
            payment_method=t.payment_method,
            is_completed=t.is_completed,
            created_at=t.created_at,
        )
        for t in transactions
    ]

    return UserDetailResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        full_name=user.full_name,
        status=user.status,
        language=user.language,
        balance_kopeks=user.balance_kopeks,
        balance_rubles=user.balance_rubles,
        email=user.email,
        email_verified=user.email_verified,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_activity=user.last_activity,
        cabinet_last_login=user.cabinet_last_login,
        subscription=subscription_info,
        promo_group=promo_group_info,
        referral=referral_info,
        total_spent_kopeks=user_stats.get("total_spent", 0),
        purchase_count=user_stats.get("purchase_count", 0),
        used_promocodes=user.used_promocodes,
        has_had_paid_subscription=user.has_had_paid_subscription,
        lifetime_used_traffic_bytes=user.lifetime_used_traffic_bytes or 0,
        restriction_topup=user.restriction_topup,
        restriction_subscription=user.restriction_subscription,
        restriction_reason=user.restriction_reason,
        promo_offer_discount_percent=user.promo_offer_discount_percent,
        promo_offer_discount_source=user.promo_offer_discount_source,
        promo_offer_discount_expires_at=user.promo_offer_discount_expires_at,
        recent_transactions=recent_transactions,
        remnawave_uuid=user.remnawave_uuid,
    )


@router.get("/by-telegram/{telegram_id}", response_model=UserDetailResponse)
async def get_user_by_telegram(
    telegram_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get user by Telegram ID."""
    user = await get_user_by_telegram_id(db, telegram_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return await get_user_detail(user.id, admin, db)


# === Balance Management ===

@router.post("/{user_id}/balance", response_model=UpdateBalanceResponse)
async def update_user_balance(
    user_id: int,
    request: UpdateBalanceRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Update user balance.

    - Positive amount: adds to balance
    - Negative amount: subtracts from balance
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    old_balance = user.balance_kopeks

    if request.amount_kopeks >= 0:
        # Add balance
        success = await add_user_balance(
            db=db,
            user=user,
            amount_kopeks=request.amount_kopeks,
            description=request.description,
            create_transaction=request.create_transaction,
            transaction_type=TransactionType.DEPOSIT,
        )
    else:
        # Subtract balance
        amount_to_subtract = abs(request.amount_kopeks)
        if user.balance_kopeks < amount_to_subtract:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient balance. Current: {user.balance_kopeks}, requested: {amount_to_subtract}",
            )
        success = await subtract_user_balance(
            db=db,
            user=user,
            amount_kopeks=amount_to_subtract,
            description=request.description,
            create_transaction=request.create_transaction,
        )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update balance",
        )

    # Refresh user
    await db.refresh(user)

    logger.info(
        f"Admin {admin.id} updated balance for user {user_id}: "
        f"{old_balance} -> {user.balance_kopeks} ({request.amount_kopeks:+d})"
    )

    return UpdateBalanceResponse(
        success=True,
        old_balance_kopeks=old_balance,
        new_balance_kopeks=user.balance_kopeks,
        message=f"Balance updated: {old_balance/100:.2f}₽ -> {user.balance_kopeks/100:.2f}₽",
    )


# === Subscription Management ===

@router.post("/{user_id}/subscription", response_model=UpdateSubscriptionResponse)
async def update_user_subscription(
    user_id: int,
    request: UpdateSubscriptionRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Update user subscription.

    Actions:
    - **extend**: Extend subscription by X days
    - **set_end_date**: Set specific end date
    - **change_tariff**: Change subscription tariff
    - **set_traffic**: Set traffic limit and/or used traffic
    - **toggle_autopay**: Enable/disable autopay
    - **cancel**: Cancel subscription (set status to expired)
    - **activate**: Activate subscription
    - **create**: Create new subscription if not exists
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    subscription = user.subscription

    if request.action == "create":
        # Create new subscription
        if subscription:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User already has a subscription",
            )

        from app.database.crud.subscription import create_paid_subscription

        days = request.days or 30
        is_trial = request.is_trial or False
        traffic_limit = request.traffic_limit_gb or 100
        device_limit = request.device_limit or 1
        connected_squads = []

        # Get tariff for settings if provided
        if request.tariff_id:
            tariff = await get_tariff_by_id(db, request.tariff_id)
            if tariff:
                if not request.traffic_limit_gb:
                    traffic_limit = tariff.traffic_limit_gb
                if not request.device_limit:
                    device_limit = tariff.device_limit
                if tariff.allowed_squads:
                    connected_squads = tariff.allowed_squads

        new_sub = await create_paid_subscription(
            db=db,
            user_id=user.id,
            duration_days=days,
            traffic_limit_gb=traffic_limit,
            device_limit=device_limit,
            is_trial=is_trial,
            tariff_id=request.tariff_id,
            connected_squads=connected_squads,
        )

        # Sync to Remnawave panel
        await _sync_subscription_to_panel(db, user, new_sub)

        logger.info(f"Admin {admin.id} created subscription for user {user_id}")

        return UpdateSubscriptionResponse(
            success=True,
            message=f"Subscription created for {days} days",
            subscription=await _build_subscription_info_async(db, new_sub),
        )

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User has no subscription",
        )

    if request.action == "extend":
        if not request.days:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Days parameter is required for extend action",
            )

        old_end = subscription.end_date
        await extend_subscription(db, subscription, request.days)
        await db.refresh(subscription)

        # Sync to Remnawave panel
        await _sync_subscription_to_panel(db, user, subscription)

        logger.info(
            f"Admin {admin.id} extended subscription for user {user_id} by {request.days} days"
        )

        return UpdateSubscriptionResponse(
            success=True,
            message=f"Subscription extended by {request.days} days",
            subscription=await _build_subscription_info_async(db, subscription),
        )

    elif request.action == "set_end_date":
        if not request.end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="end_date parameter is required",
            )

        subscription.end_date = request.end_date
        if request.end_date > datetime.utcnow():
            subscription.status = SubscriptionStatus.ACTIVE.value
        else:
            subscription.status = SubscriptionStatus.EXPIRED.value

        await db.commit()
        await db.refresh(subscription)

        # Sync to Remnawave panel
        await _sync_subscription_to_panel(db, user, subscription)

        logger.info(f"Admin {admin.id} set end_date for user {user_id} subscription")

        return UpdateSubscriptionResponse(
            success=True,
            message=f"Subscription end date set to {request.end_date.isoformat()}",
            subscription=await _build_subscription_info_async(db, subscription),
        )

    elif request.action == "change_tariff":
        if request.tariff_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tariff_id parameter is required",
            )

        tariff = await get_tariff_by_id(db, request.tariff_id)
        if not tariff:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tariff not found",
            )

        subscription.tariff_id = request.tariff_id
        subscription.traffic_limit_gb = tariff.traffic_limit_gb
        subscription.device_limit = tariff.device_limit
        # Set squads from tariff
        if tariff.allowed_squads:
            subscription.connected_squads = tariff.allowed_squads
        await db.commit()
        await db.refresh(subscription)

        # Sync to Remnawave panel
        await _sync_subscription_to_panel(db, user, subscription)

        logger.info(
            f"Admin {admin.id} changed tariff for user {user_id} to {tariff.name}"
        )

        return UpdateSubscriptionResponse(
            success=True,
            message=f"Tariff changed to {tariff.name}",
            subscription=await _build_subscription_info_async(db, subscription),
        )

    elif request.action == "set_traffic":
        if request.traffic_limit_gb is not None:
            subscription.traffic_limit_gb = request.traffic_limit_gb

        if request.traffic_used_gb is not None:
            subscription.traffic_used_gb = request.traffic_used_gb

        await db.commit()
        await db.refresh(subscription)

        # Sync to Remnawave panel
        await _sync_subscription_to_panel(db, user, subscription)

        logger.info(f"Admin {admin.id} updated traffic for user {user_id}")

        return UpdateSubscriptionResponse(
            success=True,
            message="Traffic settings updated",
            subscription=await _build_subscription_info_async(db, subscription),
        )

    elif request.action == "toggle_autopay":
        if request.autopay_enabled is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="autopay_enabled parameter is required",
            )

        subscription.autopay_enabled = request.autopay_enabled
        await db.commit()
        await db.refresh(subscription)

        state = "enabled" if request.autopay_enabled else "disabled"
        logger.info(f"Admin {admin.id} {state} autopay for user {user_id}")

        return UpdateSubscriptionResponse(
            success=True,
            message=f"Autopay {state}",
            subscription=await _build_subscription_info_async(db, subscription),
        )

    elif request.action == "cancel":
        subscription.status = SubscriptionStatus.EXPIRED.value
        subscription.end_date = datetime.utcnow()
        await db.commit()
        await db.refresh(subscription)

        # Sync to Remnawave panel
        await _sync_subscription_to_panel(db, user, subscription)

        logger.info(f"Admin {admin.id} cancelled subscription for user {user_id}")

        return UpdateSubscriptionResponse(
            success=True,
            message="Subscription cancelled",
            subscription=await _build_subscription_info_async(db, subscription),
        )

    elif request.action == "activate":
        subscription.status = SubscriptionStatus.ACTIVE.value
        if subscription.end_date and subscription.end_date <= datetime.utcnow():
            # Extend by 30 days if expired
            subscription.end_date = datetime.utcnow() + timedelta(days=30)
        await db.commit()
        await db.refresh(subscription)

        # Sync to Remnawave panel
        await _sync_subscription_to_panel(db, user, subscription)

        logger.info(f"Admin {admin.id} activated subscription for user {user_id}")

        return UpdateSubscriptionResponse(
            success=True,
            message="Subscription activated",
            subscription=await _build_subscription_info_async(db, subscription),
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown action: {request.action}",
        )


# === Available Tariffs ===

@router.get("/{user_id}/available-tariffs", response_model=UserAvailableTariffsResponse)
async def get_user_available_tariffs(
    user_id: int,
    include_inactive: bool = Query(False, description="Include inactive tariffs"),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Get list of tariffs available for a specific user.

    Takes into account user's promo group to determine which tariffs are accessible.
    Shows all tariffs with availability flag.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get all tariffs
    from app.database.crud.tariff import get_all_tariffs

    tariffs = await get_all_tariffs(db, include_inactive=include_inactive)

    # Get current subscription tariff
    current_tariff_id = None
    current_tariff_name = None
    if user.subscription and user.subscription.tariff_id:
        current_tariff_id = user.subscription.tariff_id
        if user.subscription.tariff:
            current_tariff_name = user.subscription.tariff.name

    # Build tariff items
    tariff_items = []
    for tariff in tariffs:
        # Check if available for user's promo group
        is_available = tariff.is_available_for_promo_group(user.promo_group_id)
        requires_promo_group = bool(tariff.allowed_promo_groups)

        # Build period prices
        period_prices = []
        if tariff.period_prices:
            for days_str, price_kopeks in sorted(tariff.period_prices.items(), key=lambda x: int(x[0])):
                days = int(days_str)
                period_prices.append(PeriodPriceInfo(
                    days=days,
                    price_kopeks=price_kopeks,
                    price_rubles=price_kopeks / 100,
                ))

        tariff_items.append(UserAvailableTariffItem(
            id=tariff.id,
            name=tariff.name,
            description=tariff.description,
            is_active=tariff.is_active,
            is_trial_available=tariff.is_trial_available,
            traffic_limit_gb=tariff.traffic_limit_gb,
            device_limit=tariff.device_limit,
            tier_level=tariff.tier_level,
            display_order=tariff.display_order,
            period_prices=period_prices,
            is_daily=tariff.is_daily,
            daily_price_kopeks=tariff.daily_price_kopeks,
            custom_days_enabled=tariff.custom_days_enabled,
            price_per_day_kopeks=tariff.price_per_day_kopeks,
            min_days=tariff.min_days,
            max_days=tariff.max_days,
            is_available=is_available,
            requires_promo_group=requires_promo_group,
        ))

    # Sort by display_order, then by tier_level
    tariff_items.sort(key=lambda t: (t.display_order, t.tier_level))

    return UserAvailableTariffsResponse(
        user_id=user.id,
        promo_group_id=user.promo_group_id,
        promo_group_name=user.promo_group.name if user.promo_group else None,
        tariffs=tariff_items,
        total=len(tariff_items),
        current_tariff_id=current_tariff_id,
        current_tariff_name=current_tariff_name,
    )


# === Status Management ===

@router.post("/{user_id}/status", response_model=UpdateUserStatusResponse)
async def update_user_status(
    user_id: int,
    request: UpdateUserStatusRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update user status (active, blocked, deleted)."""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    old_status = user.status
    new_status = request.status.value

    if old_status == new_status:
        return UpdateUserStatusResponse(
            success=True,
            old_status=old_status,
            new_status=new_status,
            message="Status unchanged",
        )

    user.status = new_status
    user.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)

    action = f"{old_status} -> {new_status}"
    if request.reason:
        action += f" (reason: {request.reason})"

    logger.info(f"Admin {admin.id} changed status for user {user_id}: {action}")

    return UpdateUserStatusResponse(
        success=True,
        old_status=old_status,
        new_status=new_status,
        message=f"Status changed from {old_status} to {new_status}",
    )


@router.post("/{user_id}/block", response_model=UpdateUserStatusResponse)
async def block_user(
    user_id: int,
    reason: Optional[str] = None,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Block a user (shortcut for status update)."""
    request = UpdateUserStatusRequest(status=UserStatusEnum.BLOCKED, reason=reason)
    return await update_user_status(user_id, request, admin, db)


@router.post("/{user_id}/unblock", response_model=UpdateUserStatusResponse)
async def unblock_user(
    user_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Unblock a user (shortcut for status update)."""
    request = UpdateUserStatusRequest(status=UserStatusEnum.ACTIVE)
    return await update_user_status(user_id, request, admin, db)


# === Restrictions Management ===

@router.post("/{user_id}/restrictions", response_model=UpdateRestrictionsResponse)
async def update_user_restrictions(
    user_id: int,
    request: UpdateRestrictionsRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update user restrictions (topup, subscription)."""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if request.restriction_topup is not None:
        user.restriction_topup = request.restriction_topup

    if request.restriction_subscription is not None:
        user.restriction_subscription = request.restriction_subscription

    if request.restriction_reason is not None:
        user.restriction_reason = request.restriction_reason

    user.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)

    logger.info(
        f"Admin {admin.id} updated restrictions for user {user_id}: "
        f"topup={user.restriction_topup}, subscription={user.restriction_subscription}"
    )

    return UpdateRestrictionsResponse(
        success=True,
        restriction_topup=user.restriction_topup,
        restriction_subscription=user.restriction_subscription,
        restriction_reason=user.restriction_reason,
        message="Restrictions updated",
    )


# === Promo Group Management ===

@router.post("/{user_id}/promo-group", response_model=UpdatePromoGroupResponse)
async def update_user_promo_group(
    user_id: int,
    request: UpdatePromoGroupRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update user promo group."""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    old_promo_group_id = user.promo_group_id
    new_promo_group_id = request.promo_group_id
    promo_group_name = None

    if new_promo_group_id is not None:
        # Verify promo group exists
        result = await db.execute(
            select(PromoGroup).where(PromoGroup.id == new_promo_group_id)
        )
        promo_group = result.scalar_one_or_none()
        if not promo_group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Promo group not found",
            )
        promo_group_name = promo_group.name

    user.promo_group_id = new_promo_group_id
    user.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)

    logger.info(
        f"Admin {admin.id} changed promo group for user {user_id}: "
        f"{old_promo_group_id} -> {new_promo_group_id}"
    )

    return UpdatePromoGroupResponse(
        success=True,
        old_promo_group_id=old_promo_group_id,
        new_promo_group_id=new_promo_group_id,
        promo_group_name=promo_group_name,
        message="Promo group updated",
    )


# === Delete User ===

@router.delete("/{user_id}", response_model=DeleteUserResponse)
async def delete_user(
    user_id: int,
    request: DeleteUserRequest = DeleteUserRequest(),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Delete a user.

    - **soft_delete=True**: Mark user as deleted (default)
    - **soft_delete=False**: Permanently delete from database
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if request.soft_delete:
        await soft_delete_user(db, user)
        action = "soft deleted"
    else:
        # Hard delete
        await db.delete(user)
        await db.commit()
        action = "permanently deleted"

    reason_text = f" (reason: {request.reason})" if request.reason else ""
    logger.info(f"Admin {admin.id} {action} user {user_id}{reason_text}")

    return DeleteUserResponse(
        success=True,
        message=f"User {action} successfully",
    )


# === User Referrals ===

@router.get("/{user_id}/referrals", response_model=UsersListResponse)
async def get_user_referrals(
    user_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get list of users referred by this user."""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    referrals = await get_referrals(db, user.id)

    # Apply pagination manually
    total = len(referrals)
    referrals = referrals[offset : offset + limit]

    # Get spending stats
    user_ids = [r.id for r in referrals]
    spending_stats = await get_users_spending_stats(db, user_ids) if user_ids else {}

    items = [_build_user_list_item(r, spending_stats) for r in referrals]

    return UsersListResponse(
        users=items,
        total=total,
        offset=offset,
        limit=limit,
    )


# === User Transactions ===

@router.get("/{user_id}/transactions")
async def get_user_transactions(
    user_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    transaction_type: Optional[str] = Query(None),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get user transactions."""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    query = select(Transaction).where(Transaction.user_id == user.id)

    if transaction_type:
        query = query.where(Transaction.type == transaction_type)

    # Get total count
    count_query = select(func.count(Transaction.id)).where(Transaction.user_id == user.id)
    if transaction_type:
        count_query = count_query.where(Transaction.type == transaction_type)
    total = (await db.execute(count_query)).scalar() or 0

    # Get transactions
    query = query.order_by(Transaction.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    transactions = result.scalars().all()

    items = [
        UserTransactionItem(
            id=t.id,
            type=t.type,
            amount_kopeks=t.amount_kopeks,
            amount_rubles=t.amount_kopeks / 100,
            description=t.description,
            payment_method=t.payment_method,
            is_completed=t.is_completed,
            created_at=t.created_at,
        )
        for t in transactions
    ]

    return {
        "transactions": items,
        "total": total,
        "offset": offset,
        "limit": limit,
    }


# === Panel Sync ===

@router.get("/{user_id}/sync/status", response_model=PanelSyncStatusResponse)
async def get_user_sync_status(
    user_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Get sync status between bot and panel for a user.

    Shows differences between bot data and panel data.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Bot data
    bot_sub_status = None
    bot_sub_end_date = None
    bot_traffic_limit = 0
    bot_traffic_used = 0.0
    bot_device_limit = 0
    bot_squads: List[str] = []

    if user.subscription:
        bot_sub_status = user.subscription.status
        bot_sub_end_date = user.subscription.end_date
        bot_traffic_limit = user.subscription.traffic_limit_gb
        bot_traffic_used = user.subscription.traffic_used_gb or 0.0
        bot_device_limit = user.subscription.device_limit or 0
        bot_squads = user.subscription.connected_squads or []

    # Panel data
    panel_found = False
    panel_status = None
    panel_expire_at = None
    panel_traffic_limit = 0.0
    panel_traffic_used = 0.0
    panel_device_limit = 0
    panel_squads: List[str] = []
    differences = []

    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()
        if service.is_configured:
            async with service.get_api_client() as api:
                panel_users = await api.get_user_by_telegram_id(user.telegram_id)
                if panel_users:
                    panel_user = panel_users[0]
                    panel_found = True
                    panel_status = panel_user.status.value if panel_user.status else None
                    panel_expire_at = panel_user.expire_at
                    panel_traffic_limit = panel_user.traffic_limit_bytes / (1024**3) if panel_user.traffic_limit_bytes else 0
                    panel_traffic_used = panel_user.used_traffic_bytes / (1024**3) if panel_user.used_traffic_bytes else 0
                    panel_device_limit = panel_user.hwid_device_limit or 0
                    # Extract squad UUIDs from active_internal_squads
                    panel_squads = [s.get('uuid', '') for s in (panel_user.active_internal_squads or []) if s.get('uuid')]

                    # Check differences
                    if bot_sub_status and panel_status:
                        bot_active = bot_sub_status in ("active", "trial")
                        panel_active = panel_status.upper() == "ACTIVE"
                        if bot_active != panel_active:
                            differences.append(f"Status: bot={bot_sub_status}, panel={panel_status}")

                    if bot_sub_end_date and panel_expire_at:
                        # Convert both to naive UTC for comparison
                        # Bot dates are stored as naive UTC
                        bot_end_utc = bot_sub_end_date.replace(tzinfo=None) if bot_sub_end_date.tzinfo else bot_sub_end_date
                        # Panel dates might be timezone-aware, convert to UTC first
                        if panel_expire_at.tzinfo:
                            from datetime import timezone
                            panel_end_utc = panel_expire_at.astimezone(timezone.utc).replace(tzinfo=None)
                        else:
                            # Panel might return naive datetime in MSK (UTC+3), try both interpretations
                            panel_end_utc = panel_expire_at

                        diff_seconds = abs((bot_end_utc - panel_end_utc).total_seconds())
                        # Allow for timezone offset (3 hours = MSK) and small sync delays
                        # If diff is ~3 hours (10800 sec) +/- 5 min, assume it's timezone issue
                        is_timezone_diff = abs(diff_seconds - 10800) < 300  # 3 hours +/- 5 min
                        if diff_seconds > 3600 and not is_timezone_diff:  # More than 1 hour and not timezone
                            differences.append(f"End date differs by {diff_seconds/3600:.1f} hours")

                    if abs(bot_traffic_limit - panel_traffic_limit) > 1:
                        differences.append(f"Traffic limit: bot={bot_traffic_limit}GB, panel={panel_traffic_limit:.1f}GB")

                    if abs(bot_traffic_used - panel_traffic_used) > 0.5:
                        differences.append(f"Traffic used: bot={bot_traffic_used:.2f}GB, panel={panel_traffic_used:.2f}GB")

                    # Compare device limits
                    if bot_device_limit != panel_device_limit:
                        differences.append(f"Device limit: bot={bot_device_limit}, panel={panel_device_limit}")

                    # Compare squads
                    bot_squads_set = set(bot_squads) if bot_squads else set()
                    panel_squads_set = set(panel_squads) if panel_squads else set()
                    if bot_squads_set != panel_squads_set:
                        only_in_bot = bot_squads_set - panel_squads_set
                        only_in_panel = panel_squads_set - bot_squads_set
                        squad_diff_parts = []
                        if only_in_bot:
                            squad_diff_parts.append(f"only in bot: {len(only_in_bot)}")
                        if only_in_panel:
                            squad_diff_parts.append(f"only in panel: {len(only_in_panel)}")
                        differences.append(f"Squads mismatch ({', '.join(squad_diff_parts)})")

    except Exception as e:
        logger.warning(f"Failed to get panel data for user {user_id}: {e}")
        differences.append(f"Error fetching panel data: {str(e)}")

    return PanelSyncStatusResponse(
        user_id=user.id,
        telegram_id=user.telegram_id,
        remnawave_uuid=user.remnawave_uuid,
        last_sync=user.last_remnawave_sync,
        bot_subscription_status=bot_sub_status,
        bot_subscription_end_date=bot_sub_end_date,
        bot_traffic_limit_gb=bot_traffic_limit,
        bot_traffic_used_gb=bot_traffic_used,
        bot_device_limit=bot_device_limit,
        bot_squads=bot_squads,
        panel_found=panel_found,
        panel_status=panel_status,
        panel_expire_at=panel_expire_at,
        panel_traffic_limit_gb=panel_traffic_limit,
        panel_traffic_used_gb=panel_traffic_used,
        panel_device_limit=panel_device_limit,
        panel_squads=panel_squads,
        has_differences=len(differences) > 0,
        differences=differences,
    )


@router.post("/{user_id}/sync/from-panel", response_model=SyncFromPanelResponse)
async def sync_user_from_panel(
    user_id: int,
    request: SyncFromPanelRequest = SyncFromPanelRequest(),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Sync user data FROM panel TO bot.

    Fetches user data from Remnawave panel and updates local database.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    try:
        from app.services.remnawave_service import RemnaWaveService, RemnaWaveConfigurationError

        service = RemnaWaveService()
        if not service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=service.configuration_error or "Remnawave API not configured",
            )

        changes = {}
        errors = []
        panel_info = None

        async with service.get_api_client() as api:
            # Find user in panel
            panel_users = await api.get_user_by_telegram_id(user.telegram_id)

            if not panel_users:
                return SyncFromPanelResponse(
                    success=False,
                    message="User not found in panel",
                    errors=["No user with this telegram_id found in Remnawave panel"],
                )

            panel_user = panel_users[0]

            # Build panel info
            active_squads = []
            if hasattr(panel_user, 'active_internal_squads') and panel_user.active_internal_squads:
                for squad in panel_user.active_internal_squads:
                    if hasattr(squad, 'uuid'):
                        active_squads.append(squad.uuid)
                    elif isinstance(squad, str):
                        active_squads.append(squad)

            panel_info = PanelUserInfo(
                uuid=panel_user.uuid,
                short_uuid=panel_user.short_uuid,
                username=panel_user.username,
                status=panel_user.status.value if panel_user.status else None,
                expire_at=panel_user.expire_at,
                traffic_limit_gb=panel_user.traffic_limit_bytes / (1024**3) if panel_user.traffic_limit_bytes else 0,
                traffic_used_gb=panel_user.used_traffic_bytes / (1024**3) if panel_user.used_traffic_bytes else 0,
                device_limit=panel_user.hwid_device_limit or 1,
                subscription_url=panel_user.subscription_url,
                active_squads=active_squads,
            )

            # Update remnawave_uuid if different
            if user.remnawave_uuid != panel_user.uuid:
                changes["remnawave_uuid"] = {"old": user.remnawave_uuid, "new": panel_user.uuid}
                user.remnawave_uuid = panel_user.uuid

            # Update subscription if requested
            if request.update_subscription and user.subscription:
                sub = user.subscription

                # Update end date (normalize timezone)
                if panel_user.expire_at:
                    # Convert panel expire_at to naive UTC for storage
                    if panel_user.expire_at.tzinfo:
                        from datetime import timezone
                        panel_expire_utc = panel_user.expire_at.astimezone(timezone.utc).replace(tzinfo=None)
                    else:
                        panel_expire_utc = panel_user.expire_at

                    sub_end_naive = sub.end_date.replace(tzinfo=None) if sub.end_date and sub.end_date.tzinfo else sub.end_date
                    if sub_end_naive != panel_expire_utc:
                        changes["end_date"] = {"old": sub.end_date.isoformat() if sub.end_date else None, "new": panel_expire_utc.isoformat()}
                        sub.end_date = panel_expire_utc

                # Update status
                panel_status_str = panel_user.status.value if panel_user.status else "DISABLED"
                now = datetime.utcnow()
                # Compare with normalized panel expire date
                panel_expire_for_check = panel_expire_utc if panel_user.expire_at else None
                if panel_status_str == "ACTIVE" and panel_expire_for_check and panel_expire_for_check > now:
                    new_status = SubscriptionStatus.ACTIVE.value
                elif panel_expire_for_check and panel_expire_for_check <= now:
                    new_status = SubscriptionStatus.EXPIRED.value
                else:
                    new_status = SubscriptionStatus.DISABLED.value

                if sub.status != new_status:
                    changes["status"] = {"old": sub.status, "new": new_status}
                    sub.status = new_status

                # Update traffic limit
                panel_traffic_limit = int(panel_user.traffic_limit_bytes / (1024**3)) if panel_user.traffic_limit_bytes else 0
                if sub.traffic_limit_gb != panel_traffic_limit:
                    changes["traffic_limit_gb"] = {"old": sub.traffic_limit_gb, "new": panel_traffic_limit}
                    sub.traffic_limit_gb = panel_traffic_limit

                # Update device limit
                panel_device_limit = panel_user.hwid_device_limit or 1
                if sub.device_limit != panel_device_limit:
                    changes["device_limit"] = {"old": sub.device_limit, "new": panel_device_limit}
                    sub.device_limit = panel_device_limit

                # Update connected squads
                if active_squads and sub.connected_squads != active_squads:
                    changes["connected_squads"] = {"old": sub.connected_squads, "new": active_squads}
                    sub.connected_squads = active_squads

                # Update subscription URL
                if panel_user.subscription_url and sub.subscription_url != panel_user.subscription_url:
                    changes["subscription_url"] = {"old": sub.subscription_url, "new": panel_user.subscription_url}
                    sub.subscription_url = panel_user.subscription_url

                # Update short UUID
                if panel_user.short_uuid and sub.remnawave_short_uuid != panel_user.short_uuid:
                    changes["remnawave_short_uuid"] = {"old": sub.remnawave_short_uuid, "new": panel_user.short_uuid}
                    sub.remnawave_short_uuid = panel_user.short_uuid

            # Update traffic usage if requested
            if request.update_traffic and user.subscription:
                panel_traffic_used = panel_user.used_traffic_bytes / (1024**3) if panel_user.used_traffic_bytes else 0
                if abs((user.subscription.traffic_used_gb or 0) - panel_traffic_used) > 0.01:
                    changes["traffic_used_gb"] = {"old": user.subscription.traffic_used_gb, "new": panel_traffic_used}
                    user.subscription.traffic_used_gb = panel_traffic_used

            # Create subscription if missing but user exists in panel
            if request.create_if_missing and not user.subscription and panel_user.expire_at:
                from app.database.crud.subscription import create_paid_subscription

                panel_traffic_limit = int(panel_user.traffic_limit_bytes / (1024**3)) if panel_user.traffic_limit_bytes else 100
                # Normalize panel expire date for calculation
                if panel_user.expire_at.tzinfo:
                    from datetime import timezone
                    panel_expire_naive = panel_user.expire_at.astimezone(timezone.utc).replace(tzinfo=None)
                else:
                    panel_expire_naive = panel_user.expire_at
                days_remaining = max(1, (panel_expire_naive - datetime.utcnow()).days)

                new_sub = await create_paid_subscription(
                    db=db,
                    user_id=user.id,
                    duration_days=days_remaining,
                    traffic_limit_gb=panel_traffic_limit,
                    device_limit=panel_user.hwid_device_limit or 1,
                    connected_squads=active_squads,
                )
                new_sub.remnawave_short_uuid = panel_user.short_uuid
                new_sub.subscription_url = panel_user.subscription_url
                changes["subscription_created"] = True

            # Update last sync time
            user.last_remnawave_sync = datetime.utcnow()
            user.updated_at = datetime.utcnow()

            await db.commit()

        logger.info(f"Admin {admin.id} synced user {user_id} from panel. Changes: {list(changes.keys())}")

        return SyncFromPanelResponse(
            success=True,
            message=f"Synced {len(changes)} changes from panel" if changes else "No changes needed",
            panel_user=panel_info,
            changes=changes,
            errors=errors,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing user {user_id} from panel: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync error: {str(e)}",
        )


@router.post("/{user_id}/sync/to-panel", response_model=SyncToPanelResponse)
async def sync_user_to_panel(
    user_id: int,
    request: SyncToPanelRequest = SyncToPanelRequest(),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Sync user data FROM bot TO panel.

    Sends user/subscription data to Remnawave panel, creating or updating as needed.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no subscription to sync",
        )

    try:
        from app.services.remnawave_service import RemnaWaveService
        from app.external.remnawave_api import UserStatus as PanelUserStatus, TrafficLimitStrategy
        from app.config import settings
        from app.utils.subscription_utils import resolve_hwid_device_limit_for_payload

        service = RemnaWaveService()
        if not service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=service.configuration_error or "Remnawave API not configured",
            )

        sub = user.subscription
        changes = {}
        errors = []
        action = "no_changes"
        panel_uuid = user.remnawave_uuid

        # Prepare data for panel
        is_active = (
            sub.status in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIAL.value)
            and sub.end_date
            and sub.end_date > datetime.utcnow()
        )
        panel_status = PanelUserStatus.ACTIVE if is_active else PanelUserStatus.DISABLED

        # Ensure expire_at is in future for panel
        expire_at = sub.end_date
        if expire_at and expire_at <= datetime.utcnow():
            expire_at = datetime.utcnow() + timedelta(minutes=1)

        username = settings.format_remnawave_username(
            full_name=user.full_name,
            username=user.username,
            telegram_id=user.telegram_id,
        )

        description = settings.format_remnawave_user_description(
            full_name=user.full_name,
            username=user.username,
            telegram_id=user.telegram_id,
        )

        hwid_limit = resolve_hwid_device_limit_for_payload(sub)
        traffic_limit_bytes = sub.traffic_limit_gb * (1024**3) if sub.traffic_limit_gb > 0 else 0

        async with service.get_api_client() as api:
            # Try to find existing user in panel
            if not panel_uuid:
                existing_users = await api.get_user_by_telegram_id(user.telegram_id)
                if existing_users:
                    panel_uuid = existing_users[0].uuid
                    user.remnawave_uuid = panel_uuid
                    changes["remnawave_uuid_discovered"] = panel_uuid

            if panel_uuid:
                # Update existing user
                update_kwargs = {"uuid": panel_uuid}

                if request.update_status:
                    update_kwargs["status"] = panel_status
                    changes["status"] = panel_status.value

                if request.update_expire_date and expire_at:
                    update_kwargs["expire_at"] = expire_at
                    changes["expire_at"] = expire_at.isoformat()

                if request.update_traffic_limit:
                    update_kwargs["traffic_limit_bytes"] = traffic_limit_bytes
                    update_kwargs["traffic_limit_strategy"] = TrafficLimitStrategy.MONTH
                    changes["traffic_limit_gb"] = sub.traffic_limit_gb

                if request.update_squads and sub.connected_squads:
                    update_kwargs["active_internal_squads"] = sub.connected_squads
                    changes["connected_squads"] = sub.connected_squads

                update_kwargs["description"] = description
                if hwid_limit is not None:
                    update_kwargs["hwid_device_limit"] = hwid_limit
                    changes["device_limit"] = hwid_limit

                try:
                    await api.update_user(**update_kwargs)
                    action = "updated"
                except Exception as update_error:
                    if hasattr(update_error, 'status_code') and update_error.status_code == 404:
                        # User not found in panel, create new
                        panel_uuid = None
                    else:
                        raise

            if not panel_uuid and request.create_if_missing:
                # Create new user in panel
                create_kwargs = {
                    "username": username,
                    "expire_at": expire_at or (datetime.utcnow() + timedelta(days=30)),
                    "status": panel_status,
                    "traffic_limit_bytes": traffic_limit_bytes,
                    "traffic_limit_strategy": TrafficLimitStrategy.MONTH,
                    "telegram_id": user.telegram_id,
                    "description": description,
                    "active_internal_squads": sub.connected_squads or [],
                }

                if hwid_limit is not None:
                    create_kwargs["hwid_device_limit"] = hwid_limit

                new_panel_user = await api.create_user(**create_kwargs)
                panel_uuid = new_panel_user.uuid
                user.remnawave_uuid = new_panel_user.uuid
                sub.remnawave_short_uuid = new_panel_user.short_uuid
                sub.subscription_url = new_panel_user.subscription_url

                changes["created_in_panel"] = True
                changes["panel_uuid"] = panel_uuid
                changes["short_uuid"] = new_panel_user.short_uuid
                action = "created"

            # Update last sync time
            user.last_remnawave_sync = datetime.utcnow()
            user.updated_at = datetime.utcnow()

            await db.commit()

        logger.info(f"Admin {admin.id} synced user {user_id} to panel. Action: {action}")

        return SyncToPanelResponse(
            success=True,
            message=f"User {action} in panel" if action != "no_changes" else "No changes needed",
            action=action,
            panel_uuid=panel_uuid,
            changes=changes,
            errors=errors,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing user {user_id} to panel: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync error: {str(e)}",
        )
