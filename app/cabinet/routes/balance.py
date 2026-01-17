"""Balance and payment routes for cabinet."""

import logging
import math
import time
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.database.models import User, Transaction, PaymentMethod
from app.config import settings
from app.services.yookassa_service import YooKassaService
from app.external.cryptobot import CryptoBotService
from app.database.crud.user import get_user_by_id
from app.services.payment_service import PaymentService

from ..dependencies import get_cabinet_db, get_current_cabinet_user
from ..schemas.balance import (
    BalanceResponse,
    TransactionResponse,
    TransactionListResponse,
    PaymentMethodResponse,
    TopUpRequest,
    TopUpResponse,
    StarsInvoiceRequest,
    StarsInvoiceResponse,
    PendingPaymentResponse,
    PendingPaymentListResponse,
    ManualCheckResponse,
)
from app.services.payment_verification_service import (
    list_recent_pending_payments,
    get_payment_record,
    run_manual_check,
    SUPPORTED_MANUAL_CHECK_METHODS,
    method_display_name,
    PendingPayment,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/balance", tags=["Cabinet Balance"])


@router.get("", response_model=BalanceResponse)
async def get_balance(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get current user's balance."""
    # Reload user from current session to get fresh data
    # (user object is from different session in get_current_cabinet_user)
    fresh_user = await get_user_by_id(db, user.id)
    if not fresh_user:
        raise HTTPException(status_code=404, detail="User not found")

    return BalanceResponse(
        balance_kopeks=fresh_user.balance_kopeks,
        balance_rubles=fresh_user.balance_kopeks / 100,
    )


@router.get("/transactions", response_model=TransactionListResponse)
async def get_transactions(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    type: Optional[str] = Query(None, description="Filter by transaction type"),
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get transaction history."""
    # Base query
    query = select(Transaction).where(Transaction.user_id == user.id)

    # Filter by type
    if type:
        query = query.where(Transaction.type == type)

    # Get total count
    count_query = select(func.count()).select_from(Transaction).where(Transaction.user_id == user.id)
    if type:
        count_query = count_query.where(Transaction.type == type)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    offset = (page - 1) * per_page
    query = query.order_by(desc(Transaction.created_at)).offset(offset).limit(per_page)

    result = await db.execute(query)
    transactions = result.scalars().all()

    items = []
    for t in transactions:
        # Determine sign based on transaction type
        # Credits (positive): DEPOSIT, REFERRAL_REWARD, REFUND, POLL_REWARD
        # Debits (negative): SUBSCRIPTION_PAYMENT, WITHDRAWAL
        is_debit = t.type in ['subscription_payment', 'withdrawal']
        amount_kopeks = -abs(t.amount_kopeks) if is_debit else abs(t.amount_kopeks)

        items.append(TransactionResponse(
            id=t.id,
            type=t.type,
            amount_kopeks=amount_kopeks,
            amount_rubles=amount_kopeks / 100,
            description=t.description,
            payment_method=t.payment_method,
            is_completed=t.is_completed,
            created_at=t.created_at,
            completed_at=t.completed_at,
        ))

    pages = math.ceil(total / per_page) if total > 0 else 1

    return TransactionListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/payment-methods", response_model=List[PaymentMethodResponse])
async def get_payment_methods():
    """Get available payment methods."""
    methods = []

    # YooKassa - with card and SBP options
    if settings.is_yookassa_enabled():
        methods.append(PaymentMethodResponse(
            id="yookassa",
            name="YooKassa",
            description="Pay via YooKassa",
            min_amount_kopeks=settings.YOOKASSA_MIN_AMOUNT_KOPEKS,
            max_amount_kopeks=settings.YOOKASSA_MAX_AMOUNT_KOPEKS,
            is_available=True,
            options=[
                {"id": "card", "name": "💳 Карта", "description": "Банковская карта"},
                {"id": "sbp", "name": "🏦 СБП", "description": "Система быстрых платежей (QR)"},
            ],
        ))

    # CryptoBot
    if settings.is_cryptobot_enabled():
        methods.append(PaymentMethodResponse(
            id="cryptobot",
            name="CryptoBot",
            description="Pay with cryptocurrency via CryptoBot",
            min_amount_kopeks=1000,
            max_amount_kopeks=10000000,
            is_available=True,
        ))

    # Telegram Stars
    if settings.TELEGRAM_STARS_ENABLED:
        methods.append(PaymentMethodResponse(
            id="telegram_stars",
            name="Telegram Stars",
            description="Pay with Telegram Stars",
            min_amount_kopeks=100,
            max_amount_kopeks=1000000,
            is_available=True,
        ))

    # Heleket
    if settings.is_heleket_enabled():
        methods.append(PaymentMethodResponse(
            id="heleket",
            name="Heleket Crypto",
            description="Pay with cryptocurrency via Heleket",
            min_amount_kopeks=1000,
            max_amount_kopeks=10000000,
            is_available=True,
        ))

    # MulenPay
    if settings.is_mulenpay_enabled():
        methods.append(PaymentMethodResponse(
            id="mulenpay",
            name=settings.get_mulenpay_display_name(),
            description="MulenPay payment",
            min_amount_kopeks=settings.MULENPAY_MIN_AMOUNT_KOPEKS,
            max_amount_kopeks=settings.MULENPAY_MAX_AMOUNT_KOPEKS,
            is_available=True,
        ))

    # PAL24 - add options for card/sbp
    if settings.is_pal24_enabled():
        methods.append(PaymentMethodResponse(
            id="pal24",
            name="PAL24",
            description="Pay via PAL24",
            min_amount_kopeks=settings.PAL24_MIN_AMOUNT_KOPEKS,
            max_amount_kopeks=settings.PAL24_MAX_AMOUNT_KOPEKS,
            is_available=True,
            options=[
                {"id": "sbp", "name": "🏦 СБП", "description": "Система быстрых платежей"},
                {"id": "card", "name": "💳 Карта", "description": "Банковская карта"},
            ],
        ))

    # Platega - add options for different payment methods
    if settings.is_platega_enabled():
        platega_methods = settings.get_platega_active_methods()
        definitions = settings.get_platega_method_definitions()
        platega_options = []
        for method_code in platega_methods:
            info = definitions.get(method_code, {})
            platega_options.append({
                "id": str(method_code),
                "name": info.get("title") or info.get("name") or f"Platega {method_code}",
                "description": info.get("description") or info.get("name") or "",
            })

        methods.append(PaymentMethodResponse(
            id="platega",
            name=settings.get_platega_display_name(),
            description="Pay via Platega",
            min_amount_kopeks=settings.PLATEGA_MIN_AMOUNT_KOPEKS,
            max_amount_kopeks=settings.PLATEGA_MAX_AMOUNT_KOPEKS,
            is_available=True,
            options=platega_options if platega_options else None,
        ))

    # Wata
    if settings.is_wata_enabled():
        methods.append(PaymentMethodResponse(
            id="wata",
            name="Wata",
            description="Pay via Wata",
            min_amount_kopeks=settings.WATA_MIN_AMOUNT_KOPEKS,
            max_amount_kopeks=settings.WATA_MAX_AMOUNT_KOPEKS,
            is_available=True,
        ))

    # CloudPayments
    if settings.is_cloudpayments_enabled():
        methods.append(PaymentMethodResponse(
            id="cloudpayments",
            name="CloudPayments",
            description="Pay with bank card via CloudPayments",
            min_amount_kopeks=settings.CLOUDPAYMENTS_MIN_AMOUNT_KOPEKS,
            max_amount_kopeks=settings.CLOUDPAYMENTS_MAX_AMOUNT_KOPEKS,
            is_available=True,
        ))

    # FreeKassa
    if settings.is_freekassa_enabled():
        methods.append(PaymentMethodResponse(
            id="freekassa",
            name=settings.get_freekassa_display_name(),
            description="Pay via FreeKassa",
            min_amount_kopeks=settings.FREEKASSA_MIN_AMOUNT_KOPEKS,
            max_amount_kopeks=settings.FREEKASSA_MAX_AMOUNT_KOPEKS,
            is_available=True,
        ))

    return methods


@router.post("/stars-invoice", response_model=StarsInvoiceResponse)
async def create_stars_invoice(
    request: StarsInvoiceRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Создать Telegram Stars invoice для пополнения баланса.
    Используется в Telegram Mini App для прямой оплаты Stars.
    """
    if not settings.TELEGRAM_STARS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram Stars payments are not enabled",
        )

    # Validate amount
    if request.amount_kopeks < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Minimum amount is 1.00 RUB",
        )

    if request.amount_kopeks > 1000000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum amount is 10,000.00 RUB",
        )

    # Calculate Stars amount
    try:
        amount_rubles = request.amount_kopeks / 100
        stars_amount = settings.rubles_to_stars(amount_rubles)

        if stars_amount <= 0:
            stars_amount = 1
    except Exception as e:
        logger.error(f"Error calculating Stars amount: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to calculate Stars amount",
        )

    # Create payload for tracking payment
    payload = f"balance_topup_{user.id}_{request.amount_kopeks}_{int(time.time())}"

    # Create invoice through Telegram Bot API
    try:
        bot_token = settings.BOT_TOKEN
        api_url = f"https://api.telegram.org/bot{bot_token}/createInvoiceLink"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                api_url,
                json={
                    "title": "Пополнение баланса VPN",
                    "description": f"Пополнение баланса на {amount_rubles:.2f} ₽ ({stars_amount} ⭐)",
                    "payload": payload,
                    "provider_token": "",  # Empty for Stars
                    "currency": "XTR",
                    "prices": [{"label": "Пополнение баланса", "amount": stars_amount}],
                },
            )

            result = response.json()

            if not result.get("ok"):
                logger.error(f"Telegram API error: {result}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create Stars invoice",
                )

            invoice_url = result["result"]
            logger.info(
                f"Created Stars invoice for balance top-up: user={user.id}, "
                f"amount={request.amount_kopeks} kopeks, stars={stars_amount}"
            )

            return StarsInvoiceResponse(
                invoice_url=invoice_url,
                stars_amount=stars_amount,
                amount_kopeks=request.amount_kopeks,
            )

    except httpx.HTTPError as e:
        logger.error(f"HTTP error creating Stars invoice: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to connect to Telegram API",
        )


@router.post("/topup", response_model=TopUpResponse)
async def create_topup(
    request: TopUpRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Create payment for balance top-up."""
    # Validate payment method
    methods = await get_payment_methods()
    method = next((m for m in methods if m.id == request.payment_method), None)

    if not method or not method.is_available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unavailable payment method",
        )

    # Validate amount
    if request.amount_kopeks < method.min_amount_kopeks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Minimum amount is {method.min_amount_kopeks / 100:.2f} RUB",
        )

    if request.amount_kopeks > method.max_amount_kopeks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum amount is {method.max_amount_kopeks / 100:.2f} RUB",
        )

    amount_rubles = request.amount_kopeks / 100
    payment_url = None
    payment_id = None

    try:
        if request.payment_method == "yookassa":
            yookassa_service = YooKassaService()
            yookassa_metadata = {
                "user_id": str(user.id),
                "user_telegram_id": str(user.telegram_id) if user.telegram_id else "",
                "user_username": user.username or "",
                "amount_kopeks": str(request.amount_kopeks),
                "type": "balance_topup",
                "source": "cabinet",
            }

            # Use payment_option to select card or sbp (default: card)
            option = (request.payment_option or "").strip().lower()
            if option == "sbp":
                # Create SBP payment with QR code
                result = await yookassa_service.create_sbp_payment(
                    amount=amount_rubles,
                    currency="RUB",
                    description=f"Пополнение баланса на {amount_rubles:.2f} ₽",
                    metadata=yookassa_metadata,
                )
            else:
                # Default: card payment
                result = await yookassa_service.create_payment(
                    amount=amount_rubles,
                    currency="RUB",
                    description=f"Пополнение баланса на {amount_rubles:.2f} ₽",
                    metadata=yookassa_metadata,
                )

            if result and not result.get("error"):
                payment_url = result.get("confirmation_url")
                payment_id = result.get("id")
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create YooKassa payment",
                )

        elif request.payment_method == "cryptobot":
            cryptobot_service = CryptoBotService()
            # Convert RUB to USDT (approximate)
            usdt_amount = amount_rubles / 100  # Approximate rate
            result = await cryptobot_service.create_invoice(
                amount=usdt_amount,
                asset="USDT",
                description=f"Balance top-up {amount_rubles:.2f} RUB",
                payload=f"cabinet_topup_{user.id}_{request.amount_kopeks}",
            )
            if result:
                # Priority: web_app for desktop/browser, mini_app for mobile, bot as fallback
                payment_url = (
                    result.get("web_app_invoice_url")
                    or result.get("mini_app_invoice_url")
                    or result.get("bot_invoice_url")
                    or result.get("pay_url")
                )
                payment_id = str(result.get("invoice_id"))
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create CryptoBot invoice",
                )

        elif request.payment_method == "telegram_stars":
            # Telegram Stars payments require bot interaction
            bot_username = settings.get_bot_username() or "bot"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Telegram Stars payments are only available through the bot. Please use @{bot_username}",
            )

        elif request.payment_method == "platega":
            if not settings.is_platega_enabled():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Platega payment method is unavailable",
                )

            active_methods = settings.get_platega_active_methods()
            if not active_methods:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No Platega payment methods configured",
                )

            # Use payment_option if provided, otherwise use first active method
            method_option = request.payment_option or str(active_methods[0])
            try:
                method_code = int(str(method_option).strip())
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid Platega payment option",
                )

            if method_code not in active_methods:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Selected Platega method is unavailable",
                )

            payment_service = PaymentService()
            result = await payment_service.create_platega_payment(
                db=db,
                user_id=user.id,
                amount_kopeks=request.amount_kopeks,
                description=settings.get_balance_payment_description(request.amount_kopeks, telegram_user_id=user.telegram_id),
                language=getattr(user, 'language', None) or settings.DEFAULT_LANGUAGE,
                payment_method_code=method_code,
            )

            if result and result.get("redirect_url"):
                payment_url = result.get("redirect_url")
                payment_id = result.get("transaction_id") or str(result.get("local_payment_id", "pending"))
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create Platega payment",
                )

        elif request.payment_method == "heleket":
            if not settings.is_heleket_enabled():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Heleket payment method is unavailable",
                )

            payment_service = PaymentService()
            result = await payment_service.create_heleket_payment(
                db=db,
                user_id=user.id,
                amount_kopeks=request.amount_kopeks,
                description=settings.get_balance_payment_description(request.amount_kopeks),
                language=getattr(user, 'language', None) or settings.DEFAULT_LANGUAGE,
            )

            if result and result.get("payment_url"):
                payment_url = result.get("payment_url")
                payment_id = str(result.get("local_payment_id") or result.get("uuid") or "pending")
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create Heleket payment",
                )

        elif request.payment_method == "mulenpay":
            if not settings.is_mulenpay_enabled():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="MulenPay payment method is unavailable",
                )

            payment_service = PaymentService()
            result = await payment_service.create_mulenpay_payment(
                db=db,
                user_id=user.id,
                amount_kopeks=request.amount_kopeks,
                description=settings.get_balance_payment_description(request.amount_kopeks),
                language=getattr(user, 'language', None) or settings.DEFAULT_LANGUAGE,
            )

            if result and result.get("payment_url"):
                payment_url = result.get("payment_url")
                payment_id = str(result.get("local_payment_id") or result.get("mulen_payment_id") or "pending")
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create MulenPay payment",
                )

        elif request.payment_method == "pal24":
            if not settings.is_pal24_enabled():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="PAL24 payment method is unavailable",
                )

            # Use payment_option to select card or sbp (default: sbp)
            option = (request.payment_option or "").strip().lower()
            if option not in {"card", "sbp"}:
                option = "sbp"
            provider_method = "card" if option == "card" else "sbp"

            payment_service = PaymentService()
            result = await payment_service.create_pal24_payment(
                db=db,
                user_id=user.id,
                amount_kopeks=request.amount_kopeks,
                description=settings.get_balance_payment_description(request.amount_kopeks),
                language=getattr(user, 'language', None) or settings.DEFAULT_LANGUAGE,
                payment_method=provider_method,
            )

            if result:
                # Select appropriate URL based on payment option
                preferred_urls = []
                if option == "sbp":
                    preferred_urls.append(result.get("sbp_url") or result.get("transfer_url"))
                elif option == "card":
                    preferred_urls.append(result.get("card_url"))
                preferred_urls.extend([
                    result.get("link_url"),
                    result.get("link_page_url"),
                    result.get("payment_url"),
                    result.get("transfer_url"),
                ])
                payment_url = next((url for url in preferred_urls if url), None)
                payment_id = str(result.get("local_payment_id") or result.get("bill_id") or "pending")

            if not payment_url:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create PAL24 payment",
                )

        elif request.payment_method == "wata":
            if not settings.is_wata_enabled():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Wata payment method is unavailable",
                )

            payment_service = PaymentService()
            result = await payment_service.create_wata_payment(
                db=db,
                user_id=user.id,
                amount_kopeks=request.amount_kopeks,
                description=settings.get_balance_payment_description(request.amount_kopeks),
                language=getattr(user, 'language', None) or settings.DEFAULT_LANGUAGE,
            )

            if result and result.get("payment_url"):
                payment_url = result.get("payment_url")
                payment_id = str(result.get("local_payment_id") or result.get("payment_link_id") or "pending")
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create Wata payment",
                )

        elif request.payment_method == "cloudpayments":
            if not settings.is_cloudpayments_enabled():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="CloudPayments payment method is unavailable",
                )

            payment_service = PaymentService()
            result = await payment_service.create_cloudpayments_payment(
                db=db,
                user_id=user.id,
                amount_kopeks=request.amount_kopeks,
                description=settings.get_balance_payment_description(request.amount_kopeks),
                language=getattr(user, 'language', None) or settings.DEFAULT_LANGUAGE,
            )

            if result and result.get("payment_url"):
                payment_url = result.get("payment_url")
                payment_id = str(result.get("local_payment_id") or result.get("invoice_id") or "pending")
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create CloudPayments payment",
                )

        elif request.payment_method == "freekassa":
            if not settings.is_freekassa_enabled():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="FreeKassa payment method is unavailable",
                )

            payment_service = PaymentService()
            result = await payment_service.create_freekassa_payment(
                db=db,
                user_id=user.id,
                amount_kopeks=request.amount_kopeks,
                description=settings.get_balance_payment_description(request.amount_kopeks),
                language=getattr(user, 'language', None) or settings.DEFAULT_LANGUAGE,
            )

            if result and result.get("payment_url"):
                payment_url = result.get("payment_url")
                payment_id = str(result.get("local_payment_id") or result.get("order_id") or "pending")
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create FreeKassa payment",
                )

        else:
            # For other payment methods, redirect to bot
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This payment method is only available through the Telegram bot.",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Payment creation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment. Please try again later.",
        )

    if not payment_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment URL not received",
        )

    return TopUpResponse(
        payment_id=payment_id or "pending",
        payment_url=payment_url,
        amount_kopeks=request.amount_kopeks,
        amount_rubles=amount_rubles,
        status="pending",
        expires_at=None,
    )


def _get_status_info(record: PendingPayment) -> tuple[str, str]:
    """Get status emoji and text for a pending payment."""
    status = (record.status or "").lower()

    if record.is_paid:
        return "✅", "Оплачено"

    if record.method == PaymentMethod.PAL24:
        mapping = {
            "new": ("⏳", "Ожидает оплаты"),
            "process": ("⌛", "Обрабатывается"),
            "success": ("✅", "Оплачено"),
            "fail": ("❌", "Ошибка"),
            "canceled": ("❌", "Отменено"),
        }
        return mapping.get(status, ("❓", "Неизвестно"))

    if record.method == PaymentMethod.MULENPAY:
        mapping = {
            "created": ("⏳", "Ожидает оплаты"),
            "processing": ("⌛", "Обрабатывается"),
            "hold": ("🔒", "На удержании"),
            "success": ("✅", "Оплачено"),
            "canceled": ("❌", "Отменено"),
            "error": ("❌", "Ошибка"),
        }
        return mapping.get(status, ("❓", "Неизвестно"))

    if record.method == PaymentMethod.WATA:
        mapping = {
            "opened": ("⏳", "Ожидает оплаты"),
            "pending": ("⏳", "Ожидает оплаты"),
            "processing": ("⌛", "Обрабатывается"),
            "paid": ("✅", "Оплачено"),
            "closed": ("✅", "Оплачено"),
            "declined": ("❌", "Отклонено"),
            "canceled": ("❌", "Отменено"),
            "expired": ("⌛", "Истёк"),
        }
        return mapping.get(status, ("❓", "Неизвестно"))

    if record.method == PaymentMethod.PLATEGA:
        mapping = {
            "pending": ("⏳", "Ожидает оплаты"),
            "inprogress": ("⌛", "Обрабатывается"),
            "confirmed": ("✅", "Оплачено"),
            "failed": ("❌", "Ошибка"),
            "canceled": ("❌", "Отменено"),
            "expired": ("⌛", "Истёк"),
        }
        return mapping.get(status, ("❓", "Неизвестно"))

    if record.method == PaymentMethod.HELEKET:
        if status in {"pending", "created", "waiting", "check", "processing"}:
            return "⏳", "Ожидает оплаты"
        if status in {"paid", "paid_over"}:
            return "✅", "Оплачено"
        if status in {"cancel", "canceled", "fail", "failed", "expired"}:
            return "❌", "Отменено"
        return "❓", "Неизвестно"

    if record.method == PaymentMethod.YOOKASSA:
        mapping = {
            "pending": ("⏳", "Ожидает оплаты"),
            "waiting_for_capture": ("⌛", "Обрабатывается"),
            "succeeded": ("✅", "Оплачено"),
            "canceled": ("❌", "Отменено"),
        }
        return mapping.get(status, ("❓", "Неизвестно"))

    if record.method == PaymentMethod.CRYPTOBOT:
        mapping = {
            "active": ("⏳", "Ожидает оплаты"),
            "paid": ("✅", "Оплачено"),
            "expired": ("⌛", "Истёк"),
        }
        return mapping.get(status, ("❓", "Неизвестно"))

    if record.method == PaymentMethod.CLOUDPAYMENTS:
        mapping = {
            "pending": ("⏳", "Ожидает оплаты"),
            "authorized": ("⌛", "Авторизовано"),
            "completed": ("✅", "Оплачено"),
            "failed": ("❌", "Ошибка"),
        }
        return mapping.get(status, ("❓", "Неизвестно"))

    if record.method == PaymentMethod.FREEKASSA:
        mapping = {
            "pending": ("⏳", "Ожидает оплаты"),
            "success": ("✅", "Оплачено"),
            "paid": ("✅", "Оплачено"),
            "canceled": ("❌", "Отменено"),
            "error": ("❌", "Ошибка"),
        }
        return mapping.get(status, ("❓", "Неизвестно"))

    return "❓", "Неизвестно"


def _is_checkable(record: PendingPayment) -> bool:
    """Check if payment can be manually checked."""
    if record.method not in SUPPORTED_MANUAL_CHECK_METHODS:
        return False
    if not record.is_recent():
        return False
    status = (record.status or "").lower()
    if record.method == PaymentMethod.PAL24:
        return status in {"new", "process"}
    if record.method == PaymentMethod.MULENPAY:
        return status in {"created", "processing", "hold"}
    if record.method == PaymentMethod.WATA:
        return status in {"opened", "pending", "processing", "inprogress", "in_progress"}
    if record.method == PaymentMethod.PLATEGA:
        return status in {"pending", "inprogress", "in_progress"}
    if record.method == PaymentMethod.HELEKET:
        return status not in {"paid", "paid_over", "cancel", "canceled", "fail", "failed", "expired"}
    if record.method == PaymentMethod.YOOKASSA:
        return status in {"pending", "waiting_for_capture"}
    if record.method == PaymentMethod.CRYPTOBOT:
        return status in {"active"}
    if record.method == PaymentMethod.CLOUDPAYMENTS:
        return status in {"pending", "authorized"}
    if record.method == PaymentMethod.FREEKASSA:
        return status in {"pending", "created", "processing"}
    return False


def _get_payment_url(record: PendingPayment) -> Optional[str]:
    """Extract payment URL from record."""
    payment = record.payment
    payment_url = getattr(payment, "payment_url", None)

    if record.method == PaymentMethod.PAL24:
        payment_url = getattr(payment, "link_url", None) or getattr(payment, "link_page_url", None) or payment_url
    elif record.method == PaymentMethod.WATA:
        payment_url = getattr(payment, "url", None) or payment_url
    elif record.method == PaymentMethod.YOOKASSA:
        payment_url = getattr(payment, "confirmation_url", None) or payment_url
    elif record.method == PaymentMethod.CRYPTOBOT:
        payment_url = (
            getattr(payment, "bot_invoice_url", None)
            or getattr(payment, "mini_app_invoice_url", None)
            or getattr(payment, "web_app_invoice_url", None)
            or payment_url
        )
    elif record.method == PaymentMethod.PLATEGA:
        payment_url = getattr(payment, "redirect_url", None) or payment_url
    elif record.method == PaymentMethod.CLOUDPAYMENTS:
        payment_url = getattr(payment, "payment_url", None) or payment_url
    elif record.method == PaymentMethod.FREEKASSA:
        payment_url = getattr(payment, "payment_url", None) or payment_url

    return payment_url


def _record_to_response(record: PendingPayment) -> PendingPaymentResponse:
    """Convert PendingPayment to API response."""
    status_emoji, status_text = _get_status_info(record)
    return PendingPaymentResponse(
        id=record.local_id,
        method=record.method.value,
        method_display=method_display_name(record.method),
        identifier=record.identifier,
        amount_kopeks=record.amount_kopeks,
        amount_rubles=record.amount_kopeks / 100,
        status=record.status or "",
        status_emoji=status_emoji,
        status_text=status_text,
        is_paid=record.is_paid,
        is_checkable=_is_checkable(record),
        created_at=record.created_at,
        expires_at=record.expires_at,
        payment_url=_get_payment_url(record),
        user_id=record.user.id if record.user else None,
        user_telegram_id=record.user.telegram_id if record.user else None,
        user_username=record.user.username if record.user else None,
    )


@router.get("/pending-payments", response_model=PendingPaymentListResponse)
async def get_pending_payments(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(10, ge=1, le=50, description="Items per page"),
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get user's pending payments for manual verification."""
    all_pending = await list_recent_pending_payments(db)

    # Filter only current user's payments
    user_payments = [p for p in all_pending if p.user and p.user.id == user.id]

    total = len(user_payments)
    pages = math.ceil(total / per_page) if total > 0 else 1

    # Paginate
    start_idx = (page - 1) * per_page
    page_payments = user_payments[start_idx:start_idx + per_page]

    items = [_record_to_response(p) for p in page_payments]

    return PendingPaymentListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/pending-payments/{method}/{payment_id}", response_model=PendingPaymentResponse)
async def get_pending_payment_details(
    method: str,
    payment_id: int,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get details of a specific pending payment."""
    try:
        payment_method = PaymentMethod(method)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid payment method: {method}",
        )

    record = await get_payment_record(db, payment_method, payment_id)

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )

    # Check that payment belongs to the current user
    if not record.user or record.user.id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return _record_to_response(record)


@router.post("/pending-payments/{method}/{payment_id}/check", response_model=ManualCheckResponse)
async def check_payment_status(
    method: str,
    payment_id: int,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Manually check and update payment status."""
    try:
        payment_method = PaymentMethod(method)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid payment method: {method}",
        )

    # Get current record
    record = await get_payment_record(db, payment_method, payment_id)

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )

    # Check that payment belongs to the current user
    if not record.user or record.user.id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Check if manual check is available
    if not _is_checkable(record):
        return ManualCheckResponse(
            success=False,
            message="Ручная проверка недоступна для этого платежа",
            payment=_record_to_response(record),
            status_changed=False,
        )

    old_status = record.status
    old_is_paid = record.is_paid

    # Run manual check
    payment_service = PaymentService()
    updated = await run_manual_check(db, payment_method, payment_id, payment_service)

    if not updated:
        return ManualCheckResponse(
            success=False,
            message="Не удалось проверить статус платежа",
            payment=_record_to_response(record),
            status_changed=False,
        )

    status_changed = updated.status != old_status or updated.is_paid != old_is_paid

    if status_changed:
        _, new_status_text = _get_status_info(updated)
        message = f"Статус обновлён: {new_status_text}"
    else:
        message = "Статус не изменился"

    return ManualCheckResponse(
        success=True,
        message=message,
        payment=_record_to_response(updated),
        status_changed=status_changed,
        old_status=old_status,
        new_status=updated.status,
    )
