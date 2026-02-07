"""Admin routes for traffic usage statistics."""

import asyncio
import csv
import io
import logging
import time
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.models import Subscription, User
from app.services.remnawave_service import RemnaWaveService

from ..dependencies import get_cabinet_db, get_current_admin_user
from ..schemas.traffic import (
    ExportCsvRequest,
    ExportCsvResponse,
    TrafficNodeInfo,
    TrafficUsageResponse,
    UserTrafficItem,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix='/admin/traffic', tags=['Admin Traffic'])

_ALLOWED_PERIODS = frozenset({1, 3, 7, 14, 30})
_CONCURRENCY_LIMIT = 5  # Max parallel API calls to avoid rate limiting
_DEVICE_CONCURRENCY_LIMIT = 10

# In-memory cache: {(start_str, end_str): (timestamp, aggregated_data, nodes_info, devices_map)}
_traffic_cache: dict[
    tuple[str, str], tuple[float, dict[str, dict[str, int]], list[TrafficNodeInfo], dict[str, int]]
] = {}
_CACHE_TTL = 300  # 5 minutes
_cache_lock = asyncio.Lock()

# Valid sort fields for the GET endpoint
_SORT_FIELDS = frozenset({'total_bytes', 'full_name', 'tariff_name', 'device_limit', 'traffic_limit_gb'})
_MAX_DATE_RANGE_DAYS = 31


def _validate_period(period: int) -> None:
    if period not in _ALLOWED_PERIODS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Period must be one of: {sorted(_ALLOWED_PERIODS)}',
        )


def _resolve_date_range(period: int, start_date: str, end_date: str) -> tuple[str, str, int]:
    """Resolve date range from either custom dates or period.

    Returns (start_str, end_str, period_days) in ISO datetime format.
    """
    now = datetime.now(UTC)

    if start_date and end_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=UTC)
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=UTC, hour=23, minute=59, second=59)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Invalid date format. Use YYYY-MM-DD.',
            )

        if start_dt > end_dt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='start_date must be before end_date.',
            )

        end_dt = min(end_dt, now)

        if start_dt > end_dt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='start_date cannot be in the future.',
            )

        delta = (end_dt - start_dt).days
        if delta > _MAX_DATE_RANGE_DAYS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Date range must not exceed {_MAX_DATE_RANGE_DAYS} days.',
            )

        period_days = max(delta, 1)
        start_str = start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        end_str = end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        return start_str, end_str, period_days

    _validate_period(period)
    end_dt = now
    start_dt = end_dt - timedelta(days=period)
    start_str = start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_str = end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    return start_str, end_str, period


async def _fetch_devices(api, user_uuids: list[str]) -> dict[str, int]:
    """Fetch connected device count for each user UUID. Returns {uuid: count}."""
    semaphore = asyncio.Semaphore(_DEVICE_CONCURRENCY_LIMIT)
    devices_map: dict[str, int] = {}

    async def fetch_one(uuid: str):
        async with semaphore:
            try:
                result = await api.get_user_devices(uuid)
                devices_map[uuid] = result.get('total', 0)
            except Exception:
                logger.debug('Failed to get devices for user %s', uuid, exc_info=True)
                devices_map[uuid] = 0

    await asyncio.gather(*(fetch_one(uid) for uid in user_uuids))
    return devices_map


async def _aggregate_traffic(
    start_str: str, end_str: str, user_uuids: list[str]
) -> tuple[dict[str, dict[str, int]], list[TrafficNodeInfo], dict[str, int]]:
    """Aggregate per-user traffic across all nodes for a given period.

    Uses legacy per-node endpoint to fetch all users' traffic per node —
    O(nodes) API calls instead of O(users). The legacy endpoint returns
    {userUuid, nodeUuid, total} per entry (non-legacy only returns topUsers
    without userUuid).

    Returns (user_traffic, nodes_info, devices_map) where:
      user_traffic = {remnawave_uuid: {node_uuid: total_bytes, ...}}
      nodes_info = [TrafficNodeInfo, ...]
      devices_map = {remnawave_uuid: connected_device_count}
    """
    cache_key = (start_str, end_str)

    # Quick check without lock
    now = time.time()
    cached = _traffic_cache.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1], cached[2], cached[3]

    # Acquire lock for the slow path
    async with _cache_lock:
        # Re-check after acquiring lock
        now = time.time()
        cached = _traffic_cache.get(cache_key)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1], cached[2], cached[3]

        service = RemnaWaveService()
        if not service.is_configured:
            return {}, [], {}

        user_uuids_set = set(user_uuids)

        async with service.get_api_client() as api:
            nodes = await api.get_all_nodes()

            # Fetch per-node user stats — O(nodes) calls instead of O(users)
            semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

            async def fetch_node_users(node):
                async with semaphore:
                    try:
                        stats = await api.get_bandwidth_stats_node_users_legacy(node.uuid, start_str, end_str)
                        return node.uuid, stats
                    except Exception:
                        logger.warning('Failed to get traffic for node %s', node.name, exc_info=True)
                        return node.uuid, None

            results = await asyncio.gather(*(fetch_node_users(n) for n in nodes))

            nodes_info: list[TrafficNodeInfo] = [
                TrafficNodeInfo(node_uuid=node.uuid, node_name=node.name, country_code=node.country_code)
                for node in nodes
            ]
            nodes_info.sort(key=lambda n: n.node_name)

            # Legacy response: [{userUuid, username, nodeUuid, total, date}, ...]
            user_traffic: dict[str, dict[str, int]] = {}
            for node_uuid, entries in results:
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    uid = entry.get('userUuid', '')
                    total = int(entry.get('total', 0))
                    if uid and total > 0 and uid in user_uuids_set:
                        user_traffic.setdefault(uid, {})[node_uuid] = (
                            user_traffic.get(uid, {}).get(node_uuid, 0) + total
                        )

            # Fetch devices for users that have traffic
            uuids_with_traffic = list(user_traffic.keys())
            devices_map = await _fetch_devices(api, uuids_with_traffic) if uuids_with_traffic else {}

        _traffic_cache[cache_key] = (now, user_traffic, nodes_info, devices_map)
        return user_traffic, nodes_info, devices_map


async def _load_user_map(db: AsyncSession) -> dict[str, User]:
    """Load all users with remnawave_uuid, eagerly loading subscription + tariff."""
    stmt = (
        select(User)
        .where(User.remnawave_uuid.isnot(None))
        .options(selectinload(User.subscription).selectinload(Subscription.tariff))
    )
    result = await db.execute(stmt)
    users = result.scalars().all()
    return {u.remnawave_uuid: u for u in users if u.remnawave_uuid}


def _build_traffic_items(
    user_traffic: dict[str, dict[str, int]],
    user_map: dict[str, User],
    nodes_info: list[TrafficNodeInfo],
    devices_map: dict[str, int],
    search: str = '',
    sort_by: str = 'total_bytes',
    sort_desc: bool = True,
    tariff_filter: set[str] | None = None,
    node_filter: set[str] | None = None,
    status_filter: set[str] | None = None,
) -> list[UserTrafficItem]:
    """Merge traffic data with user data, apply search/tariff/node/status filters, return sorted list."""
    items: list[UserTrafficItem] = []
    search_lower = search.lower().strip()

    all_uuids = set(user_traffic.keys()) | set(user_map.keys())
    for uuid in all_uuids:
        user = user_map.get(uuid)
        if not user:
            continue

        traffic = user_traffic.get(uuid, {})

        # Apply node filter: keep only selected nodes, recalculate total
        if node_filter is not None:
            traffic = {nid: val for nid, val in traffic.items() if nid in node_filter}

        total_bytes = sum(traffic.values())

        full_name = user.full_name
        username = user.username

        if search_lower:
            if search_lower not in (full_name or '').lower() and search_lower not in (username or '').lower():
                continue

        sub = user.subscription
        tariff_name = None
        subscription_status = None
        traffic_limit_gb = 0.0
        device_limit = 1

        if sub:
            subscription_status = sub.actual_status if hasattr(sub, 'actual_status') else sub.status
            traffic_limit_gb = float(sub.traffic_limit_gb or 0)
            device_limit = sub.device_limit or 1
            if sub.tariff:
                tariff_name = sub.tariff.name

        if tariff_filter is not None:
            if (tariff_name or '') not in tariff_filter:
                continue

        if status_filter is not None:
            if (subscription_status or '') not in status_filter:
                continue

        connected_devices = devices_map.get(uuid, 0)

        items.append(
            UserTrafficItem(
                user_id=user.id,
                telegram_id=user.telegram_id,
                username=username,
                full_name=full_name,
                tariff_name=tariff_name,
                subscription_status=subscription_status,
                traffic_limit_gb=traffic_limit_gb,
                device_limit=device_limit,
                connected_devices=connected_devices,
                node_traffic=traffic,
                total_bytes=total_bytes,
            )
        )

    # Sort by the requested field; node columns use 'node_<uuid>' prefix
    if sort_by.startswith('node_'):
        node_uuid = sort_by[5:]
        items.sort(key=lambda x: x.node_traffic.get(node_uuid, 0), reverse=sort_desc)
    elif sort_by in ('full_name', 'tariff_name'):
        items.sort(key=lambda x: (getattr(x, sort_by, None) or '').lower(), reverse=sort_desc)
    else:
        items.sort(key=lambda x: getattr(x, sort_by, 0) or 0, reverse=sort_desc)

    return items


@router.get('', response_model=TrafficUsageResponse)
async def get_traffic_usage(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
    period: int = Query(30, ge=1, le=30),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str = Query('', max_length=100),
    sort_by: str = Query('total_bytes', max_length=100),
    sort_desc: bool = Query(True),
    tariffs: str = Query('', max_length=500),
    nodes: str = Query('', max_length=2000),
    statuses: str = Query('', max_length=200),
    start_date: str = Query('', max_length=10),
    end_date: str = Query('', max_length=10),
):
    """Get paginated per-user traffic usage by node."""
    start_str, end_str, period_days = _resolve_date_range(period, start_date, end_date)

    user_map = await _load_user_map(db)
    user_traffic, nodes_info, devices_map = await _aggregate_traffic(start_str, end_str, list(user_map.keys()))

    # Collect all available tariff names (before filtering)
    available_tariffs = sorted(
        {
            u.subscription.tariff.name
            for u in user_map.values()
            if u.subscription and u.subscription.tariff and u.subscription.tariff.name
        }
    )

    # Collect all available statuses (before filtering)
    available_statuses = sorted(
        {
            (u.subscription.actual_status if hasattr(u.subscription, 'actual_status') else u.subscription.status)
            for u in user_map.values()
            if u.subscription
        }
    )

    # Parse tariff filter
    tariff_filter: set[str] | None = None
    if tariffs.strip():
        tariff_filter = {t.strip() for t in tariffs.split(',') if t.strip()}

    # Parse node filter
    node_filter: set[str] | None = None
    if nodes.strip():
        node_filter = {n.strip() for n in nodes.split(',') if n.strip()}

    # Parse status filter
    status_filter: set[str] | None = None
    if statuses.strip():
        status_filter = {s.strip() for s in statuses.split(',') if s.strip()}

    # Validate sort_by: allow known fields + 'node_<uuid>' for dynamic node columns
    node_uuids = {n.node_uuid for n in nodes_info}
    is_node_sort = sort_by.startswith('node_') and sort_by[5:] in node_uuids
    if sort_by not in _SORT_FIELDS and not is_node_sort:
        sort_by = 'total_bytes'

    items = _build_traffic_items(
        user_traffic,
        user_map,
        nodes_info,
        devices_map,
        search,
        sort_by,
        sort_desc,
        tariff_filter,
        node_filter,
        status_filter,
    )

    total = len(items)
    paginated = items[offset : offset + limit]

    # Filter nodes_info to only selected nodes for frontend column display
    filtered_nodes = nodes_info
    if node_filter is not None:
        filtered_nodes = [n for n in nodes_info if n.node_uuid in node_filter]

    return TrafficUsageResponse(
        items=paginated,
        nodes=filtered_nodes,
        total=total,
        offset=offset,
        limit=limit,
        period_days=period_days,
        available_tariffs=available_tariffs,
        available_statuses=available_statuses,
    )


@router.post('/export-csv', response_model=ExportCsvResponse)
async def export_traffic_csv(
    request: ExportCsvRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Generate CSV with traffic usage and send to admin's Telegram DM."""
    if not admin.telegram_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Admin has no Telegram ID configured',
        )

    start_str, end_str, period_days = _resolve_date_range(request.period, request.start_date, request.end_date)

    user_map = await _load_user_map(db)
    user_traffic, nodes_info, devices_map = await _aggregate_traffic(start_str, end_str, list(user_map.keys()))

    # Parse filters
    tariff_filter: set[str] | None = None
    if request.tariffs.strip():
        tariff_filter = {t.strip() for t in request.tariffs.split(',') if t.strip()}

    node_filter: set[str] | None = None
    if request.nodes.strip():
        node_filter = {n.strip() for n in request.nodes.split(',') if n.strip()}

    status_filter: set[str] | None = None
    if request.statuses.strip():
        status_filter = {s.strip() for s in request.statuses.split(',') if s.strip()}

    items = _build_traffic_items(
        user_traffic,
        user_map,
        nodes_info,
        devices_map,
        search=request.search,
        tariff_filter=tariff_filter,
        node_filter=node_filter,
        status_filter=status_filter,
    )

    # Filter node columns for CSV if node filter active
    csv_nodes = nodes_info
    if node_filter is not None:
        csv_nodes = [n for n in nodes_info if n.node_uuid in node_filter]

    # Build CSV rows
    rows: list[dict] = []
    for item in items:
        row: dict = {
            'User ID': item.user_id,
            'Telegram ID': item.telegram_id or '',
            'Username': item.username or '',
            'Full Name': item.full_name,
            'Tariff': item.tariff_name or '',
            'Status': item.subscription_status or '',
            'Traffic Limit (GB)': item.traffic_limit_gb,
            'Devices': f'{item.connected_devices}/{item.device_limit}',
        }
        for node in csv_nodes:
            row[f'{node.node_name} (bytes)'] = item.node_traffic.get(node.node_uuid, 0)
        row['Total (bytes)'] = item.total_bytes
        row['Total (GB)'] = round(item.total_bytes / (1024**3), 2) if item.total_bytes else 0
        rows.append(row)

    # Generate CSV
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    csv_bytes = output.getvalue().encode('utf-8-sig')

    timestamp = datetime.now(UTC).strftime('%Y%m%d_%H%M%S')
    filename = f'traffic_usage_{period_days}d_{timestamp}.csv'

    try:
        bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        async with bot:
            await bot.send_document(
                chat_id=admin.telegram_id,
                document=BufferedInputFile(csv_bytes, filename=filename),
                caption=f'Traffic usage report ({period_days}d)\nUsers: {len(rows)}',
            )
    except Exception:
        logger.error('Failed to send CSV to admin %s', admin.telegram_id, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to send CSV report. Please try again later.',
        )

    return ExportCsvResponse(success=True, message=f'CSV sent ({len(rows)} users)')
