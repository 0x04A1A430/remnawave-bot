"""Gift subscription routes for cabinet."""

import asyncio
import re
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.crud.tariff import get_tariff_by_id
from app.database.crud.user import lock_user_for_pricing
from app.database.models import (
    GuestPurchase,
    GuestPurchaseStatus,
    User,
)
from app.services.gift_purchase_service import (
    GiftFeatureDisabledError,
    GiftIdempotencyConflictError,
    GiftInsufficientBalanceError,
    GiftPeriodUnavailableError,
    GiftPriceChangedError,
    GiftPurchaseRestrictedError,
    GiftRecipient,
    GiftTariffUnavailableError,
    is_gift_enabled,
    list_gift_offers,
    purchase_gift_from_balance,
    quote_gift_purchase,
)
from app.services.guest_purchase_service import (
    GuestPurchaseError,
    create_purchase,
    notify_gift_claim_available,
)
from app.services.payment_method_config_service import get_enabled_methods_for_user
from app.utils.cache import RateLimitCache
from app.utils.promo_offer import get_user_active_promo_discount_percent

from ..dependencies import get_cabinet_db, get_current_cabinet_user
from ..schemas.gift import (
    ActivateGiftRequest,
    ActivateGiftResponse,
    GiftConfigPaymentMethod,
    GiftConfigResponse,
    GiftConfigSubOption,
    GiftConfigTariff,
    GiftConfigTariffPeriod,
    GiftPurchaseRequest,
    GiftPurchaseResponse,
    GiftPurchaseStatusResponse,
    PendingGiftResponse,
    ReceivedGiftResponse,
    SentGiftResponse,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/gift', tags=['Cabinet Gift'])

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
_TELEGRAM_RE = re.compile(r'^@?[a-zA-Z][a-zA-Z0-9_]{4,31}$')


@router.get('/config', response_model=GiftConfigResponse)
async def get_gift_config(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get gift subscription configuration: tariffs, payment methods, balance."""
    if not await is_gift_enabled(db):
        return GiftConfigResponse(
            is_enabled=False,
            balance_kopeks=user.balance_kopeks,
        )

    offers = await list_gift_offers(db, buyer=user)
    tariffs: list[GiftConfigTariff] = []
    for offer in offers:
        periods: list[GiftConfigTariffPeriod] = []
        for quote in offer.quotes:
            periods.append(
                GiftConfigTariffPeriod(
                    days=quote.period_days,
                    price_kopeks=quote.final_price_kopeks,
                    price_label=settings.format_price(quote.final_price_kopeks),
                    original_price_kopeks=quote.original_price_kopeks if quote.discount_percent > 0 else None,
                    discount_percent=quote.discount_percent if quote.discount_percent > 0 else None,
                )
            )
        tariffs.append(
            GiftConfigTariff(
                id=offer.tariff_id,
                name=offer.tariff_name,
                description=offer.tariff_description,
                traffic_limit_gb=offer.traffic_limit_gb if offer.traffic_limit_gb is not None else 0,
                device_limit=offer.device_limit,
                periods=periods,
            )
        )

    # Get user's promo group for discount calculation
    promo_group = user.get_primary_promo_group() if hasattr(user, 'get_primary_promo_group') else None
    if promo_group is None:
        promo_group = getattr(user, 'promo_group', None)
    promo_group_name = promo_group.name if promo_group else None

    # Get active promo offer discount
    promo_offer_discount_percent = get_user_active_promo_discount_percent(user)

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
        promo_group_name=promo_group_name,
        active_discount_percent=promo_offer_discount_percent if promo_offer_discount_percent > 0 else None,
        active_discount_expires_at=(
            getattr(user, 'promo_offer_discount_expires_at', None) if promo_offer_discount_percent > 0 else None
        ),
    )


@router.post('/purchase', response_model=GiftPurchaseResponse)
async def create_gift_purchase(
    body: GiftPurchaseRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Create a gift subscription purchase from the cabinet."""
    if not await is_gift_enabled(db):
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

    # Recipient is optional — when omitted, buyer gets a code to share manually
    has_recipient = bool(body.recipient_type and body.recipient_value)

    if has_recipient:
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

    # Pre-check: verify the Telegram username is known — DB first, then Bot API —
    # purely to warn the buyer if it can't be found. Binding happens at claim
    # time (whoever activates the link), so we no longer pre-resolve for delivery.
    recipient_warning: str | None = None
    if has_recipient and body.recipient_type == 'telegram':
        tg_username = body.recipient_value.lstrip('@')
        normalized_username = tg_username.lower()

        # 1) Check local DB — user may already be registered in the bot
        db_result = await db.execute(
            select(User.telegram_id).where(
                func.lower(User.username) == normalized_username,
                User.telegram_id.isnot(None),
            )
        )
        if db_result.scalar_one_or_none() is None:
            # 2) Fall back to Bot API (works for public usernames the bot has seen)
            try:
                from app.bot_factory import create_bot

                async with create_bot() as bot:
                    await asyncio.wait_for(bot.get_chat(chat_id=f'@{tg_username}'), timeout=5.0)
            except Exception:
                recipient_warning = 'telegram_unresolvable'
                logger.warning(
                    'Telegram username not resolvable for gift',
                    username=tg_username,
                    buyer_id=user.id,
                )

    # Shared quote validation
    try:
        quote = await quote_gift_purchase(
            db=db,
            buyer=user,
            tariff_id=body.tariff_id,
            period_days=body.period_days,
        )
    except GiftFeatureDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Gift feature is not enabled',
        ) from exc
    except GiftTariffUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Tariff not found or inactive',
        ) from exc
    except GiftPeriodUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Price is not configured for this period',
        ) from exc

    # Gateway mode: create payment via external provider
    if body.payment_mode == 'gateway':
        if not body.payment_method:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='payment_method is required for gateway mode',
            )

        # Lock user for pricing before calculating quote to prevent concurrent promo offer reuse
        locked_user = await lock_user_for_pricing(db, user.id)
        if not locked_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='User not found',
            )

        try:
            quote = await quote_gift_purchase(
                db=db,
                buyer=locked_user,
                tariff_id=body.tariff_id,
                period_days=body.period_days,
            )
        except GiftFeatureDisabledError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Gift feature is not enabled',
            ) from exc
        except GiftTariffUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Tariff not found or inactive',
            ) from exc
        except GiftPeriodUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Price is not configured for this period',
            ) from exc

        if locked_user.email:
            buyer_contact_type = 'email'
            buyer_contact_value = locked_user.email
        elif locked_user.username:
            buyer_contact_type = 'telegram'
            buyer_contact_value = f'@{locked_user.username}'
        else:
            buyer_contact_type = 'telegram'
            buyer_contact_value = f'id:{locked_user.telegram_id or locked_user.id}'

        tariff = await get_tariff_by_id(db, body.tariff_id)

        purchase_kwargs: dict = (
            {
                'gift_recipient_type': body.recipient_type,
                'gift_recipient_value': body.recipient_value,
                'gift_message': body.gift_message,
            }
            if has_recipient
            else {
                'gift_message': body.gift_message,
            }
        )

        try:
            purchase = await create_purchase(
                db,
                landing=None,
                tariff=tariff,
                period_days=body.period_days,
                amount_kopeks=quote.final_price_kopeks,
                contact_type=buyer_contact_type,
                contact_value=buyer_contact_value,
                payment_method=body.payment_method,
                is_gift=True,
                source='cabinet',
                buyer_user_id=locked_user.id,
                commit=False,
                **purchase_kwargs,
            )
        except GuestPurchaseError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

        # Persist warning so it survives the gateway redirect
        if recipient_warning:
            purchase.recipient_warning = recipient_warning

        # Build return URL for after payment
        cabinet_base = (settings.CABINET_URL or '').rstrip('/')
        return_url = f'{cabinet_base}/gift/result?token={purchase.token[:12]}'

        # Consume promo offer discount under lock before creating external payment
        if quote.consumes_promo_offer and getattr(locked_user, 'promo_offer_discount_percent', 0):
            locked_user.promo_offer_discount_percent = 0
            locked_user.promo_offer_discount_source = None
            locked_user.promo_offer_discount_expires_at = None

        from app.services.payment_service import PaymentService

        # Stars payments need a Bot instance to create invoice links
        bot = None
        if body.payment_method == 'telegram_stars':
            from app.bot_factory import create_bot

            bot = create_bot()

        try:
            payment_service = PaymentService(bot=bot)
            payment_result = await payment_service.create_guest_payment(
                db=db,
                amount_kopeks=quote.final_price_kopeks,
                payment_method=body.payment_method,
                description=f'Gift: {quote.tariff_name} ({body.period_days}d)',
                purchase_token=purchase.token,
                return_url=return_url,
            )
        except Exception:
            await db.rollback()
            raise
        finally:
            if bot:
                await bot.session.close()

        if payment_result is None:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail='Payment provider is unavailable, please try again later',
            )

        payment_url = payment_result.get('payment_url')
        if not payment_url:
            await db.rollback()
            logger.error(
                'Gift payment created but no payment_url returned',
                purchase_token=purchase.token[:5],
                provider=payment_result.get('provider'),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail='Payment provider returned an invalid response',
            )

        await db.commit()
        await db.refresh(purchase)

        return GiftPurchaseResponse(
            status='created',
            purchase_token=purchase.token[:12],
            payment_url=payment_url,
            warning=recipient_warning,
        )

    # Balance mode: delegate to shared purchase_gift_from_balance
    # Generate a fresh server-side UUID idempotency key for legacy cabinet balance requests
    # since the public cabinet schema does not supply a client-provided checkout id.
    idempotency_key = f'cab_{uuid.uuid4().hex}'
    recipient = GiftRecipient(
        recipient_type=body.recipient_type if has_recipient else None,
        recipient_value=body.recipient_value if has_recipient else None,
        gift_message=body.gift_message,
    )

    try:
        result = await purchase_gift_from_balance(
            db=db,
            buyer_id=user.id,
            tariff_id=body.tariff_id,
            period_days=body.period_days,
            expected_price_kopeks=quote.final_price_kopeks,
            idempotency_key=idempotency_key,
            source='cabinet',
            recipient=recipient,
        )
    except GiftFeatureDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Gift feature is not enabled') from exc
    except GiftPurchaseRestrictedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail='Purchases are restricted for this account'
        ) from exc
    except GiftTariffUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Tariff not found or inactive') from exc
    except GiftPeriodUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail='Price is not configured for this period'
        ) from exc
    except GiftInsufficientBalanceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Insufficient balance') from exc
    except GiftPriceChangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail='Price has changed, please try again'
        ) from exc
    except GiftIdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Idempotency conflict') from exc

    # Persist warning on purchase record if unresolvable telegram recipient
    if recipient_warning:
        result.purchase.recipient_warning = recipient_warning
        await db.commit()

    # Unified claimable model: ALL gifts (code-only AND directed) stay in PAID
    # until claimed via the gift link — the buyer shares it, whoever activates it
    # gets the subscription. For a directed gift, best-effort notify the recipient
    # (and a backstop copy to the buyer); never block on notification.
    if has_recipient:
        try:
            await notify_gift_claim_available(
                result.purchase,
                tariff_name=result.quote.tariff_name,
                period_days=body.period_days,
            )
        except Exception:
            logger.warning('Failed to send gift claim notification', purchase_id=result.purchase.id)

    return GiftPurchaseResponse(
        status='ok',
        purchase_token=result.purchase.token[:12],
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
        .options(selectinload(GuestPurchase.tariff))
        .where(
            GuestPurchase.user_id == user.id,
            GuestPurchase.is_gift.is_(True),
            GuestPurchase.status == GuestPurchaseStatus.PENDING_ACTIVATION.value,
        )
        .order_by(GuestPurchase.created_at.desc())
        .limit(100)
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
                token=p.token[:12],
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
    if len(token) >= 64:
        token_filter = GuestPurchase.token == token
    else:
        token_filter = GuestPurchase.token.startswith(token)

    result = await db.execute(select(GuestPurchase).options(selectinload(GuestPurchase.tariff)).where(token_filter))
    purchase = result.scalars().first()
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

    is_code_only = purchase.is_gift and not purchase.gift_recipient_type
    # Unified model: every gift waiting to be claimed (PAID, or PENDING_ACTIVATION
    # mid-claim) is shareable — expose the claim token so the buyer sees the link
    # for directed gifts too, not only code-only ones.
    is_claimable = purchase.is_gift and purchase.status in (
        GuestPurchaseStatus.PAID.value,
        GuestPurchaseStatus.PENDING_ACTIVATION.value,
    )

    return GiftPurchaseStatusResponse(
        status=purchase.status,
        is_gift=True,
        is_code_only=is_code_only,
        is_claimable=is_claimable,
        purchase_token=purchase.token[:12] if is_claimable else None,
        recipient_contact_value=recipient_contact_value,
        gift_message=purchase.gift_message,
        tariff_name=tariff_name,
        period_days=purchase.period_days,
        warning=purchase.recipient_warning,
    )


@router.get('/sent', response_model=list[SentGiftResponse])
async def get_sent_gifts(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get all gifts the current user has sent."""
    result = await db.execute(
        select(GuestPurchase)
        .options(selectinload(GuestPurchase.tariff), selectinload(GuestPurchase.user))
        .where(
            GuestPurchase.buyer_user_id == user.id,
            GuestPurchase.is_gift.is_(True),
        )
        .order_by(GuestPurchase.created_at.desc())
        .limit(100)
    )
    purchases = result.scalars().all()

    sent: list[SentGiftResponse] = []
    for p in purchases:
        activated_by_username = None
        if p.status == GuestPurchaseStatus.DELIVERED.value and p.user and p.user.username:
            activated_by_username = f'@{p.user.username}'

        sent.append(
            SentGiftResponse(
                token=p.token[:12],
                tariff_name=p.tariff.name if p.tariff else None,
                period_days=p.period_days,
                device_limit=p.tariff.device_limit if p.tariff else 1,
                status=p.status,
                gift_recipient_value=p.gift_recipient_value,
                gift_message=p.gift_message,
                activated_by_username=activated_by_username,
                created_at=p.created_at,
            )
        )

    return sent


@router.get('/received', response_model=list[ReceivedGiftResponse])
async def get_received_gifts(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get all gifts the current user has received."""
    result = await db.execute(
        select(GuestPurchase)
        .options(selectinload(GuestPurchase.tariff), selectinload(GuestPurchase.buyer))
        .where(
            GuestPurchase.user_id == user.id,
            GuestPurchase.is_gift.is_(True),
        )
        .order_by(GuestPurchase.created_at.desc())
        .limit(100)
    )
    purchases = result.scalars().all()

    received: list[ReceivedGiftResponse] = []
    for p in purchases:
        sender_display = None
        if p.buyer and p.buyer.username:
            sender_display = f'@{p.buyer.username}'
        elif p.contact_value:
            sender_display = p.contact_value

        received.append(
            ReceivedGiftResponse(
                token=p.token[:12],
                tariff_name=p.tariff.name if p.tariff else None,
                period_days=p.period_days,
                device_limit=p.tariff.device_limit if p.tariff else 1,
                status=p.status,
                sender_display=sender_display,
                gift_message=p.gift_message,
                created_at=p.created_at,
            )
        )

    return received


@router.post('/activate', response_model=ActivateGiftResponse)
async def activate_gift_by_code(
    body: ActivateGiftRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Activate a gift subscription by its code (token)."""
    from app.services.guest_purchase_service import activate_purchase as svc_activate

    # Bug 2 fix: rate limit activation attempts to prevent brute-force token enumeration
    is_limited = await RateLimitCache.is_rate_limited(user.id, 'gift_activate', limit=10, window=60)
    if is_limited:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail='Too many requests')

    code = body.code.strip()
    if code.upper().startswith('GIFT-') or code.upper().startswith('GIFT_'):
        code = code[5:]

    if len(code) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Code too short')

    # Support both full token and prefix-based lookup (displayed codes are truncated)
    if len(code) >= 64:
        # Full token — exact match
        token_filter = GuestPurchase.token == code
    else:
        # Prefix match — for short display codes like GIFT-XXXXXXXXXXXX
        token_filter = GuestPurchase.token.startswith(code)

    result = await db.execute(
        select(GuestPurchase)
        .options(selectinload(GuestPurchase.tariff))
        .where(token_filter, GuestPurchase.is_gift.is_(True))
        .with_for_update()
    )
    purchase = result.scalars().first()

    if purchase is None or not purchase.is_gift:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Gift not found',
        )

    # Bug 1 fix: check ownership BEFORE leaking any status/tariff info
    if purchase.user_id is not None and purchase.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Gift not found',
        )

    # Prevent self-activation: buyer cannot activate their own gift
    if purchase.buyer_user_id is not None and purchase.buyer_user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Cannot activate your own gift',
        )

    if purchase.status == GuestPurchaseStatus.DELIVERED.value:
        return ActivateGiftResponse(
            status='activated',
            tariff_name=purchase.tariff.name if purchase.tariff else None,
            period_days=purchase.period_days,
        )

    # Code-only gifts are in PAID status; directed gifts are in PENDING_ACTIVATION
    activatable_statuses = {
        GuestPurchaseStatus.PENDING_ACTIVATION.value,
        GuestPurchaseStatus.PAID.value,
    }
    if purchase.status not in activatable_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='This gift cannot be activated',
        )

    # For code-only gifts (user_id is None), link the purchase to the activating user
    if purchase.user_id is None:
        purchase.user_id = user.id

    # Transition PAID → PENDING_ACTIVATION so activate_purchase() accepts it
    if purchase.status == GuestPurchaseStatus.PAID.value:
        purchase.status = GuestPurchaseStatus.PENDING_ACTIVATION.value

    await db.flush()

    try:
        await svc_activate(db, purchase.token, skip_notification=True)
    except GuestPurchaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return ActivateGiftResponse(
        status='activated',
        tariff_name=purchase.tariff.name if purchase.tariff else None,
        period_days=purchase.period_days,
    )
