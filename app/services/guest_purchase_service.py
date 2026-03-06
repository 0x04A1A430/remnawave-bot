"""Service for guest (unauthenticated) purchases via landing pages."""

import asyncio
from datetime import UTC, datetime
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.landing import create_guest_purchase
from app.database.crud.subscription import create_paid_subscription, get_subscription_by_user_id, replace_subscription
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
    commit: bool = True,
) -> GuestPurchase:
    """Create a guest purchase record."""
    purchase = await create_guest_purchase(
        db,
        commit=commit,
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
        token_prefix=purchase.token[:5],
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
    result = await db.execute(select(GuestPurchase).where(GuestPurchase.token == purchase_token).with_for_update())
    purchase = result.scalars().first()

    if purchase is None:
        logger.warning('Fulfill called for unknown purchase', token_prefix=purchase_token[:5])
        return None

    if purchase.status != GuestPurchaseStatus.PAID.value:
        logger.warning(
            'Fulfill called for purchase not in PAID status',
            token_prefix=purchase_token[:5],
            current_status=purchase.status,
        )
        return purchase

    try:
        # Determine recipient contact info
        recipient_type, recipient_value = _get_recipient_contact(purchase)

        # Find or create user for the recipient (no commit — stays within our transaction)
        user = await _find_or_create_user(db, recipient_type, recipient_value)

        # Load tariff early — needed for both PENDING_ACTIVATION and DELIVERED paths
        tariff = await get_tariff_by_id(db, purchase.tariff_id)
        if tariff is None:
            logger.error('Tariff not found during fulfillment', tariff_id=purchase.tariff_id)
            raise GuestPurchaseError('Tariff not found', status_code=500)

        # Resolve notification params before any commit (avoids lazy-loading after commit)
        notification_tariff_name = tariff.name
        notification_language = user.language if hasattr(user, 'language') and user.language else 'ru'

        # Check if user already has an active subscription — hold for manual activation
        existing_subscription = await get_subscription_by_user_id(db, user.id)
        if existing_subscription is not None and existing_subscription.is_active:
            purchase.status = GuestPurchaseStatus.PENDING_ACTIVATION.value
            purchase.user_id = user.id
            await db.commit()
            await db.refresh(purchase)

            try:
                await send_guest_notification(
                    purchase,
                    is_pending_activation=True,
                    tariff_name=notification_tariff_name,
                    language=notification_language,
                )
            except Exception:
                logger.exception('Failed to send pending_activation notification', purchase_id=purchase.id)

            logger.info(
                'Guest purchase held for activation (existing subscription)',
                purchase_id=purchase.id,
                token_prefix=purchase_token[:5],
                user_id=user.id,
            )
            return purchase

        # Verify the purchase amount matches the tariff price
        expected_price = tariff.get_price_for_period(purchase.period_days)
        if expected_price is not None and purchase.amount_kopeks != expected_price:
            logger.error(
                'Purchase amount mismatch — aborting fulfillment',
                purchase_id=purchase.id,
                expected_kopeks=expected_price,
                actual_kopeks=purchase.amount_kopeks,
            )
            purchase.status = GuestPurchaseStatus.FAILED.value
            await db.commit()
            return purchase

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

        try:
            await send_guest_notification(
                purchase,
                is_pending_activation=False,
                tariff_name=notification_tariff_name,
                language=notification_language,
            )
        except Exception:
            logger.exception('Failed to send delivery notification', purchase_id=purchase.id)

        logger.info(
            'Guest purchase fulfilled',
            purchase_id=purchase.id,
            token_prefix=purchase_token[:5],
            user_id=user.id,
            recipient_type=recipient_type,
        )

    except GuestPurchaseError:
        raise
    except Exception:
        await db.rollback()
        logger.exception(
            'Failed to fulfill purchase',
            token_prefix=purchase_token[:5],
            purchase_id=purchase.id,
        )
        raise GuestPurchaseError('Purchase fulfillment failed', status_code=500)

    return purchase


def _mask_email(email: str) -> str:
    """Mask email for logging: 'user@example.com' -> 'u***@e***.com'."""
    if not email:
        return '***'
    parts = email.split('@')
    if len(parts) != 2:
        return '***'
    local = parts[0][0] + '***' if parts[0] else '***'
    domain_parts = parts[1].split('.')
    if not domain_parts[0]:
        return f'{local}@***'
    domain = domain_parts[0][0] + '***'
    tld = domain_parts[-1] if len(domain_parts) > 1 else ''
    return f'{local}@{domain}.{tld}'


async def _find_or_create_user(
    db: AsyncSession,
    contact_type: Literal['email', 'telegram'],
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
        try:
            async with db.begin_nested():
                db.add(user)
                await db.flush()
        except IntegrityError:
            result = await db.execute(select(User).where(User.email == contact_value))
            user = result.scalars().first()
            if user:
                return user
            raise
        logger.info(
            'Created new email user for guest purchase', user_id=user.id, email_masked=_mask_email(contact_value)
        )
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
    try:
        async with db.begin_nested():
            db.add(user)
            await db.flush()
    except IntegrityError:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalars().first()
        if user:
            return user
        raise
    logger.info('Created new telegram user for guest purchase', user_id=user.id, username=username)
    return user


def _get_recipient_contact(purchase: GuestPurchase) -> tuple[str, str]:
    """Return (contact_type, contact_value) for the purchase recipient."""
    if purchase.is_gift and purchase.gift_recipient_type and purchase.gift_recipient_value:
        return purchase.gift_recipient_type, purchase.gift_recipient_value
    return purchase.contact_type, purchase.contact_value


async def send_guest_notification(
    purchase: GuestPurchase,
    *,
    is_pending_activation: bool = False,
    tariff_name: str = '',
    language: str = 'ru',
) -> None:
    """Send email notification for guest purchase delivery or activation requirement.

    For telegram contacts, no notification is sent (success page only).
    For gifts, notification goes to the recipient, not the buyer.

    Args:
        purchase: The guest purchase record.
        is_pending_activation: Whether this is a pending activation notification.
        tariff_name: Pre-resolved tariff name (avoids lazy-loading after commit).
        language: User language for email template (avoids lazy-loading user relationship).
    """
    # Lazy imports to avoid circular dependencies (cabinet services -> services -> cabinet)
    from app.cabinet.services.email_service import email_service
    from app.cabinet.services.email_templates import EmailNotificationTemplates
    from app.services.notification_delivery_service import NotificationType

    recipient_type, recipient_email = _get_recipient_contact(purchase)

    if recipient_type != 'email':
        return

    success_page_url = f'{(settings.CABINET_URL or "").rstrip("/")}/buy/success/{purchase.token}'

    context = {
        'tariff_name': tariff_name,
        'period_days': purchase.period_days,
        'success_page_url': success_page_url,
        'subscription_url': purchase.subscription_url or '',
        'is_gift': purchase.is_gift,
        'gift_message': purchase.gift_message,
    }

    if is_pending_activation:
        notification_type = NotificationType.GUEST_ACTIVATION_REQUIRED
    elif purchase.is_gift:
        notification_type = NotificationType.GUEST_GIFT_RECEIVED
    else:
        notification_type = NotificationType.GUEST_SUBSCRIPTION_DELIVERED

    templates = EmailNotificationTemplates()
    template = templates.get_template(notification_type, language, context)
    if not template:
        logger.warning('No email template found for guest notification', notification_type=notification_type.value)
        return

    result = await asyncio.to_thread(
        email_service.send_email,
        to_email=recipient_email,
        subject=template['subject'],
        body_html=template['body_html'],
    )

    if result:
        logger.info(
            'Guest purchase notification sent',
            purchase_id=purchase.id,
            notification_type=notification_type.value,
            recipient_masked=_mask_email(recipient_email),
        )
    else:
        logger.warning(
            'Failed to send guest purchase notification',
            purchase_id=purchase.id,
            notification_type=notification_type.value,
        )


async def activate_purchase(db: AsyncSession, purchase_token: str) -> GuestPurchase:
    """Activate a PENDING_ACTIVATION purchase by replacing or creating a subscription.

    Uses SELECT ... FOR UPDATE to prevent concurrent activation.
    Raises GuestPurchaseError on validation failures.
    Returns the updated purchase (status=DELIVERED).
    """
    result = await db.execute(select(GuestPurchase).where(GuestPurchase.token == purchase_token).with_for_update())
    purchase = result.scalars().first()

    if purchase is None:
        raise GuestPurchaseError('Purchase not found', status_code=404)

    # Idempotent: already delivered
    if purchase.status == GuestPurchaseStatus.DELIVERED.value:
        return purchase

    if purchase.status != GuestPurchaseStatus.PENDING_ACTIVATION.value:
        raise GuestPurchaseError('Purchase is not pending activation', status_code=400)

    tariff = await get_tariff_by_id(db, purchase.tariff_id)
    if tariff is None:
        raise GuestPurchaseError('Tariff not found', status_code=500)

    if not purchase.user_id:
        raise GuestPurchaseError('No user linked to purchase', status_code=500)

    user_result = await db.execute(select(User).where(User.id == purchase.user_id))
    user = user_result.scalars().first()
    if user is None:
        raise GuestPurchaseError('User not found', status_code=500)

    # Resolve notification params before any commit (avoids lazy-loading after commit)
    notification_tariff_name = tariff.name
    notification_language = user.language if hasattr(user, 'language') and user.language else 'ru'

    try:
        existing_subscription = await get_subscription_by_user_id(db, user.id)
        subscription_service = SubscriptionService()

        if existing_subscription is not None:
            subscription = await replace_subscription(
                db,
                existing_subscription,
                duration_days=purchase.period_days,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
                connected_squads=tariff.allowed_squads,
                is_trial=False,
            )
            subscription.tariff_id = tariff.id
        else:
            subscription = await create_paid_subscription(
                db=db,
                user_id=user.id,
                duration_days=purchase.period_days,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
                connected_squads=tariff.allowed_squads,
                tariff_id=tariff.id,
            )

        await subscription_service.create_remnawave_user(db, subscription)
        await db.refresh(subscription)

        purchase.subscription_url = subscription.subscription_url
        purchase.subscription_crypto_link = subscription.subscription_crypto_link
        purchase.status = GuestPurchaseStatus.DELIVERED.value
        purchase.delivered_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(purchase)

        try:
            await send_guest_notification(
                purchase,
                is_pending_activation=False,
                tariff_name=notification_tariff_name,
                language=notification_language,
            )
        except Exception:
            logger.exception('Failed to send delivery notification after activation', purchase_id=purchase.id)

        logger.info(
            'Guest purchase activated',
            purchase_id=purchase.id,
            token_prefix=purchase_token[:5],
            user_id=user.id,
        )

    except GuestPurchaseError:
        raise
    except Exception:
        await db.rollback()
        logger.exception('Failed to activate purchase', purchase_id=purchase.id)
        raise GuestPurchaseError('Activation failed, please try again', status_code=500)

    return purchase
