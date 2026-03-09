"""Gift subscription routes for cabinet."""

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
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.database.models import GuestPurchaseStatus, PaymentMethod, Tariff, TransactionType, User
from app.services.guest_purchase_service import (
    GuestPurchaseError,
    create_purchase,
    fulfill_purchase,
)
from app.services.payment_method_config_service import get_enabled_methods_for_user

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
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/gift', tags=['Cabinet Gift'])

GIFT_ENABLED_KEY = 'CABINET_GIFT_ENABLED'

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
_TELEGRAM_RE = re.compile(r'^@?[a-zA-Z][a-zA-Z0-9_]{4,31}$')


def _period_label(days: int) -> str:
    """Human-readable label for a period in days."""
    if days == 1:
        return '1 day'
    if days <= 6:
        return f'{days} days'
    if days == 7:
        return '1 week'
    if days == 14:
        return '2 weeks'
    if days == 30:
        return '1 month'
    if days == 60:
        return '2 months'
    if days == 90:
        return '3 months'
    if days == 180:
        return '6 months'
    if days == 365:
        return '1 year'
    months = days // 30
    remainder = days % 30
    if months > 0 and remainder == 0:
        return f'{months} mo.'
    if months > 0:
        return f'{months} mo. + {remainder} d.'
    return f'{days} days'


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
    await create_transaction(
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

    # Fulfill the purchase (find/create recipient user, create subscription, notify)
    try:
        await fulfill_purchase(db, purchase.token)
    except Exception:
        logger.exception(
            'Gift purchase fulfillment failed (purchase is paid, will retry)',
            purchase_id=purchase.id,
        )

    return GiftPurchaseResponse(
        status='ok',
        purchase_token=purchase.token,
    )


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

    # Only the buyer can view this
    if purchase.buyer_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Access denied',
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
