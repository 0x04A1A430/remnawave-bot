from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends

from app.database.models import (
    CryptoBotPayment,
    MulenPayPayment,
    Pal24Payment,
    Subscription,
    SubscriptionStatus,
    Ticket,
    TicketStatus,
    User,
    UserStatus,
    YooKassaPayment,
)
from app.webapi.dependencies import get_db, require_permission
from app.webapi.schemas import StatsResponse

router = APIRouter(prefix="/stats")


@router.get("/overview", response_model=StatsResponse)
async def get_overview(
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.stats:read")),
) -> StatsResponse:
    total_users = await db.scalar(select(func.count(User.id)))

    active_users = await db.scalar(
        select(func.count(User.id)).where(User.status == UserStatus.ACTIVE.value)
    )
    blocked_users = await db.scalar(
        select(func.count(User.id)).where(User.status == UserStatus.BLOCKED.value)
    )
    total_balance = await db.scalar(select(func.coalesce(func.sum(User.balance_kopeks), 0)))

    active_subscriptions = await db.scalar(
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.ACTIVE.value
        )
    )
    expired_subscriptions = await db.scalar(
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.EXPIRED.value
        )
    )

    open_tickets = await db.scalar(
        select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.OPEN.value)
    )

    pending_yookassa = await db.scalar(
        select(func.count(YooKassaPayment.id)).where(
            YooKassaPayment.is_paid.is_(False)
        )
    ) or 0
    pending_cryptobot = await db.scalar(
        select(func.count(CryptoBotPayment.id)).where(
            CryptoBotPayment.status.in_(["active", "pending"])
        )
    ) or 0
    pending_mulenpay = await db.scalar(
        select(func.count(MulenPayPayment.id)).where(
            MulenPayPayment.is_paid.is_(False)
        )
    ) or 0
    pending_pal24 = await db.scalar(
        select(func.count(Pal24Payment.id)).where(
            Pal24Payment.is_paid.is_(False)
        )
    ) or 0

    pending_payments = (
        pending_yookassa + pending_cryptobot + pending_mulenpay + pending_pal24
    )

    return StatsResponse(
        total_users=total_users or 0,
        active_users=active_users or 0,
        blocked_users=blocked_users or 0,
        total_balance_kopeks=total_balance or 0,
        active_subscriptions=active_subscriptions or 0,
        expired_subscriptions=expired_subscriptions or 0,
        open_tickets=open_tickets or 0,
        pending_payments=pending_payments or 0,
    )
