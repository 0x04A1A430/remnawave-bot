"""One-shot backfill of numeric panel identity (Remnawave 3.0.0).

Remnawave 3.0.0 removed ``uuid`` from ``UsersSchema``; a panel user is now
identified only by a numeric ``id``.  Bot rows created before the upgrade store
the old uuid, which the panel can no longer translate: ``POST /api/users/resolve``
accepts only ``{id | shortUuid | username}`` and ``GET /api/users/by-subscription-uuid``
is gone.

So the link is rebuilt from identifiers that survived the upgrade, in descending
order of trust:

1. ``subscriptions.remnawave_short_uuid`` — panel-assigned, stored verbatim,
   unique.  This is the only exact key.
2. ``telegram_id`` — panel-side ``telegramId``.  In multi-tariff a bot user owns
   several panel users sharing one telegramId, so a match is only accepted when
   it is unambiguous, or when the panel username carries the subscription's
   ``remnawave_short_id`` suffix.
3. Reconstructed username — deterministic only while ``REMNAWAVE_USER_USERNAME_TEMPLATE``
   depends on stable fields (the default ``user_{telegram_id}`` does; a template
   built from ``full_name`` does not, because the name may have changed since the
   panel user was created).  Best-effort, applied last.
4. ``email`` — for email-only users with no telegram_id.

The whole panel roster is streamed once and matched locally, rather than issuing
a request per row: an install with tens of thousands of subscriptions would
otherwise hammer the panel for hours.

Nothing here guesses.  A row that cannot be matched confidently is reported as
unresolved and left alone, because a wrong id silently points a subscription at
someone else's VPN account.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import GraceAccessSessionModel, Subscription, User
from app.external.remnawave_api import RemnaWaveUser
from app.services.remnawave_service import RemnaWaveService


logger = structlog.get_logger(__name__)

# Статусы, при которых подписка считается живой — такая строка внутри
# сиблинг-группы получает панельный id первой.
_ALIVE_STATUSES = ('active', 'trial', 'limited')


@dataclass
class UnresolvedRow:
    kind: str  # 'subscription' | 'user' | 'grace_session'
    row_id: Any
    reason: str
    remnawave_uuid: str | None = None
    remnawave_short_uuid: str | None = None


@dataclass
class BackfillReport:
    dry_run: bool = True
    panel_users: int = 0
    subscriptions_total: int = 0
    subscriptions_resolved: int = 0
    users_resolved: int = 0
    grace_sessions_resolved: int = 0
    by_strategy: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    unresolved: list[UnresolvedRow] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.unresolved and not self.conflicts

    def as_dict(self) -> dict[str, Any]:
        return {
            'dry_run': self.dry_run,
            'panel_users': self.panel_users,
            'subscriptions_total': self.subscriptions_total,
            'subscriptions_resolved': self.subscriptions_resolved,
            'users_resolved': self.users_resolved,
            'grace_sessions_resolved': self.grace_sessions_resolved,
            'by_strategy': dict(self.by_strategy),
            'unresolved': len(self.unresolved),
            'conflicts': len(self.conflicts),
            'complete': self.complete,
        }


class _PanelIndex:
    """Local lookup tables over the full panel roster."""

    def __init__(self, panel_users: list[RemnaWaveUser]):
        self.by_short_uuid: dict[str, RemnaWaveUser] = {}
        self.by_username: dict[str, RemnaWaveUser] = {}
        self.by_telegram_id: dict[int, list[RemnaWaveUser]] = defaultdict(list)
        self.by_email: dict[str, list[RemnaWaveUser]] = defaultdict(list)

        for panel_user in panel_users:
            if panel_user.short_uuid:
                self.by_short_uuid[panel_user.short_uuid] = panel_user
            if panel_user.username:
                self.by_username[panel_user.username] = panel_user
            if panel_user.telegram_id is not None:
                self.by_telegram_id[int(panel_user.telegram_id)].append(panel_user)
            if panel_user.email:
                self.by_email[panel_user.email.strip().lower()].append(panel_user)


async def _load_panel_roster(service: RemnaWaveService) -> list[RemnaWaveUser]:
    async with service.get_api_client() as api:
        # enrich_happ_links=False: happ-ссылки для сопоставления не нужны, а
        # обогащение добавляет внешний вызов на каждого пользователя.
        return await api.get_all_users_stream(size=1000, enrich_happ_links=False)


def _match_subscription(
    subscription: Subscription,
    user: User | None,
    index: _PanelIndex,
    claimed: dict[int, str],
    *,
    exact_only: bool = False,
) -> tuple[RemnaWaveUser | None, str]:
    """Return (panel_user, strategy) or (None, failure_reason).

    ``exact_only`` restricts matching to the one exact key (``shortUuid``).  The
    caller runs that pass over every row first: otherwise a weak
    ``telegram_id_unique`` match on a low-numbered row could claim the panel
    account that a later row identifies exactly, permanently linking a dead
    subscription to a live panel user and leaving the live one NULL.
    """

    short_uuid = (subscription.remnawave_short_uuid or '').strip()
    if short_uuid:
        panel_user = index.by_short_uuid.get(short_uuid)
        if panel_user is not None:
            return panel_user, 'short_uuid'
        # A stored shortUuid that the panel does not know means the panel user
        # was deleted.  Falling through to telegram_id here would re-link the
        # subscription to a *different* panel account of the same person, so we
        # stop instead.
        return None, 'short_uuid_not_found_in_panel'

    if exact_only:
        return None, 'no_exact_key'

    suffix = f'_{subscription.remnawave_short_id}' if subscription.remnawave_short_id else ''

    telegram_id = getattr(user, 'telegram_id', None) if user is not None else None
    if telegram_id is not None:
        candidates = index.by_telegram_id.get(int(telegram_id), [])
        if len(candidates) == 1:
            return candidates[0], 'telegram_id_unique'
        if len(candidates) > 1 and suffix:
            # Multi-tariff: the per-subscription short id is baked into the
            # panel username, which makes the choice unambiguous again.
            suffixed = [c for c in candidates if c.username and c.username.endswith(suffix)]
            if len(suffixed) == 1:
                return suffixed[0], 'telegram_id_plus_short_id'
        if len(candidates) > 1:
            return None, 'ambiguous_telegram_id'

    if user is not None:
        rebuilt = _rebuild_username(user, suffix)
        if rebuilt:
            panel_user = index.by_username.get(rebuilt)
            if panel_user is not None and panel_user.id not in claimed:
                return panel_user, 'reconstructed_username'

    email = (getattr(user, 'email', None) or '').strip().lower() if user is not None else ''
    if email:
        candidates = index.by_email.get(email, [])
        if len(candidates) == 1:
            return candidates[0], 'email_unique'
        if len(candidates) > 1:
            return None, 'ambiguous_email'

    return None, 'no_surviving_identifier'


def _rebuild_username(user: User, suffix: str) -> str | None:
    """Best-effort reconstruction of the panel username from local data."""
    try:
        return settings.build_remnawave_subscription_username(
            full_name=getattr(user, 'full_name', '') or '',
            username=getattr(user, 'username', None),
            telegram_id=getattr(user, 'telegram_id', None),
            email=getattr(user, 'email', None),
            user_id=int(user.id),
            suffix=suffix,
        )
    except Exception as exc:  # reconstruction is best-effort
        logger.debug('backfill: username reconstruction failed', user_id=user.id, error=str(exc))
        return None


async def backfill_remnawave_ids(db: AsyncSession, *, dry_run: bool = True) -> BackfillReport:
    """Populate ``remnawave_id`` on subscriptions, users and grace sessions.

    Idempotent: rows that already carry a numeric id are skipped, so the
    backfill can be re-run after fixing whatever blocked the first pass.
    """
    report = BackfillReport(dry_run=dry_run)

    service = RemnaWaveService()
    service._refresh_configuration()
    if not service.is_configured:
        raise RuntimeError(f'RemnaWave panel is not configured: {service.configuration_error}')

    panel_users = await _load_panel_roster(service)
    report.panel_users = len(panel_users)
    index = _PanelIndex(panel_users)
    logger.info('backfill: panel roster loaded', panel_users=len(panel_users))

    # Порядок решает, какая строка получит id внутри «сиблинг-группы» (несколько
    # подписок одного пользователя на один панельный аккаунт). Колонка частично
    # уникальна — id достанется ровно одной. Берём ЖИВУЮ: по id это был бы самый
    # старый ряд, то есть конвертированный триал, и тогда grace-сессия текущей
    # подписки осталась бы без идентичности (для неё это жёсткий data fault), а
    # вебхук резолвил бы события на мёртвую строку.
    _alive_first = case((Subscription.status.in_(_ALIVE_STATUSES), 0), else_=1)
    subscriptions = (
        (
            await db.execute(
                select(Subscription)
                .where(Subscription.remnawave_id.is_(None))
                .order_by(_alive_first, Subscription.end_date.desc(), Subscription.id.desc())
            )
        )
        .scalars()
        .all()
    )
    # Only rows that ever had a panel link need one back; a never-provisioned
    # draft legitimately has no identity.
    subscriptions = [s for s in subscriptions if s.remnawave_uuid or s.remnawave_short_uuid]
    report.subscriptions_total = len(subscriptions)

    user_ids = {int(s.user_id) for s in subscriptions}
    users_by_id: dict[int, User] = {}
    if user_ids:
        rows = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        users_by_id = {int(u.id): u for u in rows}

    # panel id -> the subscription that claimed it, so a double-assignment is
    # reported instead of blowing up on the partial unique index at flush time.
    # Primed with ids a PREVIOUS run already persisted: those rows are excluded
    # from `subscriptions` by the IS NULL filter, so without priming a re-run
    # would happily hand an already-owned panel id to another row and abort the
    # whole transaction on the unique index.
    _persisted = (
        await db.execute(
            select(Subscription.id, Subscription.remnawave_id, Subscription.user_id).where(
                Subscription.remnawave_id.isnot(None)
            )
        )
    ).all()
    claimed: dict[int, str] = {int(panel_id): f'#{sub_id} (persisted)' for sub_id, panel_id, _ in _persisted}
    # Владелец приминговывается ВМЕСТЕ с id. Без этого повторный прогон не мог
    # опознать сиблинга уже записанной строки, объявлял несуществующий
    # межпользовательский конфликт и откатывал весь прогон — а повторный прогон
    # здесь штатный сценарий, потому что первый всегда завершается «частично».
    _primed_owner: dict[int, int] = {int(panel_id): int(user_id) for _, panel_id, user_id in _persisted}
    # Same for shortUuid: we must not copy one onto a row when another row
    # already stores it — the index is deliberately non-unique, so nothing in
    # the database would reject the duplicate, and webhook subscription lookup
    # uses that column as a fallback key.
    taken_short_uuids: set[str] = {
        value
        for (value,) in (
            await db.execute(
                select(Subscription.remnawave_short_uuid).where(Subscription.remnawave_short_uuid.isnot(None))
            )
        ).all()
        if value
    }
    resolved_by_subscription: dict[int, int] = {}
    # panel id -> bot user, чтобы отличить «две подписки ОДНОГО пользователя
    # смотрят на один панельный аккаунт» (норма) от «на один аккаунт претендуют
    # РАЗНЫЕ пользователи» (настоящий конфликт).
    claimed_owner: dict[int, int] = dict(_primed_owner)

    def assign(subscription: Subscription, panel_user: RemnaWaveUser, strategy: str) -> None:
        claimed[panel_user.id] = str(subscription.id)
        claimed_owner[panel_user.id] = int(subscription.user_id)
        resolved_by_subscription[int(subscription.id)] = panel_user.id
        subscription.remnawave_id = panel_user.id
        # Refresh the short uuid while we have authoritative data — it is the
        # key any future re-run depends on.
        if panel_user.short_uuid and not subscription.remnawave_short_uuid:
            if panel_user.short_uuid not in taken_short_uuids:
                subscription.remnawave_short_uuid = panel_user.short_uuid
                taken_short_uuids.add(panel_user.short_uuid)
        report.subscriptions_resolved += 1
        report.by_strategy[strategy] += 1

    def is_expected_sibling(subscription: Subscription, panel_user: RemnaWaveUser) -> bool:
        """Тот же панельный аккаунт у ДРУГОЙ подписки того же пользователя.

        В single-tariff это норма, а не ошибка: все подписки одного бот-юзера
        по определению указывают на один панельный аккаунт (истёк триал →
        вставляется новая строка, старая сохраняет тот же shortUuid).
        Считать это конфликтом означало откатывать весь бэкфил на любой
        инсталляции, которая когда-либо работала в single-tariff, — то есть
        сделать обязательный шаг миграции невыполнимым.

        Идентичность в этом случае живёт на `users.remnawave_id`, а
        `subscriptions.remnawave_id` получает только одна строка: колонка
        частично уникальна, двум строкам один id не присвоить.
        """
        return claimed_owner.get(panel_user.id) == int(subscription.user_id)

    def record_expected_sibling(subscription: Subscription, panel_user: RemnaWaveUser) -> None:
        # НЕ конфликт и не откат: строка просто остаётся без id. В multi-tariff
        # общий аккаунт у двух подписок — это ненормально, но ронять весь
        # прогон из-за одной пары нельзя: инсталляция, переключённая из
        # single-tariff в multi, штатно несёт такие пары, и откат заблокировал
        # бы миграцию целиком. Ничего не записывается, оператор видит строку в
        # отчёте и разбирается точечно.
        report.unresolved.append(
            UnresolvedRow(
                kind='subscription',
                row_id=int(subscription.id),
                reason='sibling_shares_panel_account',
                remnawave_uuid=subscription.remnawave_uuid,
                remnawave_short_uuid=subscription.remnawave_short_uuid,
            )
        )

    def record_conflict(subscription: Subscription, panel_user: RemnaWaveUser) -> None:
        report.conflicts.append(
            f'panel user {panel_user.id} matched by subscription {claimed[panel_user.id]} and {subscription.id}'
        )
        report.unresolved.append(
            UnresolvedRow(
                kind='subscription',
                row_id=int(subscription.id),
                reason='conflict_panel_id_already_claimed',
                remnawave_uuid=subscription.remnawave_uuid,
                remnawave_short_uuid=subscription.remnawave_short_uuid,
            )
        )

    # Pass 1 takes every exact (shortUuid) match; pass 2 then works only with
    # panel accounts nothing has claimed. Order of trust, not order of row id.
    pending: list[Subscription] = []
    for subscription in subscriptions:
        user = users_by_id.get(int(subscription.user_id))
        panel_user, strategy = _match_subscription(subscription, user, index, claimed, exact_only=True)
        if panel_user is None:
            if strategy == 'no_exact_key':
                pending.append(subscription)
            else:
                report.unresolved.append(
                    UnresolvedRow(
                        kind='subscription',
                        row_id=int(subscription.id),
                        reason=strategy,
                        remnawave_uuid=subscription.remnawave_uuid,
                        remnawave_short_uuid=subscription.remnawave_short_uuid,
                    )
                )
            continue
        if panel_user.id in claimed:
            if is_expected_sibling(subscription, panel_user):
                record_expected_sibling(subscription, panel_user)
            else:
                record_conflict(subscription, panel_user)
            continue
        assign(subscription, panel_user, strategy)

    for subscription in pending:
        user = users_by_id.get(int(subscription.user_id))
        panel_user, strategy = _match_subscription(subscription, user, index, claimed)
        if panel_user is None:
            report.unresolved.append(
                UnresolvedRow(
                    kind='subscription',
                    row_id=int(subscription.id),
                    reason=strategy,
                    remnawave_uuid=subscription.remnawave_uuid,
                    remnawave_short_uuid=subscription.remnawave_short_uuid,
                )
            )
            continue
        if panel_user.id in claimed:
            if is_expected_sibling(subscription, panel_user):
                record_expected_sibling(subscription, panel_user)
                continue
            # An exact match already owns this panel account — a weaker
            # strategy must never take it away.
            report.unresolved.append(
                UnresolvedRow(
                    kind='subscription',
                    row_id=int(subscription.id),
                    reason='panel_id_owned_by_stronger_match',
                    remnawave_uuid=subscription.remnawave_uuid,
                    remnawave_short_uuid=subscription.remnawave_short_uuid,
                )
            )
            continue
        assign(subscription, panel_user, strategy)

    await _prefer_alive_sibling(db, subscriptions, resolved_by_subscription, claimed, report)

    await _backfill_users(db, index, users_by_id, resolved_by_subscription, subscriptions, report)
    await _backfill_grace_sessions(db, resolved_by_subscription, report)

    if dry_run:
        await db.rollback()
    elif report.conflicts:
        # A conflict means two bot rows believe they own one panel account, so
        # at least one of the writes in this transaction is wrong. Persisting
        # "whichever came first" would silently point a subscription at another
        # person's VPN account — roll back and let an operator look.
        await db.rollback()
        logger.error(
            'backfill: rolled back, panel-id conflicts must be resolved first',
            conflicts=len(report.conflicts),
            sample=report.conflicts[:5],
        )
    else:
        await db.commit()

    logger.info('backfill: finished', **report.as_dict())
    return report


async def _prefer_alive_sibling(
    db: AsyncSession,
    subscriptions: list[Subscription],
    resolved_by_subscription: dict[int, int],
    claimed: dict[int, str],
    report: BackfillReport,
) -> None:
    """Передать панельный id живой подписке, если его забрала мёртвая.

    Порядка обхода мало. Точный ключ (`shortUuid`) сплошь и рядом остаётся
    ТОЛЬКО на мёртвой строке: при замене подписки `crud/subscription.py`
    вставляет новую строку без него, а конвертированный триал свой сохраняет.
    Первый проход берёт исключительно точные совпадения, поэтому id достаётся
    мёртвой строке независимо от сортировки — а живая уходит в
    `no_surviving_identifier`.

    Последствия ровно те, ради которых сортировка и вводилась: grace-сессия
    открывается на ТЕКУЩЕЙ подписке, и без id она становится нечитаемой
    (жёсткий data fault), а вебхук резолвит события на мёртвую строку, потому
    что фильтрует по `Subscription.remnawave_id` раньше, чем по shortUuid.

    Только для single-tariff: там у пользователя один панельный аккаунт, и
    любая его подписка адресует именно его. В multi-tariff у каждой подписки
    свой аккаунт, и переносить id между ними нельзя.
    """
    if settings.is_multi_tariff_enabled():
        return

    by_user: dict[int, list[Subscription]] = defaultdict(list)
    for subscription in subscriptions:
        by_user[int(subscription.user_id)].append(subscription)

    for rows in by_user.values():
        resolved = [r for r in rows if int(r.id) in resolved_by_subscription]
        if len(resolved) != 1:
            continue
        holder = resolved[0]
        if holder.status in _ALIVE_STATUSES:
            continue
        alive = [r for r in rows if r.status in _ALIVE_STATUSES and int(r.id) not in resolved_by_subscription]
        if len(alive) != 1:
            continue
        target = alive[0]

        panel_id = resolved_by_subscription.pop(int(holder.id))

        # Снять id со старой строки и СБРОСИТЬ отдельно, до присвоения новой.
        # `uq_subscriptions_remnawave_id` проверяется на каждом стейтменте, а
        # порядок UPDATE'ов внутри одного flush выбирает SQLAlchemy — без
        # явного разделения обе строки могли бы на мгновение держать один id
        # и словить IntegrityError на живой БД.
        holder.remnawave_id = None
        await db.flush((holder,))
        target.remnawave_id = panel_id
        await db.flush((target,))

        resolved_by_subscription[int(target.id)] = panel_id
        claimed[panel_id] = str(target.id)
        # Строка-приёмник была записана как нерешённая в проходе выше — теперь
        # у неё есть id, и в отчёте её быть не должно.
        report.unresolved = [
            row for row in report.unresolved if not (row.kind == 'subscription' and row.row_id == int(target.id))
        ]
        report.by_strategy['moved_to_alive_sibling'] += 1
        logger.info(
            'backfill: панельный id передан живой подписке',
            panel_user_id=panel_id,
            from_subscription_id=int(holder.id),
            to_subscription_id=int(target.id),
        )


async def _backfill_users(
    db: AsyncSession,
    index: _PanelIndex,
    users_by_id: dict[int, User],
    resolved_by_subscription: dict[int, int],
    subscriptions: list[Subscription],
    report: BackfillReport,
) -> None:
    """Give each bot user the panel id of its own panel account.

    ТОЛЬКО в single-tariff. Там бот-пользователю соответствует ровно один
    панельный аккаунт, и `users.remnawave_id` — его канонический адрес.

    В multi-tariff колонка не заполняется вообще. Идентичность живёт на
    подписке, а на User колонка исторически пуста — именно поэтому около
    десятка фолбэков вида `sub.remnawave_id or user.remnawave_id` (продление
    из админки, платный сброс трафика, возврат в канал, экран устройств)
    безопасно пропускали панель. Заполнив её, бэкфил оживил бы их: подписка,
    чей собственный id не разрезолвился, начала бы адресовать панельный
    аккаунт СОСЕДНЕГО тарифа — продление переписывало бы чужой expireAt и
    сквады, а сброс трафика обнулял бы чужой счётчик.

    Гейт по `users.remnawave_uuid` для этого недостаточен: инсталляция,
    начинавшая в single-tariff и переключённая на multi-tariff, несёт эту
    колонку заполненной у всех «дореформенных» пользователей.
    """
    if settings.is_multi_tariff_enabled():
        logger.info('backfill: multi-tariff — users.remnawave_id намеренно не заполняется')
        return

    per_user: dict[int, set[int]] = defaultdict(set)
    for subscription in subscriptions:
        panel_id = resolved_by_subscription.get(int(subscription.id))
        if panel_id is not None:
            per_user[int(subscription.user_id)].add(panel_id)

    gate = User.remnawave_uuid.isnot(None)
    candidate_user_ids = set(per_user)
    if candidate_user_ids:
        gate = or_(gate, User.id.in_(candidate_user_ids))

    pending_users = (await db.execute(select(User).where(User.remnawave_id.is_(None), gate))).scalars().all()

    for user in pending_users:
        candidates = per_user.get(int(user.id), set())
        if len(candidates) == 1:
            user.remnawave_id = next(iter(candidates))
            report.users_resolved += 1
            report.by_strategy['user_from_subscription'] += 1
            continue

        telegram_id = getattr(user, 'telegram_id', None)
        if telegram_id is not None:
            panel_candidates = index.by_telegram_id.get(int(telegram_id), [])
            if len(panel_candidates) == 1:
                user.remnawave_id = panel_candidates[0].id
                report.users_resolved += 1
                report.by_strategy['user_from_telegram_id'] += 1
                continue

        report.unresolved.append(
            UnresolvedRow(
                kind='user',
                row_id=int(user.id),
                reason='ambiguous_or_missing_panel_account',
                remnawave_uuid=user.remnawave_uuid,
            )
        )
        users_by_id.setdefault(int(user.id), user)


async def _backfill_grace_sessions(
    db: AsyncSession,
    resolved_by_subscription: dict[int, int],
    report: BackfillReport,
) -> None:
    """Grace sessions inherit identity from their subscription (1:1 by FK)."""
    sessions = (
        (await db.execute(select(GraceAccessSessionModel).where(GraceAccessSessionModel.remnawave_id.is_(None))))
        .scalars()
        .all()
    )

    for session in sessions:
        panel_id = resolved_by_subscription.get(int(session.subscription_id))
        if panel_id is None:
            # The subscription may already have been backfilled by an earlier
            # run; fall back to reading it directly.
            panel_id = (
                await db.execute(select(Subscription.remnawave_id).where(Subscription.id == session.subscription_id))
            ).scalar_one_or_none()

        if panel_id is None:
            report.unresolved.append(
                UnresolvedRow(
                    kind='grace_session',
                    row_id=str(session.id),
                    reason='parent_subscription_unresolved',
                    remnawave_uuid=session.remnawave_uuid,
                )
            )
            continue

        session.remnawave_id = int(panel_id)
        report.grace_sessions_resolved += 1
