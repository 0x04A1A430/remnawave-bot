"""Service for guest (unauthenticated) purchases via landing pages."""

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.landing import create_guest_purchase
from app.database.crud.subscription import create_paid_subscription
from app.database.crud.tariff import get_tariff_by_id
from app.database.models import GuestPurchase, GuestPurchaseStatus, LandingPage, Tariff, User
from app.services.subscription_service import SubscriptionService


logger = structlog.get_logger(__name__)


class GuestPurchaseError(Exception):
    """Domain error for guest purchase operations."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def validate_and_calculate(
    db: AsyncSession,
    landing: LandingPage,
    tariff_id: int,
    period_days: int,
) -> tuple[Tariff, int]:
    """Validate tariff/period against landing config and return (tariff, price_kopeks).

    Raises:
        GuestPurchaseError: If tariff or period is not allowed or price is unavailable.
    """
    allowed_ids = landing.allowed_tariff_ids or []
    if tariff_id not in allowed_ids:
        raise GuestPurchaseError('Tariff is not available on this landing page')

    tariff = await get_tariff_by_id(db, tariff_id)
    if tariff is None or not tariff.is_active:
        raise GuestPurchaseError('Tariff not found or inactive')

    # Check period against landing-level override (if set)
    allowed_periods = landing.allowed_periods or {}
    if allowed_periods:
        tariff_periods_override = allowed_periods.get(str(tariff_id))
        if tariff_periods_override is not None:
            if period_days not in tariff_periods_override:
                raise GuestPurchaseError('Period is not available for this tariff on this landing page')
        else:
            # No override for this tariff -> all tariff periods allowed
            available = tariff.get_available_periods()
            if period_days not in available:
                raise GuestPurchaseError('Period is not available for this tariff')
    else:
        # No overrides at all -> use tariff's own periods
        available = tariff.get_available_periods()
        if period_days not in available:
            raise GuestPurchaseError('Period is not available for this tariff')

    price_kopeks = tariff.get_price_for_period(period_days)
    if price_kopeks is None:
        raise GuestPurchaseError('Price is not configured for this period')

    return tariff, price_kopeks


async def create_purchase(
    db: AsyncSession,
    landing: LandingPage,
    tariff: Tariff,
    period_days: int,
    amount_kopeks: int,
    contact_type: str,
    contact_value: str,
    payment_method: str,
    is_gift: bool = False,
    gift_recipient_type: str | None = None,
    gift_recipient_value: str | None = None,
    gift_message: str | None = None,
) -> GuestPurchase:
    """Create a guest purchase record."""
    purchase = await create_guest_purchase(
        db,
        landing_id=landing.id,
        tariff_id=tariff.id,
        period_days=period_days,
        amount_kopeks=amount_kopeks,
        contact_type=contact_type,
        contact_value=contact_value,
        payment_method=payment_method,
        is_gift=is_gift,
        gift_recipient_type=gift_recipient_type,
        gift_recipient_value=gift_recipient_value,
        gift_message=gift_message,
        status=GuestPurchaseStatus.PENDING.value,
    )

    logger.info(
        'Guest purchase created',
        purchase_id=purchase.id,
        token_prefix=purchase.token[:8],
        landing_slug=landing.slug,
        tariff_id=tariff.id,
        period_days=period_days,
        amount_kopeks=amount_kopeks,
        is_gift=is_gift,
    )

    return purchase


async def fulfill_purchase(db: AsyncSession, purchase_token: str) -> GuestPurchase | None:
    """After payment: find/create user, create subscription, send notification.

    Uses SELECT ... FOR UPDATE to prevent concurrent fulfillment of the same purchase.
    All operations happen within a single transaction — commit at the end.
    Returns the updated purchase or None if not found.
    """
    result = await db.execute(
        select(GuestPurchase)
        .where(GuestPurchase.token == purchase_token)
        .with_for_update()
    )
    purchase = result.scalars().first()

    if purchase is None:
        logger.warning('Fulfill called for unknown purchase', token_prefix=purchase_token[:8])
        return None

    if purchase.status != GuestPurchaseStatus.PAID.value:
        logger.warning(
            'Fulfill called for purchase not in PAID status',
            token_prefix=purchase_token[:8],
            current_status=purchase.status,
        )
        return purchase

    try:
        # Determine recipient contact info
        if purchase.is_gift and purchase.gift_recipient_type and purchase.gift_recipient_value:
            recipient_type = purchase.gift_recipient_type
            recipient_value = purchase.gift_recipient_value
        else:
            recipient_type = purchase.contact_type
            recipient_value = purchase.contact_value

        # Find or create user for the recipient (no commit — stays within our transaction)
        user = await _find_or_create_user(db, recipient_type, recipient_value)

        # Create local subscription and provision in RemnaWave
        tariff = await get_tariff_by_id(db, purchase.tariff_id)
        if tariff is None:
            logger.error('Tariff not found during fulfillment', tariff_id=purchase.tariff_id)
            raise GuestPurchaseError('Tariff not found', status_code=500)

        # Verify the purchase amount matches the tariff price
        expected_price = tariff.get_price_for_period(purchase.period_days)
        if expected_price is not None and purchase.amount_kopeks != expected_price:
            logger.error(
                'Purchase amount mismatch',
                purchase_id=purchase.id,
                expected_kopeks=expected_price,
                actual_kopeks=purchase.amount_kopeks,
            )

        subscription = await create_paid_subscription(
            db=db,
            user_id=user.id,
            duration_days=purchase.period_days,
            traffic_limit_gb=tariff.traffic_limit_gb,
            device_limit=tariff.device_limit,
            connected_squads=tariff.allowed_squads,
            tariff_id=tariff.id,
        )

        subscription_service = SubscriptionService()
        await subscription_service.create_remnawave_user(db, subscription)
        await db.refresh(subscription)

        purchase.subscription_url = subscription.subscription_url
        purchase.subscription_crypto_link = subscription.subscription_crypto_link

        # Update purchase directly (already locked — no need to re-fetch)
        purchase.status = GuestPurchaseStatus.DELIVERED.value
        purchase.user_id = user.id
        purchase.delivered_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(purchase)

        logger.info(
            'Guest purchase fulfilled',
            purchase_id=purchase.id,
            token_prefix=purchase_token[:8],
            user_id=user.id,
            recipient_type=recipient_type,
        )

    except Exception:
        await db.rollback()
        logger.exception(
            'Failed to fulfill purchase',
            token_prefix=purchase_token[:8],
            purchase_id=purchase.id,
        )
        raise

    return purchase


def _mask_email(email: str) -> str:
    """Mask email for logging: 'user@example.com' -> 'u***@e***.com'."""
    parts = email.split('@')
    if len(parts) != 2:
        return '***'
    local = parts[0][0] + '***' if len(parts[0]) > 0 else '***'
    domain_parts = parts[1].split('.')
    domain = domain_parts[0][0] + '***' if len(domain_parts[0]) > 0 else '***'
    tld = domain_parts[-1] if len(domain_parts) > 1 else ''
    return f'{local}@{domain}.{tld}'


async def _find_or_create_user(
    db: AsyncSession,
    contact_type: str,
    contact_value: str,
) -> User:
    """Find user by email/telegram username or create a new one.

    For email contacts: looks up by User.email
    For telegram contacts: looks up by User.username (without leading @)

    NOTE: Does NOT commit — caller is responsible for committing the transaction.
    This preserves FOR UPDATE locks held by the caller.
    """
    if contact_type == 'email':
        result = await db.execute(select(User).where(User.email == contact_value))
        user = result.scalars().first()
        if user:
            return user

        # Create new email-only user
        user = User(
            auth_type='email',
            email=contact_value,
            email_verified=False,
        )
        db.add(user)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            result = await db.execute(select(User).where(User.email == contact_value))
            user = result.scalars().first()
            if user:
                return user
            raise
        logger.info('Created new email user for guest purchase', user_id=user.id, email_masked=_mask_email(contact_value))
        return user

    # contact_type == 'telegram'
    username = contact_value.lstrip('@').lower()
    result = await db.execute(
        select(User).where(User.username == username),
    )
    user = result.scalars().first()
    if user:
        return user

    # Create new telegram user (without telegram_id — will be linked later)
    user = User(
        auth_type='telegram',
        username=username,
    )
    db.add(user)
    await db.flush()
    logger.info('Created new telegram user for guest purchase', user_id=user.id, username=username)
    return user
