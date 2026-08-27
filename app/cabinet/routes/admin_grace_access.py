"""Admin routes for restricted grace access: configuration and health.

Grace access is the only subsystem that rewrites a user's state in Remnawave on a
timer, and until now it was steered exclusively through ``.env`` plus an emergency
CLI. The generic settings page could technically reach the same keys, but it shows
them as twelve unrelated rows of free text — nothing there says that switching the
mode on without a squad UUID makes the bot disable grace at the next start, or that
the mode itself is only read during startup.

So this module exposes the knobs as one coherent unit: the merged configuration is
validated the way the runtime validates it at boot, the running mode is reported
next to the configured one, and the session counters come from the same collector
the rollback CLI prints.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import GraceAccessSessionModel, Subscription, User
from app.services.grace_access_runtime import collect_grace_status, grace_access_runtime
from app.services.grace_access_service import GraceAccessMode, GraceSessionState
from app.services.system_settings_service import (
    ReadOnlySettingError,
    bot_configuration_service,
)

from ..dependencies import get_cabinet_db, require_permission


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/grace-access', tags=['Admin Grace Access'])

# Response/request field name -> settings key. Every read, write, env-lock check and
# validation message resolves through this map, so a renamed key cannot half-apply.
FIELD_KEYS: dict[str, str] = {
    'mode': 'GRACE_ACCESS_MODE',
    'duration_hours': 'GRACE_ACCESS_DURATION_HOURS',
    'expired_squad_uuid': 'GRACE_ACCESS_EXPIRED_SQUAD_UUID',
    'limited_squad_uuid': 'GRACE_ACCESS_LIMITED_SQUAD_UUID',
    'external_squad_uuid': 'GRACE_ACCESS_EXTERNAL_SQUAD_UUID',
    'traffic_gb': 'GRACE_ACCESS_TRAFFIC_GB',
    'trial_enabled': 'GRACE_ACCESS_TRIAL_ENABLED',
    'daily_enabled': 'GRACE_ACCESS_DAILY_ENABLED',
    'free_enabled': 'GRACE_ACCESS_FREE_ENABLED',
    'reconcile_interval_seconds': 'GRACE_ACCESS_RECONCILE_INTERVAL_SECONDS',
    'reconcile_batch_size': 'GRACE_ACCESS_RECONCILE_BATCH_SIZE',
    'candidate_lookback_minutes': 'GRACE_ACCESS_CANDIDATE_LOOKBACK_MINUTES',
}

# Read once at startup by GraceAccessRuntime.start / _run_loop, so a saved value sits
# in the database without changing anything until the bot is restarted. Everything
# else goes through _build_policy(), which is rebuilt per operation and therefore live.
RESTART_ONLY_FIELDS: frozenset[str] = frozenset({'mode', 'reconcile_interval_seconds'})

# 'keep' is not a UUID: it tells the overlay builder to leave whatever external squad
# the panel user already has instead of detaching it. _resolve_grace_external_squad
# compares it lowercased, so 'Keep' works there and must not be called malformed here.
EXTERNAL_SQUAD_KEEP = 'keep'

_ENV_LOCKED_DETAIL = (
    "Setting '{key}' is fixed in the environment (.env) and cannot be changed here. "
    'Remove it from .env (and restart) to manage it from the cabinet.'
)

_OPEN_STATES: tuple[str, ...] = (
    GraceSessionState.PENDING.value,
    GraceSessionState.ACTIVE.value,
    GraceSessionState.RESTORING.value,
)

RECENT_ERROR_LIMIT = 20
SESSIONS_PAGE_LIMIT = 50


# ============ Schemas ============


class GraceAccessConfig(BaseModel):
    """Stored configuration — what the bot will use from its next start on."""

    mode: Literal['false', 'observe', 'true', 'drain']
    duration_hours: int
    expired_squad_uuid: str
    limited_squad_uuid: str
    external_squad_uuid: str
    traffic_gb: int
    trial_enabled: bool
    daily_enabled: bool
    free_enabled: bool
    reconcile_interval_seconds: int
    reconcile_batch_size: int
    candidate_lookback_minutes: int


class GraceAccessRuntimeState(BaseModel):
    """What the process is actually doing right now."""

    running_mode: str
    configured_mode: str
    # The two differ after an edit to a restart-only field, and also when startup
    # validation failed — in both cases grace behaves like the running mode, not
    # like the one on screen, and only a restart closes the gap.
    restart_required: bool


class GraceAccessStats(BaseModel):
    states: dict[str, int]
    open: int
    open_errors: int
    completed_errors: int


class GraceAccessIssue(BaseModel):
    """One reason the configuration is not safe to run.

    ``severity='error'`` means the runtime refuses ``mode=true`` and silently starts
    disabled; ``'warning'`` means grace runs but leaves something unattended.
    """

    field: str
    code: str
    severity: Literal['error', 'warning']


class GraceSessionError(BaseModel):
    id: str
    subscription_id: int
    state: str
    completion_reason: str | None = None
    last_error: str


class GraceAccessOverview(BaseModel):
    config: GraceAccessConfig
    env_locked: list[str]
    restart_only: list[str]
    runtime: GraceAccessRuntimeState
    stats: GraceAccessStats
    issues: list[GraceAccessIssue]
    recent_errors: list[GraceSessionError]


class GraceAccessUpdate(BaseModel):
    """Partial update. Only the fields present in the body are written."""

    mode: Literal['false', 'observe', 'true', 'drain'] | None = None
    duration_hours: int | None = Field(default=None, ge=1, le=8760)
    expired_squad_uuid: str | None = None
    limited_squad_uuid: str | None = None
    external_squad_uuid: str | None = None
    traffic_gb: int | None = Field(default=None, ge=0, le=1024)
    trial_enabled: bool | None = None
    daily_enabled: bool | None = None
    free_enabled: bool | None = None
    reconcile_interval_seconds: int | None = Field(default=None, ge=5, le=86400)
    reconcile_batch_size: int | None = Field(default=None, ge=1, le=10000)
    candidate_lookback_minutes: int | None = Field(default=None, ge=1, le=10080)


class GraceSessionUser(BaseModel):
    id: int
    telegram_id: int | None = None
    username: str | None = None
    full_name: str = ''


class GraceSessionItem(BaseModel):
    id: str
    subscription_id: int
    remnawave_id: int | None = None
    reason: str
    state: str
    started_at: datetime
    grace_until: datetime
    updated_at: datetime
    completion_reason: str | None = None
    last_error: str | None = None
    user: GraceSessionUser | None = None


class GraceSessionsPage(BaseModel):
    items: list[GraceSessionItem]
    total: int
    page: int
    limit: int


class GraceSquadOption(BaseModel):
    uuid: str
    name: str
    members_count: int = 0


class GraceSquadsResponse(BaseModel):
    """Squad picker source.

    ``available=False`` means the panel could not be reached; the page then falls back
    to a plain UUID field instead of pretending the panel has no squads at all.
    """

    available: bool
    items: list[GraceSquadOption]


# ============ Helpers ============


def _read_config() -> GraceAccessConfig:
    values = {field: bot_configuration_service.get_current_value(key) for field, key in FIELD_KEYS.items()}
    return GraceAccessConfig(**values)


def _normalize_squad(value: str) -> str:
    return (value or '').strip()


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _collect_issues(config: GraceAccessConfig, *, open_sessions: int, running_mode: str) -> list[GraceAccessIssue]:
    """Everything that makes the configuration unsafe, whether or not it is on yet.

    Deliberately independent of the current mode: the reason to list the missing
    squad UUID is precisely that the admin has not switched grace on yet, and
    finding out at the next restart — from a log line — is how this ends up
    silently disabled in production.
    """
    issues: list[GraceAccessIssue] = []

    for field in ('expired_squad_uuid', 'limited_squad_uuid'):
        raw = _normalize_squad(getattr(config, field))
        if not raw:
            issues.append(GraceAccessIssue(field=field, code='squad_required', severity='error'))
        elif not _is_uuid(raw):
            issues.append(GraceAccessIssue(field=field, code='squad_invalid', severity='error'))

    external = _normalize_squad(config.external_squad_uuid)
    if external and external.lower() != EXTERNAL_SQUAD_KEEP and not _is_uuid(external):
        issues.append(GraceAccessIssue(field='external_squad_uuid', code='squad_invalid', severity='error'))

    if config.traffic_gb < 1:
        issues.append(GraceAccessIssue(field='traffic_gb', code='traffic_required', severity='error'))

    # A non-mutating runtime never finishes what an earlier active run started: those
    # users keep the grace overlay in the panel until someone switches to drain or runs
    # the restore CLI. The runtime logs this as CRITICAL at startup and never again.
    if open_sessions and running_mode in {GraceAccessMode.DISABLED.value, GraceAccessMode.OBSERVE.value}:
        issues.append(GraceAccessIssue(field='mode', code='open_sessions_stranded', severity='warning'))

    return issues


def _validate_for_mode(config: GraceAccessConfig) -> None:
    """Reject a write that would leave the runtime unable to start in this mode.

    Mirrors ``_validate_active_configuration``: without it the cabinet happily saves
    ``mode=true`` with an empty squad, and the only feedback is grace being disabled
    after the next restart.
    """
    blockers = [
        issue for issue in _collect_issues(config, open_sessions=0, running_mode='') if issue.severity == 'error'
    ]
    if config.mode != GraceAccessMode.ACTIVE.value or not blockers:
        return

    labels = {
        'squad_required': "'{field}' is required when the mode is 'true'",
        'squad_invalid': "'{field}' must contain a valid UUID",
        'traffic_required': "'traffic_gb' must be at least 1 when the mode is 'true'",
    }
    reasons = '; '.join(labels[issue.code].format(field=issue.field) for issue in blockers)
    raise HTTPException(status.HTTP_400_BAD_REQUEST, f'Grace access cannot be enabled: {reasons}')


def _runtime_state(config: GraceAccessConfig) -> GraceAccessRuntimeState:
    running_mode = grace_access_runtime.mode.value
    return GraceAccessRuntimeState(
        running_mode=running_mode,
        configured_mode=config.mode,
        restart_required=running_mode != config.mode,
    )


def _serialize_user(user: User | None) -> GraceSessionUser | None:
    if user is None:
        return None
    return GraceSessionUser(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        full_name=user.full_name,
    )


# ============ Routes ============


@router.get('', response_model=GraceAccessOverview)
async def get_grace_access_overview(
    admin: User = Depends(require_permission('settings:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Configuration, running state and session health in one payload."""
    config = _read_config()
    runtime = _runtime_state(config)
    snapshot = await collect_grace_status(db, error_limit=RECENT_ERROR_LIMIT)

    return GraceAccessOverview(
        config=config,
        env_locked=[field for field, key in FIELD_KEYS.items() if bot_configuration_service.is_env_locked(key)],
        restart_only=sorted(RESTART_ONLY_FIELDS),
        runtime=runtime,
        stats=GraceAccessStats(
            states=snapshot['states'],
            open=snapshot['open'],
            open_errors=snapshot['open_errors'],
            completed_errors=snapshot['completed_errors'],
        ),
        issues=_collect_issues(config, open_sessions=snapshot['open'], running_mode=runtime.running_mode),
        recent_errors=[GraceSessionError(**row) for row in snapshot['recent_errors']],
    )


@router.get('/squads', response_model=GraceSquadsResponse)
async def list_grace_squads(
    admin: User = Depends(require_permission('settings:read')),
):
    """Squads offered by the panel, for picking the grace squads by name.

    Guarded by the same permission as the rest of this page on purpose: an admin who
    may configure grace must be able to see the list, without also being granted the
    full RemnaWave section.
    """
    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()
        if not service.is_configured:
            return GraceSquadsResponse(available=False, items=[])
        squads = await service.get_all_squads()
    except Exception as error:
        logger.warning('Grace squad list unavailable; falling back to manual UUID entry', error=str(error))
        return GraceSquadsResponse(available=False, items=[])

    return GraceSquadsResponse(
        available=True,
        items=[
            GraceSquadOption(
                uuid=str(squad.get('uuid') or ''),
                name=str(squad.get('name') or ''),
                members_count=int(squad.get('members_count') or 0),
            )
            for squad in squads
            if squad.get('uuid')
        ],
    )


@router.get('/sessions', response_model=GraceSessionsPage)
async def list_grace_sessions(
    admin: User = Depends(require_permission('settings:read')),
    db: AsyncSession = Depends(get_cabinet_db),
    state: Annotated[Literal['open', 'pending', 'active', 'restoring', 'completed', 'errors'] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=SESSIONS_PAGE_LIMIT)] = 20,
):
    """Grace sessions, newest first."""
    filters = []
    if state == 'open':
        filters.append(GraceAccessSessionModel.state.in_(_OPEN_STATES))
    elif state == 'errors':
        filters.append(GraceAccessSessionModel.last_error.isnot(None))
    elif state:
        filters.append(GraceAccessSessionModel.state == state)

    total = int(
        (await db.execute(select(func.count()).select_from(GraceAccessSessionModel).where(*filters))).scalar_one()
    )

    rows = (
        (
            await db.execute(
                select(GraceAccessSessionModel)
                .where(*filters)
                .options(selectinload(GraceAccessSessionModel.subscription).selectinload(Subscription.user))
                .order_by(GraceAccessSessionModel.updated_at.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    return GraceSessionsPage(
        items=[
            GraceSessionItem(
                id=str(row.id),
                subscription_id=int(row.subscription_id),
                remnawave_id=row.remnawave_id,
                reason=str(row.reason),
                state=str(row.state),
                started_at=row.started_at,
                grace_until=row.grace_until,
                updated_at=row.updated_at,
                completion_reason=row.completion_reason,
                last_error=row.last_error,
                user=_serialize_user(getattr(row.subscription, 'user', None)),
            )
            for row in rows
        ],
        total=total,
        page=page,
        limit=limit,
    )


@router.put('', response_model=GraceAccessOverview)
async def update_grace_access(
    payload: GraceAccessUpdate,
    admin: User = Depends(require_permission('settings:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Apply a partial configuration change, validated as a whole."""
    current = _read_config()
    patch: dict[str, Any] = payload.model_dump(exclude_unset=True)

    for field in ('expired_squad_uuid', 'limited_squad_uuid', 'external_squad_uuid'):
        if field in patch:
            patch[field] = _normalize_squad(patch[field] or '')

    # Unchanged fields are dropped before the env-lock check: the page submits the whole
    # form, and rejecting it because one pinned field came back with its own value would
    # make every other field on the page unsavable.
    changed = {field: value for field, value in patch.items() if value != getattr(current, field)}

    for field in changed:
        key = FIELD_KEYS[field]
        if bot_configuration_service.is_env_locked(key):
            raise HTTPException(status.HTTP_409_CONFLICT, _ENV_LOCKED_DETAIL.format(key=key))

    merged = current.model_copy(update=changed)
    _validate_for_mode(merged)

    for field, value in changed.items():
        try:
            await bot_configuration_service.set_value(db, FIELD_KEYS[field], value)
        except ReadOnlySettingError as error:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    await db.commit()

    if changed:
        logger.info(
            'Admin updated grace access configuration',
            telegram_id=admin.telegram_id,
            fields=sorted(changed),
            mode=merged.mode,
        )

    return await get_grace_access_overview(admin=admin, db=db)
