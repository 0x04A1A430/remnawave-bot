"""Balance and payment routes for cabinet."""

import logging
import math
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.database.models import User, Transaction
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

    items = [
        TransactionResponse(
            id=t.id,
            type=t.type,
            amount_kopeks=t.amount_kopeks,
            amount_rubles=t.amount_kopeks / 100,
            description=t.description,
            payment_method=t.payment_method,
            is_completed=t.is_completed,
            created_at=t.created_at,
            completed_at=t.completed_at,
        )
        for t in transactions
    ]

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

    # YooKassa
    if settings.is_yookassa_enabled():
        methods.append(PaymentMethodResponse(
            id="yookassa",
            name="YooKassa (Bank Card)",
            description="Pay with bank card via YooKassa",
            min_amount_kopeks=settings.YOOKASSA_MIN_AMOUNT_KOPEKS,
            max_amount_kopeks=settings.YOOKASSA_MAX_AMOUNT_KOPEKS,
            is_available=True,
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

    # PAL24
    if settings.is_pal24_enabled():
        methods.append(PaymentMethodResponse(
            id="pal24",
            name="PAL24",
            description="Pay via PAL24",
            min_amount_kopeks=settings.PAL24_MIN_AMOUNT_KOPEKS,
            max_amount_kopeks=settings.PAL24_MAX_AMOUNT_KOPEKS,
            is_available=True,
        ))

    # Platega
    if settings.is_platega_enabled():
        methods.append(PaymentMethodResponse(
            id="platega",
            name="Platega",
            description="Pay via Platega",
            min_amount_kopeks=settings.PLATEGA_MIN_AMOUNT_KOPEKS,
            max_amount_kopeks=settings.PLATEGA_MAX_AMOUNT_KOPEKS,
            is_available=True,
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

    return methods


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
            result = await yookassa_service.create_payment(
                amount=amount_rubles,
                currency="RUB",
                description=f"Пополнение баланса на {amount_rubles:.2f} ₽",
                metadata={
                    "user_id": str(user.id),
                    "amount_kopeks": str(request.amount_kopeks),
                    "type": "balance_topup",
                    "source": "cabinet",
                },
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
                payment_url = result.get("pay_url") or result.get("bot_invoice_url")
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
                description=settings.get_balance_payment_description(request.amount_kopeks),
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
