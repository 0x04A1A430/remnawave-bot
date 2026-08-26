"""Многоуровневые реферальные награды: деньги и/или дни подписки.

Схема включается настройкой ``REFERRAL_REWARD_SCHEME='levels'``. Пока она в
``legacy`` (значение по умолчанию), этот модуль не участвует в выдаче наград вовсе
— прежний путь в ``referral_service.process_referral_topup`` работает без единого
изменения в поведении. Так обновление бота не меняет денежные начисления на живых
установках: смена схемы обязана быть осознанным действием админа.

Три вещи, которые здесь важнее остального:

**Дни — не деньги.** Награда днями пишется в ledger с ``amount_kopeks=0`` и
ненулевым ``days_granted``. Подмешать дни в денежную сумму нельзя: на ней стоит и
статистика, и расчёт доступного к выводу реферального баланса — выводить «дни»
через кассу невозможно.

**Строка ledger'а принадлежит получателю.** ``user_id`` — тот, кому награда
досталась. Для пригласившего это прежний смысл колонок, поэтому существующие
выборки не трогаются вовсе; для приглашённого пара зеркалится, и такие строки
помечены причиной из ``REFEREE_DIRECTED_REASONS``.

**Ненастроенное молчит.** Процент уровня берётся ровно из его строки в БД, без
отката к ``REFERRAL_COMMISSION_PERCENT`` — ни на глубоких уровнях, ни на первом.
Иначе одно переключение схемы начало бы платить всем «дедушкам» по 25%, а уровень
с бонусом только приглашённому втихую платил бы и пригласившему. Личный процент
партнёра (``User.referral_commission_percent``) перебивает только первый уровень —
он про отношения с прямым приглашённым.

**Цепочка обходится с защитой от петли.** ``referred_by_id`` не гарантирует
ацикличность: исторические данные и ручные правки в админке способны замкнуть
A→B→A, и наивный подъём вверх повесил бы обработку пополнения намертво.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.referral_reward_level import normalize_mode, normalize_trigger
from app.database.crud.user import get_user_by_id
from app.database.models import (
    ReferralEarning,
    ReferralRewardLevel,
    ReferralRewardMode,
    ReferralRewardTrigger,
    ReferralRewardType,
    SubscriptionStatus,
    User,
)


logger = structlog.get_logger(__name__)


class RewardEvent:
    """Что именно произошло с приглашённым."""

    REGISTRATION = 'registration'
    FIRST_TOPUP = 'first_topup'
    REPEAT_TOPUP = 'repeat_topup'


# Какие триггеры уровня срабатывают на каком событии. ``every_topup`` включает и
# первое пополнение: «каждое» без первого было бы ловушкой для админа.
_TRIGGERS_BY_EVENT: dict[str, frozenset[str]] = {
    RewardEvent.REGISTRATION: frozenset({ReferralRewardTrigger.REGISTRATION.value}),
    RewardEvent.FIRST_TOPUP: frozenset(
        {ReferralRewardTrigger.FIRST_TOPUP.value, ReferralRewardTrigger.EVERY_TOPUP.value}
    ),
    RewardEvent.REPEAT_TOPUP: frozenset({ReferralRewardTrigger.EVERY_TOPUP.value}),
}


@dataclass(frozen=True)
class LevelConfig:
    """Снимок правила уровня, отвязанный от сессии.

    Именно снимок, а не ORM-объект: кэш переживает сессию, из которой был
    прочитан, а обращение к атрибуту протухшего объекта в async-коде даёт
    MissingGreenlet вместо понятной ошибки.
    """

    level: int
    is_active: bool
    reward_mode: str
    trigger: str
    referrer_percent: int | None
    referrer_fixed_kopeks: int | None
    referrer_days: int
    referrer_tariff_id: int | None
    referee_fixed_kopeks: int | None
    referee_days: int
    referee_tariff_id: int | None
    max_payments: int
    # Момент создания правила. Нужен лимиту: «сколько раз ЭТОТ уровень заплатил»
    # не должно включать начисления, сделанные до того, как уровень появился.
    created_at: object | None = None

    @property
    def money_enabled(self) -> bool:
        return self.reward_mode in (ReferralRewardMode.MONEY.value, ReferralRewardMode.BOTH.value)

    @property
    def days_enabled(self) -> bool:
        return self.reward_mode in (ReferralRewardMode.DAYS.value, ReferralRewardMode.BOTH.value)

    def matches(self, event: str) -> bool:
        return self.is_active and self.trigger in _TRIGGERS_BY_EVENT.get(event, frozenset())


@dataclass(frozen=True)
class RewardComponent:
    """Одна выдача одному получателю на одном уровне."""

    recipient_id: int
    # Пригласивший этого уровня. Хранится в компоненте, а не вычисляется заново
    # при выдаче: второй обход цепочки мог бы дать другой ответ, если между
    # расчётом и выдачей кто-то поправил привязку.
    referrer_id: int
    level: int
    money_kopeks: int
    days: int
    tariff_id: int | None
    is_referrer: bool
    percent: int

    @property
    def is_empty(self) -> bool:
        return self.money_kopeks <= 0 and self.days <= 0


class ReferralRewardLevelService:
    """Кэш конфигурации уровней.

    Кэш обязателен: без него каждое пополнение читало бы таблицу столько раз,
    сколько звеньев в цепочке. Бот и кабинет живут в одном процессе, поэтому
    правка из любого из них обязана дёргать ``invalidate_cache``.
    """

    _cache: dict[int, LevelConfig] | None = None
    _lock: asyncio.Lock = asyncio.Lock()
    # Поколение конфигурации. Без него сброс кэша, случившийся ПОКА идёт чтение из
    # базы, затирается результатом этого чтения: сохранится снимок, сделанный до
    # правки, и он останется в кэше навсегда — админ будет видеть новое правило и
    # получать начисления по старому.
    _generation: int = 0

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cache = None
        cls._generation += 1

    @classmethod
    async def _load(cls, db: AsyncSession) -> dict[int, LevelConfig]:
        if cls._cache is not None:
            return cls._cache

        async with cls._lock:
            if cls._cache is not None:
                return cls._cache

            generation = cls._generation
            result = await db.execute(select(ReferralRewardLevel).order_by(ReferralRewardLevel.level.asc()))
            configs: dict[int, LevelConfig] = {}
            for row in result.scalars().all():
                configs[row.level] = LevelConfig(
                    level=row.level,
                    is_active=bool(row.is_active),
                    reward_mode=normalize_mode(row.reward_mode),
                    trigger=normalize_trigger(row.trigger),
                    referrer_percent=row.referrer_percent,
                    referrer_fixed_kopeks=row.referrer_fixed_kopeks,
                    referrer_days=int(row.referrer_days or 0),
                    referrer_tariff_id=row.referrer_tariff_id,
                    referee_fixed_kopeks=row.referee_fixed_kopeks,
                    referee_days=int(row.referee_days or 0),
                    referee_tariff_id=row.referee_tariff_id,
                    max_payments=int(row.max_payments or 0),
                    created_at=row.created_at,
                )
            if generation == cls._generation:
                cls._cache = configs
            else:
                # Конфигурацию правили, пока мы её читали: снимок уже устарел.
                # Отдаём прочитанное вызывающему, но в кэш не кладём — следующий
                # вызов перечитает.
                logger.debug('Конфигурация уровней изменилась во время чтения, кэш не заполняется')
            return configs

    @classmethod
    async def get_level(cls, db: AsyncSession, level: int) -> LevelConfig | None:
        return (await cls._load(db)).get(level)

    @classmethod
    async def get_all(cls, db: AsyncSession) -> dict[int, LevelConfig]:
        return dict(await cls._load(db))

    @classmethod
    async def has_any_active(cls, db: AsyncSession) -> bool:
        return any(cfg.is_active for cfg in (await cls._load(db)).values())


async def resolve_referrer_chain(db: AsyncSession, user: User, max_depth: int) -> list[tuple[int, User]]:
    """Цепочка пригласивших снизу вверх: [(1, прямой), (2, его пригласивший), ...].

    ``referred_by_id`` не гарантирует ацикличность — ручная правка в админке или
    исторические данные способны замкнуть A→B→A. Без множества посещённых такой
    обход крутился бы до предела глубины впустую, а с ним цикл честно обрывается
    на первом повторе.
    """
    chain: list[tuple[int, User]] = []
    seen: set[int] = {user.id}
    current = user
    level = 1

    while level <= max_depth:
        referrer_id = getattr(current, 'referred_by_id', None)
        if not referrer_id or referrer_id in seen:
            if referrer_id and referrer_id in seen:
                logger.warning(
                    'Цикл в реферальной цепочке, обход остановлен',
                    user_id=user.id,
                    repeated_referrer_id=referrer_id,
                    level=level,
                )
            break

        referrer = await get_user_by_id(db, referrer_id)
        if not referrer:
            logger.warning('Реферер из цепочки не найден', referrer_id=referrer_id, level=level)
            break

        seen.add(referrer.id)
        chain.append((level, referrer))
        current = referrer
        level += 1

    return chain


def _resolve_percent(config: LevelConfig, referrer: User) -> int:
    """Процент комиссии уровня.

    Личный процент партнёра действует только на первом уровне: это условие его
    работы с ПРЯМЫМИ приглашёнными, а не множитель на всю пирамиду под ним.

    Пустой процент — это ноль, а не ``REFERRAL_COMMISSION_PERCENT``. Отката к
    глобальной настройке нет ни на одном уровне, включая первый: строку уровня
    заводит админ руками, и «уровень 1: бонус только приглашённому» не должно
    неожиданно платить пригласившему 25% из старого ключа. Перенос прежних
    настроек в уровень — отдельное явное действие в админке, а не побочный эффект.
    """
    if config.level == 1:
        personal = getattr(referrer, 'referral_commission_percent', None)
        if personal is not None:
            return max(0, min(100, int(personal)))

    if config.referrer_percent is None:
        return 0
    return max(0, min(100, int(config.referrer_percent)))


async def count_level_payments(db: AsyncSession, referrer_id: int, referral_id: int, level: int, since=None) -> int:
    """Сколько раз этот уровень уже платил за эту пару.

    Считаются только денежные строки: лимит ``max_payments`` унаследован от
    ``REFERRAL_MAX_COMMISSION_PAYMENTS`` и всегда означал число оплаченных
    комиссий. Дни ограничиваются собственным ``referrer_days``, а не этим счётчиком.

    ``since`` — момент создания правила уровня, и отсекает он не мелочь. Денежные
    строки классической схемы бэкфиллены в ``level=1`` и по причине неотличимы от
    уровневых. Без этой границы установка, год проработавшая на классической
    схеме, при переключении получала бы лимит, исчерпанный ЗАДОЛГО до того, как
    админ его задал: он ставит «не больше 5 выплат на реферала» и не получает ни
    одной. Считается то, что заплатил этот уровень, а не вся история пары.
    """
    query = select(func.count(ReferralEarning.id)).where(
        ReferralEarning.user_id == referrer_id,
        ReferralEarning.referral_id == referral_id,
        ReferralEarning.level == level,
        ReferralEarning.reward_type == ReferralRewardType.MONEY.value,
        ReferralEarning.amount_kopeks > 0,
    )
    if since is not None:
        query = query.where(ReferralEarning.created_at >= since)

    result = await db.execute(query)
    return int(result.scalar() or 0)


async def build_reward_components(
    db: AsyncSession,
    referee: User,
    *,
    event: str,
    topup_amount_kopeks: int,
) -> list[RewardComponent]:
    """Что и кому причитается по всей цепочке. Ничего не начисляет.

    Отделено от выдачи умышленно: расчёт можно проверить тестом без базы платежей,
    без Remnawave и без телеграма, а превью для админки строится тем же кодом, что
    и реальное начисление — расхождение показанного и выданного невозможно.
    """
    if not settings.is_referral_levels_scheme():
        return []

    max_depth = settings.get_referral_max_level_depth()
    chain = await resolve_referrer_chain(db, referee, max_depth)
    if not chain:
        return []

    components: list[RewardComponent] = []
    referee_already_paid = False

    for level, referrer in chain:
        config = await ReferralRewardLevelService.get_level(db, level)
        if config is None or not config.matches(event):
            continue

        percent = _resolve_percent(config, referrer) if config.money_enabled else 0
        money = 0
        if config.money_enabled:
            if percent > 0 and topup_amount_kopeks > 0:
                money += int(topup_amount_kopeks * percent / 100)
            if config.referrer_fixed_kopeks:
                money += max(0, int(config.referrer_fixed_kopeks))

        if money > 0 and config.max_payments > 0:
            paid = await count_level_payments(db, referrer.id, referee.id, level, since=config.created_at)
            if paid >= config.max_payments:
                logger.info(
                    'Лимит платежей уровня исчерпан, деньги не начисляются',
                    referrer_id=referrer.id,
                    referral_id=referee.id,
                    level=level,
                    max_payments=config.max_payments,
                )
                money = 0
                percent = 0

        days = max(0, config.referrer_days) if config.days_enabled else 0

        referrer_component = RewardComponent(
            recipient_id=referrer.id,
            referrer_id=referrer.id,
            level=level,
            money_kopeks=money,
            days=days,
            tariff_id=config.referrer_tariff_id,
            is_referrer=True,
            percent=percent,
        )
        if not referrer_component.is_empty:
            components.append(referrer_component)

        # Приглашённому платим ровно один раз за событие — со СВОЕГО, первого
        # сработавшего уровня. Иначе трёхуровневая цепочка выдала бы ему бонус
        # трижды за одно пополнение.
        if not referee_already_paid:
            referee_money = max(0, int(config.referee_fixed_kopeks or 0)) if config.money_enabled else 0
            referee_days = max(0, config.referee_days) if config.days_enabled else 0
            referee_component = RewardComponent(
                recipient_id=referee.id,
                referrer_id=referrer.id,
                level=level,
                money_kopeks=referee_money,
                days=referee_days,
                tariff_id=config.referee_tariff_id,
                is_referrer=False,
                percent=0,
            )
            if not referee_component.is_empty:
                components.append(referee_component)
                referee_already_paid = True

    return components


@dataclass(frozen=True)
class DaysGrant:
    """Исход выдачи дней. ``days`` = 0 означает, что дни не легли никуда."""

    days: int = 0
    subscription_id: int | None = None
    tariff_name: str | None = None
    failure: str | None = None


@dataclass
class GrantOutcome:
    """Что реально выдали по одному компоненту.

    Отдельный тип, а не bool: уведомление обязано называть выданное точно.
    Написать «начислено 7 дней», когда подписки для них не нашлось, — хуже, чем
    промолчать: пользователь пойдёт искать дни, которых нет.
    """

    component: RewardComponent
    money_credited: int = 0
    days_credited: int = 0
    subscription_id: int | None = None
    tariff_name: str | None = None
    failure: str | None = None

    @property
    def granted_anything(self) -> bool:
        return self.money_credited > 0 or self.days_credited > 0


# Статусы, в подписку с которыми нельзя класть награду.
#
# PENDING — неоплаченный черновик: активация переписывает его срок, и выданные
# дни исчезают, оставив в ledger'е запись о доставленной награде.
# DISABLED — подписку отключили осознанно (бан, действие админа), а
# ``extend_subscription`` поднимает EXPIRED/DISABLED обратно в ACTIVE. Бесплатная
# награда не должна отменять чужое решение и возвращать человеку доступ.
_DAYS_TARGET_BLOCKED_STATUSES = {
    SubscriptionStatus.PENDING.value: 'pending_draft',
    SubscriptionStatus.DISABLED.value: 'subscription_disabled',
}

# LIMITED сюда намеренно НЕ входит. Подписка с исчерпанным трафиком принадлежит
# живому платящему клиенту, и отказывать ему в награде было бы хуже побочного
# эффекта: ``extend_subscription`` поднимет её в ACTIVE, не восстановив трафик.
# Этот подъём — общее поведение продления, которым пользуются и покупки; заводить
# ради награды отдельный путь опаснее, чем принять его. Панель на ближайшей
# синхронизации вернёт лимит по собственному учёту трафика.


async def _resolve_days_target(db: AsyncSession, user: User, tariff_id: int | None) -> tuple[object | None, str | None]:
    """Подписка, в которую лягут дни, и причина отказа.

    Возвращает ``(подписка, None)`` либо ``(None, причина)``. Разделение важно:
    «подписки нет» разрешает завести новую, а «подписка есть, но трогать её
    нельзя» — запрещает и это тоже.

    Тариф в правиле уровня — это и есть ответ на вопрос «куда попадут дни».
    Он важен именно в мультитарифе: там подписок несколько, а спросить пользователя
    некого — награда приходит асинхронно, на чужом пополнении. Без тарифа берём
    основную подписку, как это делает любое другое продление в классическом режиме.
    """
    from app.database.crud.subscription import get_subscription_by_user_and_tariff, get_subscription_by_user_id

    if tariff_id is not None:
        subscription = await get_subscription_by_user_and_tariff(db, user.id, tariff_id, include_inactive=True)
    else:
        subscription = await _pick_primary_paid_subscription(db, user)
        if subscription is None:
            subscription = await get_subscription_by_user_id(db, user.id)

    if subscription is None:
        return None, None

    blocked = _DAYS_TARGET_BLOCKED_STATUSES.get((subscription.status or '').lower())
    if blocked:
        logger.info(
            'Дни не выданы: подписка в состоянии, куда награду класть нельзя',
            user_id=user.id,
            subscription_id=subscription.id,
            status=subscription.status,
            reason=blocked,
        )
        return None, blocked

    return subscription, None


# Статусы, при которых подписка считается живой. Живой ТРИАЛ — единственное, из-за
# чего нельзя звать create_paid_subscription: он конвертирует такой триал в платную
# подписку, то есть бесплатно снимает триальный статус и выключает человека из
# авто-продления (класс бага #629889).
_ALIVE_SUBSCRIPTION_STATUSES = frozenset({'active', 'trial', 'limited'})


async def _pick_primary_paid_subscription(db: AsyncSession, user: User):
    """Платная подписка, в которую пойдут дни, когда тариф в правиле не задан.

    ``get_subscription_by_user_id`` сортирует по СТАТУСУ и не смотрит на
    ``is_trial``: у пользователя с несколькими подписками награда легко уходила в
    триал мимо оплаченной. В мультитарифе это обычная ситуация — подписок много.

    Правило выбора и его порядок:

    1. только живые подписки, не считая тех, куда награду класть нельзя;
    2. оплаченные вперёд триальных — за них человек заплатил;
    3. посуточные тарифы в конец: там плата списывается за день, и добавленные дни
       ведут себя не так, как на обычной подписке;
    4. при равенстве — с самым поздним сроком окончания, затем меньший id.

    Пункт 4 нужен не «для красоты»: без полного порядка одна и та же награда при
    двух одинаковых подписках могла бы уходить то в одну, то в другую, и понять
    задним числом, куда делись дни, стало бы невозможно.

    ``None`` — подходящей платной подписки нет; вызывающий откатывается к обычному
    выбору основной, чтобы владелец одного лишь триала награду всё же получил.
    """
    from app.database.crud.subscription import get_all_subscriptions_by_user_id

    candidates = [
        sub
        for sub in await get_all_subscriptions_by_user_id(db, user.id)
        if (sub.status or '').lower() in _ALIVE_SUBSCRIPTION_STATUSES
        and (sub.status or '').lower() not in _DAYS_TARGET_BLOCKED_STATUSES
    ]
    if not candidates:
        return None

    def _is_daily(subscription) -> bool:
        tariff = getattr(subscription, 'tariff', None)
        return bool(getattr(tariff, 'is_daily', False))

    candidates.sort(
        key=lambda sub: (
            bool(sub.is_trial),
            _is_daily(sub),
            -(sub.end_date.timestamp() if sub.end_date else 0),
            sub.id,
        )
    )
    chosen = candidates[0]

    if len(candidates) > 1:
        logger.info(
            'Дни за реферала: выбрана подписка из нескольких',
            user_id=user.id,
            subscription_id=chosen.id,
            is_trial=bool(chosen.is_trial),
            candidates=len(candidates),
        )
    return chosen


async def _create_subscription_for_days(db: AsyncSession, user: User, days: int, tariff_id: int):
    """Завести подписку под награду, когда подписки нужного тарифа у него нет.

    Условие «нет ни одной подписки» было бы шире реальной опасности: человек с
    платной подпиской на другом тарифе молча не получал бы настроенные админом дни,
    хотя завести ему подписку нужного тарифа в мультитарифе совершенно законно.
    Отказываем ровно в двух случаях:

    * есть ЖИВОЙ ТРИАЛ — ``create_paid_subscription`` конвертировал бы его в
      платную подписку;
    * мультитариф выключен, а подписка уже есть — в этом режиме она одна, и вторая
      сломала бы его инварианты.
    """
    from app.database.crud.subscription import create_paid_subscription, get_all_subscriptions_by_user_id
    from app.database.crud.tariff import get_tariff_by_id

    existing = await get_all_subscriptions_by_user_id(db, user.id)

    # Неоплаченный черновик этого же тарифа выборка выше не видит: она не смотрит
    # PENDING. Создать рядом вторую подписку того же тарифа означает сломать
    # пользователю оплату — активировать черновик уже не выйдет, партиальный
    # уникальный индекс не даст.
    pending_draft = any(
        sub.tariff_id == tariff_id and (sub.status or '').lower() == SubscriptionStatus.PENDING.value
        for sub in existing
    )
    if pending_draft:
        logger.info(
            'Дни не выданы: этот тариф занят неоплаченным черновиком подписки',
            user_id=user.id,
            tariff_id=tariff_id,
        )
        return None

    alive_trial = any(sub.is_trial and (sub.status or '') in _ALIVE_SUBSCRIPTION_STATUSES for sub in existing)
    if alive_trial:
        logger.info(
            'Дни не выданы: у получателя живой триал, конвертировать его наградой нельзя',
            user_id=user.id,
            tariff_id=tariff_id,
        )
        return None

    if existing and not settings.is_multi_tariff_enabled():
        logger.info(
            'Дни не выданы: вне мультитарифа вторая подписка не заводится',
            user_id=user.id,
            tariff_id=tariff_id,
        )
        return None

    tariff = await get_tariff_by_id(db, tariff_id)
    if tariff is None:
        logger.warning('Тариф награды не найден, подписка не создаётся', tariff_id=tariff_id, user_id=user.id)
        return None

    return await create_paid_subscription(
        db=db,
        user_id=user.id,
        duration_days=days,
        traffic_limit_gb=tariff.traffic_limit_gb,
        device_limit=tariff.device_limit,
        connected_squads=list(tariff.allowed_squads or []),
        is_trial=False,
        tariff_id=tariff_id,
    )


async def grant_reward_days(db: AsyncSession, user: User, days: int, tariff_id: int | None) -> DaysGrant:
    """Выдать дни подписки. Возвращает исход, а не бросает.

    Награда за реферала — не покупка: ``is_trial`` не трогаем и тариф подписке не
    переназначаем. Поэтому ``extend_subscription`` вызывается без ``tariff_id``:
    с ним он снял бы триальный флаг и превратил триал в фантомную платную подписку
    (баг #629889), а сама подписка уже и так нужного тарифа — мы её по нему нашли.
    """
    from app.database.crud.subscription import extend_subscription
    from app.services.subscription_service import SubscriptionService

    if days <= 0:
        return DaysGrant()

    subscription, blocked = await _resolve_days_target(db, user, tariff_id)
    if blocked:
        # Подписка есть, но трогать её нельзя — заводить вторую тем более.
        return DaysGrant(failure=blocked)

    created = False
    if subscription is None:
        if tariff_id is None:
            # Классический режим без подписки: продлевать нечего, а заводить её
            # без указанного тарифа не из чего — параметры взять неоткуда.
            return DaysGrant(failure='no_subscription')
        subscription = await _create_subscription_for_days(db, user, days, tariff_id)
        created = subscription is not None
        if subscription is None:
            return DaysGrant(failure='no_subscription')

    if not created:
        await extend_subscription(db, subscription, days)

    try:
        await SubscriptionService().update_remnawave_user(db, subscription)
    except Exception as error:
        # Дни в базе уже есть — молча их не откатываем: расхождение с панелью
        # чинится следующей синхронизацией, а потеря начисленных дней — нет.
        logger.error(
            'Не удалось синхронизировать выданные дни с панелью',
            user_id=user.id,
            subscription_id=subscription.id,
            error=str(error),
        )

    # Название тарифа берётся отдельным запросом по идентификатору, а НЕ через
    # subscription.tariff. Связь у только что созданной или найденной подписки не
    # загружена, и обращение к ней — неявный запрос в базу: в async-сессии это
    # MissingGreenlet, то есть дни выданы, а строка ledger'а уже не записана.
    tariff_name = None
    tariff_id_for_name = subscription.tariff_id or tariff_id
    if tariff_id_for_name:
        from app.database.models import Tariff

        name_result = await db.execute(select(Tariff.name).where(Tariff.id == tariff_id_for_name))
        tariff_name = name_result.scalar_one_or_none()

    return DaysGrant(days=days, subscription_id=subscription.id, tariff_name=tariff_name)


# Причины начислений. Денежные намеренно совпадают с легаси-строками: на них
# завязаны все существующие выборки статистики и партнёрки, и уровень ≥2 — это
# та же комиссия, просто заработанная выше по цепочке.
REASON_FIRST_TOPUP = 'referral_first_topup'
REASON_COMMISSION = 'referral_commission_topup'
REASON_REGISTRATION_REWARD = 'referral_registration_reward'
REASON_DAYS_REFERRER = 'referral_days_reward'
REASON_DAYS_REFEREE = 'referral_days_bonus'

# Строки, где начисление ушло ПРИГЛАШЁННОМУ, а не пригласившему.
#
# Инвариант ledger'а: ``user_id`` — ПОЛУЧАТЕЛЬ награды, ``referral_id`` — вторая
# сторона пары. Для наград пригласившему это ровно прежний смысл (получатель —
# пригласивший, вторая сторона — приглашённый), поэтому ни одна существующая
# выборка не меняется. Для награды приглашённому колонки зеркалятся.
#
# Альтернатива — всегда держать ``user_id`` пригласившим — была бы хуже: тогда
# каждая из полусотни выборок вида ``WHERE user_id = :me`` приписывала бы
# пригласившему дни, выданные другому человеку, и каждую пришлось бы чинить
# отдельно. При зеркалировании чинить нужно ровно те запросы, что считают
# ``DISTINCT referral_id`` как «мои рефералы» — их два, и оба ниже помечены.
REFEREE_DIRECTED_REASONS = frozenset({REASON_DAYS_REFEREE})


def is_referee_directed(reason: str) -> bool:
    """Строка описывает награду приглашённому: ``referral_id`` в ней — пригласивший.

    Выборки, трактующие ``referral_id`` как «приглашённый мной», обязаны такие
    строки исключать, иначе пользователь получит в свои рефералы собственного
    пригласившего.
    """
    return reason in REFEREE_DIRECTED_REASONS


def _money_reason(event: str) -> str:
    if event == RewardEvent.REGISTRATION:
        return REASON_REGISTRATION_REWARD
    if event == RewardEvent.FIRST_TOPUP:
        return REASON_FIRST_TOPUP
    return REASON_COMMISSION


async def award_referral_rewards(
    db: AsyncSession,
    referee: User,
    *,
    event: str,
    topup_amount_kopeks: int = 0,
    bot=None,
) -> list[GrantOutcome]:
    """Посчитать и выдать награды всей цепочке. Возвращает фактически выданное.

    Порядок внутри одного получателя — сначала дни, потом деньги. Это не вкусовщина:
    ``add_user_balance`` умеет триггерить авто-продление подписки с баланса, и если
    деньги лягут первыми, дни могут быть добавлены к уже продлённой подписке —
    пользователь получит не то, что настроил админ (тот же класс, что и в промокодах).

    Ошибка на одном получателе не должна съедать награды остальных: цепочка
    обрабатывается по звеньям, сбой звена логируется и не прерывает обход.
    """
    from app.database.crud.referral import get_user_campaign_id

    components = await build_reward_components(db, referee, event=event, topup_amount_kopeks=topup_amount_kopeks)
    if not components:
        return []

    campaign_id = await get_user_campaign_id(db, referee.id)
    outcomes: list[GrantOutcome] = []
    referee_id = referee.id

    for component in components:
        try:
            outcome = await _grant_one(
                db, component, referee_id=referee_id, event=event, campaign_id=campaign_id, bot=bot
            )
        except Exception as error:
            # Изоляция по звеньям: сбой на одном получателе не должен съедать
            # награды остальной цепочки — они друг от друга не зависят.
            #
            # exc_info обязателен. Сюда попадает и ошибка в коде (NameError после
            # рефакторинга поймали ровно так), и без трейсбека она выглядит как
            # обычный сбой звена: награды тихо не начисляются, а в логе одна строка.
            logger.error(
                'Ошибка выдачи награды за реферала, остальная цепочка продолжается',
                recipient_id=component.recipient_id,
                level=component.level,
                error=str(error),
                exc_info=True,
            )
            continue

        if outcome is not None and outcome.granted_anything:
            outcomes.append(outcome)

    return outcomes


async def _grant_one(
    db: AsyncSession,
    component: RewardComponent,
    *,
    referee_id: int,
    event: str,
    campaign_id: int | None,
    bot=None,
) -> GrantOutcome | None:
    """Выдать один компонент награды одному получателю.

    Получатель перечитывается из базы на каждом компоненте намеренно. Неудачный
    ``add_user_balance`` внутри делает ``db.rollback()``, а откат истекает ВСЕ
    объекты сессии — включая те, что вызывающий держал в руках. Обращение к
    полю такого объекта в async-сессии даёт MissingGreenlet вместо понятной ошибки.
    """
    from app.database.crud.referral import create_referral_earning
    from app.database.crud.user import add_user_balance
    from app.database.models import TransactionType

    recipient = await get_user_by_id(db, component.recipient_id)
    if recipient is None:
        logger.error('Получатель награды не найден', recipient_id=component.recipient_id, level=component.level)
        return None

    referrer_id = component.referrer_id
    outcome = GrantOutcome(component=component)

    if component.days > 0:
        try:
            grant = await grant_reward_days(db, recipient, component.days, component.tariff_id)
        except Exception as error:
            logger.error(
                'Ошибка выдачи дней за реферала',
                recipient_id=recipient.id,
                level=component.level,
                error=str(error),
            )
            grant = DaysGrant(failure='error')

        if grant.days > 0:
            outcome.days_credited = grant.days
            outcome.subscription_id = grant.subscription_id
            outcome.tariff_name = grant.tariff_name
            await create_referral_earning(
                db=db,
                # Получатель — владелец строки. Для награды приглашённому пара
                # зеркалится, чтобы дни не приписались пригласившему.
                user_id=referrer_id if component.is_referrer else referee_id,
                referral_id=referee_id if component.is_referrer else referrer_id,
                amount_kopeks=0,
                reason=REASON_DAYS_REFERRER if component.is_referrer else REASON_DAYS_REFEREE,
                campaign_id=campaign_id,
                reward_type=ReferralRewardType.DAYS.value,
                level=component.level,
                days_granted=grant.days,
                tariff_id=component.tariff_id,
            )
        else:
            outcome.failure = grant.failure
            logger.warning(
                'Дни за реферала не выданы: подходящей подписки нет',
                recipient_id=recipient.id,
                level=component.level,
                tariff_id=component.tariff_id,
                failure=grant.failure,
            )

    if component.money_kopeks > 0:
        description = (
            f'Реферальная награда, уровень {component.level}'
            if component.is_referrer
            else 'Бонус по реферальной программе'
        )
        credited = await add_user_balance(
            db,
            recipient,
            component.money_kopeks,
            description,
            transaction_type=TransactionType.REFERRAL_REWARD,
            bot=bot,
        )
        if credited:
            outcome.money_credited = component.money_kopeks
            # Порядок «сначала деньги, потом строка ledger'а» выбран осознанно.
            # Атомарности между ними нет: add_user_balance коммитит сам. Если
            # запись строки упадёт, у пользователя останутся деньги без записи —
            # заработок будет НЕДОоценён. Обратный порядок при том же сбое дал бы
            # строку без денег, то есть завысил бы сумму, доступную к выводу.
            # Из двух расхождений безопасно только первое.
            #
            # Строка пишется ТОЛЬКО за пригласившего: приглашённому деньги идут
            # транзакцией на баланс, и так было всегда — запись их в
            # referral_earnings раздула бы его «реферальный доход» и, что хуже,
            # сумму, доступную к выводу.
            if component.is_referrer:
                await create_referral_earning(
                    db=db,
                    user_id=referrer_id,
                    referral_id=referee_id,
                    amount_kopeks=component.money_kopeks,
                    reason=_money_reason(event),
                    campaign_id=campaign_id,
                    reward_type=ReferralRewardType.MONEY.value,
                    level=component.level,
                )
        else:
            outcome.failure = outcome.failure or 'balance_failed'
            logger.error(
                'Не удалось начислить реферальные деньги на баланс',
                recipient_id=recipient.id,
                level=component.level,
                amount_kopeks=component.money_kopeks,
            )

    return outcome


# Ключи локализации для сгенерированных описаний. Строки собираются здесь, а
# попадают в полностью локализованные экраны — приветствие, «Как работают
# награды», копируемый текст приглашения. Захардкоженная русская вставка внутри
# английского экрана — не косметика: приглашение пользователь отправляет другу.
_TRIGGER_KEYS = {
    ReferralRewardTrigger.REGISTRATION.value: ('REFERRAL_TRIGGER_REGISTRATION', 'за регистрацию'),
    ReferralRewardTrigger.FIRST_TOPUP.value: ('REFERRAL_TRIGGER_FIRST_TOPUP', 'за первое пополнение'),
    ReferralRewardTrigger.EVERY_TOPUP.value: ('REFERRAL_TRIGGER_EVERY_TOPUP', 'с каждого пополнения'),
}


def _trigger_label(trigger: str, texts) -> str:
    key, default = _TRIGGER_KEYS.get(trigger, ('', trigger))
    return texts.t(key, default) if key else trigger


def _days_can_be_granted(config: LevelConfig, *, tariff_id: int | None, for_referee: bool) -> bool:
    """Могут ли дни этого правила вообще куда-то лечь.

    Условие касается ТОЛЬКО приглашённого и только повода «регистрация»: он создан
    секунду назад, подписки у него нет ни одной, и без указанного тарифа продлевать
    нечего, а создать подписку не из чего. Такая награда не начисляется НИКОГДА, а
    не иногда, — обещать её в приветствии значит обмануть каждого нового
    пользователя.

    Пригласившего это не касается: он в системе давно, подписка у него, как
    правило, есть, и его дни лягут в основную. Глушить его описание по тому же
    признаку значило бы наоборот — умолчать о награде, которая реально приходит.

    С указанным тарифом подписка будет создана в любом случае.
    """
    if not for_referee:
        return True
    if config.trigger != ReferralRewardTrigger.REGISTRATION.value:
        return True
    return tariff_id is not None


async def describe_active_levels(
    db: AsyncSession, *, tariff_names: dict[int, str] | None = None, language: str | None = None
) -> list[str]:
    """Человекочитаемое описание активных уровней.

    Один источник и для приветственного текста, и для экрана «Партнёрская
    программа», и для админского превью. Расхождение обещанного и начисляемого —
    самый дорогой класс ошибок в реферальных программах, а он ровно из того и
    берётся, что описание пишут отдельно от расчёта.
    """
    from app.localization.texts import get_texts

    texts = get_texts(language) if language else get_texts()
    configs = await ReferralRewardLevelService.get_all(db)
    names = tariff_names or {}
    lines: list[str] = []

    # Глубже REFERRAL_MAX_LEVEL_DEPTH цепочка не обходится вовсе, поэтому такие
    # уровни не платят — сколько бы их ни было заведено. Описывать их значит
    # обещать пользователю награду, которая не придёт никогда.
    max_depth = settings.get_referral_max_level_depth()

    for level in sorted(configs):
        config = configs[level]
        if not config.is_active or level > max_depth:
            continue

        rewards: list[str] = []
        if config.money_enabled:
            if config.referrer_percent:
                rewards.append(
                    texts.t('REFERRAL_REWARD_PERCENT_OF_SUM', '{percent}% от суммы').format(
                        percent=config.referrer_percent
                    )
                )
            if config.referrer_fixed_kopeks:
                rewards.append(settings.format_price(config.referrer_fixed_kopeks))
        if (
            config.days_enabled
            and config.referrer_days
            and _days_can_be_granted(config, tariff_id=config.referrer_tariff_id, for_referee=False)
        ):
            tariff_suffix = ''
            if config.referrer_tariff_id and config.referrer_tariff_id in names:
                tariff_suffix = f' ({names[config.referrer_tariff_id]})'
            rewards.append(
                texts.t('REFERRAL_REWARD_DAYS', '{days} дн. подписки').format(days=config.referrer_days) + tariff_suffix
            )

        if not rewards:
            continue

        lines.append(
            texts.t('REFERRAL_LEVEL_LINE', 'Уровень {level}: {rewards} {trigger}').format(
                level=level, rewards=' + '.join(rewards), trigger=_trigger_label(config.trigger, texts)
            )
        )

    return lines


async def describe_referee_bonus(
    db: AsyncSession, *, tariff_names: dict[int, str] | None = None, language: str | None = None
) -> str | None:
    """Что получит сам приглашённый. ``None`` — ничего не настроено.

    Берётся с первого сработавшего уровня — ровно так же, как это делает расчёт:
    приглашённому платят один раз за событие, а не по разу на каждом уровне.
    """
    from app.localization.texts import get_texts

    texts = get_texts(language) if language else get_texts()
    configs = await ReferralRewardLevelService.get_all(db)
    names = tariff_names or {}
    max_depth = settings.get_referral_max_level_depth()

    for level in sorted(configs):
        config = configs[level]
        if not config.is_active or level > max_depth:
            continue

        parts: list[str] = []
        if config.money_enabled and config.referee_fixed_kopeks:
            parts.append(settings.format_price(config.referee_fixed_kopeks))
        if (
            config.days_enabled
            and config.referee_days
            and _days_can_be_granted(config, tariff_id=config.referee_tariff_id, for_referee=True)
        ):
            tariff_suffix = ''
            if config.referee_tariff_id and config.referee_tariff_id in names:
                tariff_suffix = f' ({names[config.referee_tariff_id]})'
            parts.append(
                texts.t('REFERRAL_REWARD_DAYS', '{days} дн. подписки').format(days=config.referee_days) + tariff_suffix
            )

        if parts:
            return f'{" + ".join(parts)} {_trigger_label(config.trigger, texts)}'

    return None


def format_reward_total(money_kopeks: int, days: int, language: str | None = None) -> str:
    """Выплаченное одной строкой: деньги, дни или и то и другое.

    Дни называются отдельно, а не через денежную сумму: на ней считается
    доступный к выводу баланс, и подмешать в неё дни нельзя. Но и показать
    «выплачено 0 ₽» на программе, которая платит днями, значит соврать.

    Нулевые деньги при ненулевых днях не печатаются: «0 ₽ + 14 дн.» — это шум,
    сообщающий об отсутствии того, чего в этой программе и не предполагалось.
    """
    money = int(money_kopeks or 0)
    days = int(days or 0)

    from app.localization.texts import get_texts

    texts = get_texts(language) if language else get_texts()
    days_label = texts.t('REFERRAL_DAYS_SHORT', '{days} дн.').format(days=days)

    if days and not money:
        return days_label
    if days:
        return f'{settings.format_price(money)} + {days_label}'
    return settings.format_price(money)


def legacy_percent_for_import() -> tuple[int, list[str]]:
    """Процент для переносимого уровня и то, о чём нужно предупредить.

    Общий на два интерфейса: перенос из бота и из кабинета обязан давать один и
    тот же уровень, иначе результат зависит от того, откуда нажали.

    Классический процент задают ТРИ ключа, а не один. Копировать только
    ``REFERRAL_COMMISSION_PERCENT`` значит перенести не ту ставку:

    * ``REFERRAL_FIRST_PAYMENT_COMMISSION_PERCENT`` перебивает её на первом
      платеже — а перенос делается с поводом «первое пополнение», так что именно
      этот ключ и есть верный источник, когда он задан;
    * ``REFERRAL_RECURRING_COMMISSION_TIERS`` — лестница ставок по числу платящих
      рефералов. Одним уровнем она невыразима вовсе, поэтому о ней сообщается
      прямо: молча потерять ступени хуже, чем не перенести их с предупреждением.
    """
    notes: list[str] = []

    percent = settings.REFERRAL_COMMISSION_PERCENT
    first_payment = settings.REFERRAL_FIRST_PAYMENT_COMMISSION_PERCENT
    if first_payment is not None:
        percent = first_payment
        notes.append(
            f'Взят процент первого платежа ({first_payment}%), а не общий '
            f'({settings.REFERRAL_COMMISSION_PERCENT}%) — повод уровня «первое пополнение».'
        )

    if (settings.REFERRAL_RECURRING_COMMISSION_TIERS or '').strip():
        notes.append(
            'Ступени комиссии (REFERRAL_RECURRING_COMMISSION_TIERS) НЕ перенесены: '
            'у уровня одна ставка, лестницы по числу рефералов в нём нет.'
        )

    return max(0, min(100, int(percent or 0))), notes
