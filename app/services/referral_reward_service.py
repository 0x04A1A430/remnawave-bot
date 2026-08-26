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

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cache = None

    @classmethod
    async def _load(cls, db: AsyncSession) -> dict[int, LevelConfig]:
        if cls._cache is not None:
            return cls._cache

        async with cls._lock:
            if cls._cache is not None:
                return cls._cache

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
                )
            cls._cache = configs
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


async def count_level_payments(db: AsyncSession, referrer_id: int, referral_id: int, level: int) -> int:
    """Сколько раз этот уровень уже платил за эту пару.

    Считаются только денежные строки: лимит ``max_payments`` унаследован от
    ``REFERRAL_MAX_COMMISSION_PAYMENTS`` и всегда означал число оплаченных
    комиссий. Дни ограничиваются собственным ``referrer_days``, а не этим счётчиком.
    """
    result = await db.execute(
        select(func.count(ReferralEarning.id)).where(
            ReferralEarning.user_id == referrer_id,
            ReferralEarning.referral_id == referral_id,
            ReferralEarning.level == level,
            ReferralEarning.reward_type == ReferralRewardType.MONEY.value,
            ReferralEarning.amount_kopeks > 0,
        )
    )
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
            paid = await count_level_payments(db, referrer.id, referee.id, level)
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


async def _resolve_days_target(db: AsyncSession, user: User, tariff_id: int | None):
    """Подписка, в которую лягут дни. ``None`` — подходящей нет.

    Тариф в правиле уровня — это и есть ответ на вопрос «куда попадут дни».
    Он важен именно в мультитарифе: там подписок несколько, а спросить пользователя
    некого — награда приходит асинхронно, на чужом пополнении. Без тарифа берём
    основную подписку, как это делает любое другое продление в классическом режиме.
    """
    from app.database.crud.subscription import get_subscription_by_user_and_tariff, get_subscription_by_user_id

    if tariff_id is not None:
        return await get_subscription_by_user_and_tariff(db, user.id, tariff_id, include_inactive=True)
    return await get_subscription_by_user_id(db, user.id)


async def _create_subscription_for_days(db: AsyncSession, user: User, days: int, tariff_id: int):
    """Завести подписку под награду, когда своей у пользователя нет.

    Вызывается ТОЛЬКО когда подписок нет вовсе, и только при явно указанном тарифе.
    Это важно: ``create_paid_subscription`` умеет конвертировать живой триал в
    платную подписку, и вызвать его у человека с триалом означало бы бесплатно
    снять с него триальный статус — то есть выключить его из авто-продления
    (класс бага #629889). Проверка «подписок нет» это исключает.
    """
    from app.database.crud.subscription import create_paid_subscription, get_all_subscriptions_by_user_id
    from app.database.crud.tariff import get_tariff_by_id

    existing = await get_all_subscriptions_by_user_id(db, user.id)
    if existing:
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

    subscription = await _resolve_days_target(db, user, tariff_id)
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

    tariff_name = None
    tariff = getattr(subscription, 'tariff', None)
    if tariff is not None:
        tariff_name = getattr(tariff, 'name', None)

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
    from app.database.crud.referral import create_referral_earning, get_user_campaign_id
    from app.database.crud.user import add_user_balance
    from app.database.models import TransactionType

    components = await build_reward_components(db, referee, event=event, topup_amount_kopeks=topup_amount_kopeks)
    if not components:
        return []

    campaign_id = await get_user_campaign_id(db, referee.id)
    outcomes: list[GrantOutcome] = []

    for component in components:
        recipient = (
            referee if component.recipient_id == referee.id else await get_user_by_id(db, component.recipient_id)
        )
        if recipient is None:
            logger.error('Получатель награды не найден', recipient_id=component.recipient_id, level=component.level)
            continue

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
                    user_id=referrer_id if component.is_referrer else referee.id,
                    referral_id=referee.id if component.is_referrer else referrer_id,
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
                # Строка ledger'а пишется ТОЛЬКО за пригласившего: приглашённому
                # деньги начисляются транзакцией на баланс, и так было всегда —
                # запись их в referral_earnings раздула бы его «реферальный доход»
                # и, что хуже, сумму, доступную к выводу.
                if component.is_referrer:
                    await create_referral_earning(
                        db=db,
                        user_id=referrer_id,
                        referral_id=referee.id,
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

        if outcome.granted_anything:
            outcomes.append(outcome)

    return outcomes


_TRIGGER_LABELS = {
    ReferralRewardTrigger.REGISTRATION.value: 'за регистрацию',
    ReferralRewardTrigger.FIRST_TOPUP.value: 'за первое пополнение',
    ReferralRewardTrigger.EVERY_TOPUP.value: 'с каждого пополнения',
}


async def describe_active_levels(db: AsyncSession, *, tariff_names: dict[int, str] | None = None) -> list[str]:
    """Человекочитаемое описание активных уровней.

    Один источник и для приветственного текста, и для экрана «Партнёрская
    программа», и для админского превью. Расхождение обещанного и начисляемого —
    самый дорогой класс ошибок в реферальных программах, а он ровно из того и
    берётся, что описание пишут отдельно от расчёта.
    """
    configs = await ReferralRewardLevelService.get_all(db)
    names = tariff_names or {}
    lines: list[str] = []

    for level in sorted(configs):
        config = configs[level]
        if not config.is_active:
            continue

        rewards: list[str] = []
        if config.money_enabled:
            if config.referrer_percent:
                rewards.append(f'{config.referrer_percent}% от суммы')
            if config.referrer_fixed_kopeks:
                rewards.append(settings.format_price(config.referrer_fixed_kopeks))
        if config.days_enabled and config.referrer_days:
            tariff_suffix = ''
            if config.referrer_tariff_id and config.referrer_tariff_id in names:
                tariff_suffix = f' ({names[config.referrer_tariff_id]})'
            rewards.append(f'{config.referrer_days} дн. подписки{tariff_suffix}')

        if not rewards:
            continue

        trigger_label = _TRIGGER_LABELS.get(config.trigger, config.trigger)
        lines.append(f'Уровень {level}: {" + ".join(rewards)} {trigger_label}')

    return lines


async def describe_referee_bonus(db: AsyncSession, *, tariff_names: dict[int, str] | None = None) -> str | None:
    """Что получит сам приглашённый. ``None`` — ничего не настроено.

    Берётся с первого сработавшего уровня — ровно так же, как это делает расчёт:
    приглашённому платят один раз за событие, а не по разу на каждом уровне.
    """
    configs = await ReferralRewardLevelService.get_all(db)
    names = tariff_names or {}

    for level in sorted(configs):
        config = configs[level]
        if not config.is_active:
            continue

        parts: list[str] = []
        if config.money_enabled and config.referee_fixed_kopeks:
            parts.append(settings.format_price(config.referee_fixed_kopeks))
        if config.days_enabled and config.referee_days:
            tariff_suffix = ''
            if config.referee_tariff_id and config.referee_tariff_id in names:
                tariff_suffix = f' ({names[config.referee_tariff_id]})'
            parts.append(f'{config.referee_days} дн. подписки{tariff_suffix}')

        if parts:
            trigger_label = _TRIGGER_LABELS.get(config.trigger, config.trigger)
            return f'{" + ".join(parts)} {trigger_label}'

    return None


def format_reward_total(money_kopeks: int, days: int) -> str:
    """Выплаченное одной строкой: деньги, дни или и то и другое.

    Дни называются отдельно, а не через денежную сумму: на ней считается
    доступный к выводу баланс, и подмешать в неё дни нельзя. Но и показать
    «выплачено 0 ₽» на программе, которая платит днями, значит соврать.

    Нулевые деньги при ненулевых днях не печатаются: «0 ₽ + 14 дн.» — это шум,
    сообщающий об отсутствии того, чего в этой программе и не предполагалось.
    """
    money = int(money_kopeks or 0)
    days = int(days or 0)

    if days and not money:
        return f'{days} дн.'
    if days:
        return f'{settings.format_price(money)} + {days} дн.'
    return settings.format_price(money)
