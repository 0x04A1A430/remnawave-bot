"""Gift subscription routes for cabinet."""

import asyncio
import re
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.landing import get_purchase_by_token
from app.database.crud.system_setting import get_setting_value
from app.database.crud.tariff import get_tariff_by_id
from app.database.crud.transaction import create_transaction, emit_transaction_side_effects
from app.database.crud.user import subtract_user_balance
from app.database.models import GuestPurchase, GuestPurchaseStatus, PaymentMethod, Tariff, TransactionType, User
from app.services.guest_purchase_service import (
    GuestPurchaseError,
    create_purchase,
    fulfill_purchase,
)
from app.services.payment_method_config_service import get_enabled_methods_for_user
from app.utils.cache import RateLimitCache

from ..dependencies import get_cabinet_db, get_current_cabinet_user
from ..schemas.gift import (
    GiftConfigPaymentMethod,
    GiftConfigResponse,
    GiftConfigSubOption,
    GiftConfigTariff,
    GiftConfigTariffPeriod,
    GiftPurchaseRequest,
    GiftPurchaseResponse,
    GiftPurchaseStatusResponse,
    PendingGiftResponse,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/gift', tags=['Cabinet Gift'])

GIFT_ENABLED_KEY = 'CABINET_GIFT_ENABLED'

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
_TELEGRAM_RE = re.compile(r'^@?[a-zA-Z][a-zA-Z0-9_]{4,31}$')


async def _is_gift_enabled(db: AsyncSession) -> bool:
    """Check if the gift feature is enabled via system settings."""
    value = await get_setting_value(db, GIFT_ENABLED_KEY)
    if value is not None:
        return value.lower() == 'true'
    return False


@router.get('/config', response_model=GiftConfigResponse)
async def get_gift_config(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get gift subscription configuration: tariffs, payment methods, balance."""
    enabled = await _is_gift_enabled(db)
    if not enabled:
        return GiftConfigResponse(
            is_enabled=False,
            balance_kopeks=user.balance_kopeks,
        )

    # Load active tariffs
    result = await db.execute(
        select(Tariff).where(Tariff.is_active.is_(True)).order_by(Tariff.display_order, Tariff.id)
    )
    tariffs_db = result.scalars().all()

    tariffs: list[GiftConfigTariff] = []
    for tariff in tariffs_db:
        period_days_list = tariff.get_available_periods()
        periods: list[GiftConfigTariffPeriod] = []
        for days in period_days_list:
            price = tariff.get_price_for_period(days)
            if price is None:
                continue
            periods.append(
                GiftConfigTariffPeriod(
                    days=days,
                    price_kopeks=price,
                    price_label=settings.format_price(price),
                )
            )
        if not periods:
            continue
        tariffs.append(
            GiftConfigTariff(
                id=tariff.id,
                name=tariff.name,
                description=tariff.description,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
                periods=periods,
            )
        )

    # Load payment methods available for this user
    enabled_methods = await get_enabled_methods_for_user(db, user=user)
    payment_methods: list[GiftConfigPaymentMethod] = []
    for method_data in enabled_methods:
        sub_options = None
        raw_options = method_data.get('options')
        if raw_options:
            sub_options = [GiftConfigSubOption(id=opt['id'], name=opt.get('name', opt['id'])) for opt in raw_options]
        payment_methods.append(
            GiftConfigPaymentMethod(
                method_id=method_data['id'],
                display_name=method_data['name'],
                min_amount_kopeks=method_data.get('min_amount_kopeks'),
                max_amount_kopeks=method_data.get('max_amount_kopeks'),
                sub_options=sub_options,
            )
        )

    return GiftConfigResponse(
        is_enabled=True,
        tariffs=tariffs,
        payment_methods=payment_methods,
        balance_kopeks=user.balance_kopeks,
        currency_symbol=getattr(settings, 'CURRENCY_SYMBOL', '\u20bd'),
    )


@router.post('/purchase', response_model=GiftPurchaseResponse)
async def create_gift_purchase(
    body: GiftPurchaseRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Create a gift subscription purchase from the cabinet."""
    enabled = await _is_gift_enabled(db)
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Gift feature is not enabled',
        )

    # Rate limit: 5 gift purchases per 60 seconds per user
    is_limited = await RateLimitCache.is_rate_limited(user.id, 'gift_purchase', limit=5, window=60)
    if is_limited:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail='Too many requests')

    # Check if user has purchase restrictions
    if getattr(user, 'restriction_subscription', False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Purchases are restricted for this account',
        )

    # Validate recipient format
    if body.recipient_type == 'email' and not _EMAIL_RE.match(body.recipient_value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid email format',
        )
    if body.recipient_type == 'telegram' and not _TELEGRAM_RE.match(body.recipient_value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid Telegram username format',
        )

    # Prevent self-gift
    if body.recipient_type == 'telegram':
        normalized_recipient = body.recipient_value.lstrip('@').lower()
        if user.username and user.username.lower() == normalized_recipient:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Cannot gift to yourself',
            )
    elif body.recipient_type == 'email':
        if user.email and user.email.lower() == body.recipient_value.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Cannot gift to yourself',
            )

    # Find tariff and validate period
    tariff = await get_tariff_by_id(db, body.tariff_id)
    if tariff is None or not tariff.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Tariff not found or inactive',
        )

    price_kopeks = tariff.get_price_for_period(body.period_days)
    if price_kopeks is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Price is not configured for this period',
        )

    # Determine buyer contact info
    if user.email:
        buyer_contact_type = 'email'
        buyer_contact_value = user.email
    elif user.username:
        buyer_contact_type = 'telegram'
        buyer_contact_value = f'@{user.username}'
    else:
        buyer_contact_type = 'telegram'
        buyer_contact_value = f'id:{user.telegram_id or user.id}'

    # Gateway mode: stub for future implementation
    if body.payment_mode == 'gateway':
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail='Gateway payment for gifts is not yet supported',
        )

    # Balance mode
    if user.balance_kopeks < price_kopeks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Insufficient balance',
        )

    # Pre-check: try to resolve Telegram username via Bot API
    # Placed after validation gates to prevent zero-cost enumeration.
    # The resolved ID is passed to fulfill_purchase to avoid a duplicate API call.
    recipient_warning: str | None = None
    pre_resolved_telegram_id: int | None = None
    if body.recipient_type == 'telegram':
        tg_username = body.recipient_value.lstrip('@')
        try:
            from aiogram import Bot

            async with Bot(token=settings.BOT_TOKEN) as bot:
                chat = await asyncio.wait_for(bot.get_chat(chat_id=f'@{tg_username}'), timeout=5.0)
                pre_resolved_telegram_id = chat.id
        except Exception:
            recipient_warning = 'telegram_unresolvable'
            logger.warning(
                'Telegram username not resolvable for gift',
                username=tg_username,
                buyer_id=user.id,
            )

    # Create purchase record
    try:
        purchase = await create_purchase(
            db,
            landing=None,
            tariff=tariff,
            period_days=body.period_days,
            amount_kopeks=price_kopeks,
            contact_type=buyer_contact_type,
            contact_value=buyer_contact_value,
            payment_method='balance',
            is_gift=True,
            gift_recipient_type=body.recipient_type,
            gift_recipient_value=body.recipient_value,
            gift_message=body.gift_message,
            source='cabinet',
            buyer_user_id=user.id,
            commit=False,
        )
    except GuestPurchaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    # Subtract balance
    balance_ok = await subtract_user_balance(
        db,
        user,
        price_kopeks,
        description=f'Gift: {tariff.name} ({body.period_days}d)',
        create_transaction=False,
    )
    if not balance_ok:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Insufficient balance',
        )

    # Create transaction record
    transaction = await create_transaction(
        db,
        user_id=user.id,
        type=TransactionType.GIFT_PAYMENT,
        amount_kopeks=price_kopeks,
        description=f'Gift: {tariff.name} ({body.period_days}d) -> {body.recipient_value}',
        payment_method=PaymentMethod.BALANCE,
        commit=False,
    )

    # Mark purchase as paid
    purchase.status = GuestPurchaseStatus.PAID.value
    purchase.paid_at = datetime.now(UTC)

    await db.commit()

    # Emit deferred side-effects after atomic commit
    await emit_transaction_side_effects(
        db,
        transaction,
        amount_kopeks=price_kopeks,
        user_id=user.id,
        type=TransactionType.GIFT_PAYMENT,
        payment_method=PaymentMethod.BALANCE,
        description=f'Gift: {tariff.name} ({body.period_days}d) -> {body.recipient_value}',
    )

    # Capture token before fulfill_purchase — session state may change after rollback inside fulfill
    purchase_token = purchase.token

    # Fulfill the purchase (find/create recipient user, create subscription, notify)
    try:
        await fulfill_purchase(db, purchase_token, pre_resolved_telegram_id=pre_resolved_telegram_id)
    except Exception:
        logger.exception(
            'Gift purchase fulfillment failed (purchase is paid, will retry)',
            purchase_id=purchase.id,
        )

    return GiftPurchaseResponse(
        status='ok',
        purchase_token=purchase_token,
        warning=recipient_warning,
    )


@router.get('/pending', response_model=list[PendingGiftResponse])
async def get_pending_gifts(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get pending gift purchases that the current user can activate."""
    result = await db.execute(
        select(GuestPurchase)
        .where(
            GuestPurchase.user_id == user.id,
            GuestPurchase.is_gift.is_(True),
            GuestPurchase.status == GuestPurchaseStatus.PENDING_ACTIVATION.value,
        )
        .order_by(GuestPurchase.created_at.desc())
    )
    purchases = result.scalars().all()

    pending: list[PendingGiftResponse] = []
    for p in purchases:
        # Determine sender display name
        sender_display = None
        if p.contact_value:
            sender_display = p.contact_value

        pending.append(
            PendingGiftResponse(
                token=p.token,
                tariff_name=p.tariff.name if p.tariff else None,
                period_days=p.period_days,
                gift_message=p.gift_message,
                sender_display=sender_display,
                created_at=p.created_at,
            )
        )

    return pending


@router.get('/purchase/{token}', response_model=GiftPurchaseStatusResponse)
async def get_gift_purchase_status(
    token: str,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get the status of a cabinet gift purchase."""
    purchase = await get_purchase_by_token(db, token)
    if purchase is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Purchase not found',
        )

    # Uniform 404 prevents token existence oracle
    if purchase.buyer_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Purchase not found',
        )

    tariff_name = purchase.tariff.name if purchase.tariff else None

    recipient_contact_value = None
    if purchase.gift_recipient_value:
        recipient_contact_value = purchase.gift_recipient_value

    return GiftPurchaseStatusResponse(
        status=purchase.status,
        is_gift=True,
        recipient_contact_value=recipient_contact_value,
        gift_message=purchase.gift_message,
        tariff_name=tariff_name,
        period_days=purchase.period_days,
    )
