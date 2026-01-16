"""Admin routes for statistics dashboard in cabinet."""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from sqlalchemy import select, func, and_

from app.database.crud.subscription import get_subscriptions_statistics
from app.database.crud.transaction import get_transactions_statistics, get_revenue_by_period
from app.database.crud.server_squad import get_server_statistics
from app.services.remnawave_service import RemnaWaveService
from app.config import settings

from ..dependencies import get_cabinet_db, get_current_admin_user
from app.database.models import User, Subscription, Tariff, SubscriptionStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/stats", tags=["Cabinet Admin Stats"])


# ============ Schemas ============

class NodeStatus(BaseModel):
    """Node status info."""
    uuid: str
    name: str
    address: str
    is_connected: bool
    is_disabled: bool
    users_online: int
    traffic_used_bytes: Optional[int] = None
    uptime: Optional[str] = None


class NodesOverview(BaseModel):
    """Overview of all nodes."""
    total: int
    online: int
    offline: int
    disabled: int
    total_users_online: int
    nodes: List[NodeStatus]


class RevenueData(BaseModel):
    """Revenue data point."""
    date: str
    amount_kopeks: int
    amount_rubles: float


class SubscriptionStats(BaseModel):
    """Subscription statistics."""
    total: int
    active: int
    trial: int
    paid: int
    expired: int
    purchased_today: int
    purchased_week: int
    purchased_month: int
    trial_to_paid_conversion: float


class FinancialStats(BaseModel):
    """Financial statistics."""
    income_today_kopeks: int
    income_today_rubles: float
    income_month_kopeks: int
    income_month_rubles: float
    income_total_kopeks: int
    income_total_rubles: float
    subscription_income_kopeks: int
    subscription_income_rubles: float


class ServerStats(BaseModel):
    """Server statistics."""
    total_servers: int
    available_servers: int
    servers_with_connections: int
    total_revenue_kopeks: int
    total_revenue_rubles: float


class TariffStatItem(BaseModel):
    """Statistics for a single tariff."""
    tariff_id: int
    tariff_name: str
    active_subscriptions: int
    trial_subscriptions: int
    purchased_today: int
    purchased_week: int
    purchased_month: int


class TariffStats(BaseModel):
    """Tariff statistics."""
    tariffs: List[TariffStatItem]
    total_tariff_subscriptions: int


class DashboardStats(BaseModel):
    """Complete dashboard statistics."""
    nodes: NodesOverview
    subscriptions: SubscriptionStats
    financial: FinancialStats
    servers: ServerStats
    revenue_chart: List[RevenueData]
    tariff_stats: Optional[TariffStats] = None


# ============ Routes ============

@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get complete dashboard statistics for admin panel."""
    try:
        # Get nodes status from RemnaWave
        nodes_data = await _get_nodes_overview()

        # Get subscription statistics
        sub_stats = await get_subscriptions_statistics(db)

        # Get financial statistics
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        trans_stats = await get_transactions_statistics(db, month_start, now)

        # Get revenue chart data (last 30 days)
        revenue_data = await get_revenue_by_period(db, days=30)

        # Get server statistics
        server_stats = await get_server_statistics(db)

        # Get tariff statistics
        tariff_stats = await _get_tariff_stats(db)

        # Build response
        return DashboardStats(
            nodes=nodes_data,
            subscriptions=SubscriptionStats(
                total=sub_stats.get("total_subscriptions", 0),
                active=sub_stats.get("active_subscriptions", 0),
                trial=sub_stats.get("trial_subscriptions", 0),
                paid=sub_stats.get("paid_subscriptions", 0),
                expired=sub_stats.get("total_subscriptions", 0) - sub_stats.get("active_subscriptions", 0),
                purchased_today=sub_stats.get("purchased_today", 0),
                purchased_week=sub_stats.get("purchased_week", 0),
                purchased_month=sub_stats.get("purchased_month", 0),
                trial_to_paid_conversion=sub_stats.get("trial_to_paid_conversion", 0.0),
            ),
            financial=FinancialStats(
                income_today_kopeks=trans_stats.get("today", {}).get("income_kopeks", 0),
                income_today_rubles=trans_stats.get("today", {}).get("income_kopeks", 0) / 100,
                income_month_kopeks=trans_stats.get("totals", {}).get("income_kopeks", 0),
                income_month_rubles=trans_stats.get("totals", {}).get("income_kopeks", 0) / 100,
                income_total_kopeks=trans_stats.get("totals", {}).get("income_kopeks", 0),
                income_total_rubles=trans_stats.get("totals", {}).get("income_kopeks", 0) / 100,
                subscription_income_kopeks=trans_stats.get("totals", {}).get("subscription_income_kopeks", 0),
                subscription_income_rubles=trans_stats.get("totals", {}).get("subscription_income_kopeks", 0) / 100,
            ),
            servers=ServerStats(
                total_servers=server_stats.get("total_servers", 0),
                available_servers=server_stats.get("available_servers", 0),
                servers_with_connections=server_stats.get("servers_with_connections", 0),
                total_revenue_kopeks=server_stats.get("total_revenue_kopeks", 0),
                total_revenue_rubles=server_stats.get("total_revenue_rubles", 0.0),
            ),
            revenue_chart=[
                RevenueData(
                    date=item.get("date", "").isoformat() if hasattr(item.get("date", ""), "isoformat") else str(item.get("date", "")),
                    amount_kopeks=item.get("amount_kopeks", 0),
                    amount_rubles=item.get("amount_kopeks", 0) / 100,
                )
                for item in revenue_data
            ],
            tariff_stats=tariff_stats,
        )

    except Exception as e:
        logger.error(f"Failed to get dashboard stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load dashboard statistics",
        )


@router.get("/nodes", response_model=NodesOverview)
async def get_nodes_status(
    admin: User = Depends(get_current_admin_user),
):
    """Get status of all nodes."""
    try:
        return await _get_nodes_overview()
    except Exception as e:
        logger.error(f"Failed to get nodes status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load nodes status",
        )


@router.post("/nodes/{node_uuid}/restart")
async def restart_node(
    node_uuid: str,
    admin: User = Depends(get_current_admin_user),
):
    """Restart a node."""
    try:
        service = RemnaWaveService()
        success = await service.manage_node(node_uuid, "restart")

        if success:
            logger.info(f"Admin {admin.id} restarted node {node_uuid}")
            return {"success": True, "message": "Node restart initiated"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to restart node",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to restart node {node_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to restart node",
        )


@router.post("/nodes/{node_uuid}/toggle")
async def toggle_node(
    node_uuid: str,
    admin: User = Depends(get_current_admin_user),
):
    """Enable or disable a node."""
    try:
        service = RemnaWaveService()
        nodes = await service.get_all_nodes()

        node = next((n for n in nodes if n.get("uuid") == node_uuid), None)
        if not node:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Node not found",
            )

        is_disabled = node.get("is_disabled", False)
        action = "enable" if is_disabled else "disable"
        success = await service.manage_node(node_uuid, action)

        if success:
            logger.info(f"Admin {admin.id} {action}d node {node_uuid}")
            return {"success": True, "message": f"Node {action}d", "is_disabled": not is_disabled}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to {action} node",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle node {node_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to toggle node",
        )


async def _get_nodes_overview() -> NodesOverview:
    """Get overview of all nodes."""
    try:
        service = RemnaWaveService()
        nodes = await service.get_all_nodes()

        total = len(nodes)
        online = sum(1 for n in nodes if n.get("is_connected") and not n.get("is_disabled"))
        disabled = sum(1 for n in nodes if n.get("is_disabled"))
        offline = total - online - disabled
        total_users_online = sum(n.get("users_online", 0) or 0 for n in nodes)

        node_statuses = [
            NodeStatus(
                uuid=n.get("uuid", ""),
                name=n.get("name", "Unknown"),
                address=n.get("address", ""),
                is_connected=n.get("is_connected", False),
                is_disabled=n.get("is_disabled", False),
                users_online=n.get("users_online", 0) or 0,
                traffic_used_bytes=n.get("traffic_used_bytes"),
                uptime=n.get("uptime"),
            )
            for n in nodes
        ]

        return NodesOverview(
            total=total,
            online=online,
            offline=offline,
            disabled=disabled,
            total_users_online=total_users_online,
            nodes=node_statuses,
        )
    except Exception as e:
        logger.warning(f"Failed to get nodes from RemnaWave: {e}")
        # Return empty data if RemnaWave is unavailable
        return NodesOverview(
            total=0,
            online=0,
            offline=0,
            disabled=0,
            total_users_online=0,
            nodes=[],
        )


async def _get_tariff_stats(db: AsyncSession) -> Optional[TariffStats]:
    """Get statistics for all tariffs."""
    try:
        # Получаем ВСЕ тарифы (включая неактивные) для статистики
        tariffs_result = await db.execute(
            select(Tariff)
            .order_by(Tariff.display_order)
        )
        tariffs = tariffs_result.scalars().all()

        if not tariffs:
            logger.info("📊 Нет тарифов в системе, пропускаем статистику")
            return None

        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        tariff_items = []
        total_tariff_subscriptions = 0

        for tariff in tariffs:
            # Активные подписки на этом тарифе
            active_result = await db.execute(
                select(func.count(Subscription.id))
                .where(
                    Subscription.tariff_id == tariff.id,
                    Subscription.status == SubscriptionStatus.ACTIVE.value
                )
            )
            active_count = active_result.scalar() or 0

            # Триальные подписки на этом тарифе
            trial_result = await db.execute(
                select(func.count(Subscription.id))
                .where(
                    Subscription.tariff_id == tariff.id,
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                    Subscription.is_trial == True
                )
            )
            trial_count = trial_result.scalar() or 0

            # Куплено сегодня (не триальные)
            today_result = await db.execute(
                select(func.count(Subscription.id))
                .where(
                    Subscription.tariff_id == tariff.id,
                    Subscription.created_at >= today_start,
                    Subscription.is_trial == False
                )
            )
            purchased_today = today_result.scalar() or 0

            # Куплено за неделю
            week_result = await db.execute(
                select(func.count(Subscription.id))
                .where(
                    Subscription.tariff_id == tariff.id,
                    Subscription.created_at >= week_ago,
                    Subscription.is_trial == False
                )
            )
            purchased_week = week_result.scalar() or 0

            # Куплено за месяц
            month_result = await db.execute(
                select(func.count(Subscription.id))
                .where(
                    Subscription.tariff_id == tariff.id,
                    Subscription.created_at >= month_ago,
                    Subscription.is_trial == False
                )
            )
            purchased_month = month_result.scalar() or 0

            logger.info(f"📊 Тариф '{tariff.name}': активных={active_count}, триал={trial_count}")

            tariff_items.append(TariffStatItem(
                tariff_id=tariff.id,
                tariff_name=tariff.name,
                active_subscriptions=active_count,
                trial_subscriptions=trial_count,
                purchased_today=purchased_today,
                purchased_week=purchased_week,
                purchased_month=purchased_month,
            ))

            total_tariff_subscriptions += active_count

        logger.info(f"📊 Всего подписок по тарифам: {total_tariff_subscriptions}")

        return TariffStats(
            tariffs=tariff_items,
            total_tariff_subscriptions=total_tariff_subscriptions,
        )

    except Exception as e:
        logger.error(f"Failed to get tariff stats: {e}", exc_info=True)
        return None
