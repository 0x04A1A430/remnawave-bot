"""Уровни реферальных наград: чтение и правка конфигурации.

Конфигурация лежит в БД, а не в ``Settings``, намеренно. Ключ, прописанный в
``.env``, попадает в ``ENV_OVERRIDE_KEYS`` и перестаёт редактироваться из админки —
на типовой установке вся реферальная секция именно так и залочена. Отдельная
таблица этого механизма не касается: правится одинаково из бота и из кабинета и
переживает перезапуск по определению.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ReferralRewardLevel, ReferralRewardMode, ReferralRewardTrigger


logger = structlog.get_logger(__name__)


_VALID_MODES = frozenset(mode.value for mode in ReferralRewardMode)
_VALID_TRIGGERS = frozenset(trigger.value for trigger in ReferralRewardTrigger)

# Ниже — не догма, а предел здравого смысла: цепочка глубже десятка уровней
# означает обход десятка пользователей на каждом пополнении.
MAX_SUPPORTED_LEVEL = 10


def _invalidate_level_cache() -> None:
    """Сбросить кэш уровней после записи.

    Сброс делается здесь, а не в вызывающих: иначе любой новый путь правки
    (админка бота, кабинет, скрипт миграции) обязан был бы помнить про кэш, и
    первый же забывший получил бы «сохранил, а ничего не изменилось» — правка
    видна на экране, а начисления идут по старому правилу до перезапуска.

    Импорт локальный: сервис наград импортирует нормализаторы отсюда.
    """
    from app.services.referral_reward_service import ReferralRewardLevelService

    ReferralRewardLevelService.invalidate_cache()


def normalize_mode(value: str | None) -> str:
    """Режим наград с приведением к известному значению.

    Неизвестное значение молча превращать в «оба бонуса» нельзя — это выдало бы
    награду, которую админ не настраивал. Падаем в самый узкий вариант.
    """
    candidate = str(value or '').strip().lower()
    return candidate if candidate in _VALID_MODES else ReferralRewardMode.MONEY.value


def normalize_trigger(value: str | None) -> str:
    candidate = str(value or '').strip().lower()
    return candidate if candidate in _VALID_TRIGGERS else ReferralRewardTrigger.FIRST_TOPUP.value


async def get_all_reward_levels(db: AsyncSession, *, only_active: bool = False) -> list[ReferralRewardLevel]:
    query = select(ReferralRewardLevel).order_by(ReferralRewardLevel.level.asc())
    if only_active:
        query = query.where(ReferralRewardLevel.is_active.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_reward_level(db: AsyncSession, level: int) -> ReferralRewardLevel | None:
    result = await db.execute(select(ReferralRewardLevel).where(ReferralRewardLevel.level == level))
    return result.scalar_one_or_none()


async def get_reward_level_by_id(db: AsyncSession, level_id: int) -> ReferralRewardLevel | None:
    result = await db.execute(select(ReferralRewardLevel).where(ReferralRewardLevel.id == level_id))
    return result.scalar_one_or_none()


async def upsert_reward_level(db: AsyncSession, level: int, **values) -> ReferralRewardLevel:
    """Создать или обновить правило уровня.

    Upsert, а не отдельные create/update: уровень уникален по номеру, и попытка
    «создать второй первый уровень» — это на деле правка первого.
    """
    if level < 1 or level > MAX_SUPPORTED_LEVEL:
        raise ValueError(f'level must be between 1 and {MAX_SUPPORTED_LEVEL}')

    if 'reward_mode' in values:
        values['reward_mode'] = normalize_mode(values['reward_mode'])
    if 'trigger' in values:
        values['trigger'] = normalize_trigger(values['trigger'])

    for key in ('referrer_days', 'referee_days', 'max_payments'):
        if key in values and values[key] is not None:
            values[key] = max(0, int(values[key]))
    for key in ('referrer_fixed_kopeks', 'referee_fixed_kopeks'):
        if key in values and values[key] is not None:
            values[key] = max(0, int(values[key]))
    if values.get('referrer_percent') is not None:
        values['referrer_percent'] = max(0, min(100, int(values['referrer_percent'])))

    existing = await get_reward_level(db, level)
    if existing is None:
        # Новая строка заводится ВЫКЛЮЧЕННОЙ, даже когда её создаёт правка одного
        # поля. Колоночный default — True, и без этого правка тарифа или суммы у
        # только что удалённого уровня воскрешала бы его сразу активным: он начал
        # бы платить с ближайшего пополнения по одному заполненному полю.
        existing = ReferralRewardLevel(level=level, is_active=False)
        db.add(existing)

    for key, value in values.items():
        if hasattr(existing, key):
            setattr(existing, key, value)

    await db.commit()
    await db.refresh(existing)
    _invalidate_level_cache()
    logger.info('Обновлено правило реферального уровня', level=level, values=sorted(values))
    return existing


async def delete_reward_level(db: AsyncSession, level: int) -> bool:
    existing = await get_reward_level(db, level)
    if existing is None:
        return False
    await db.delete(existing)
    await db.commit()
    _invalidate_level_cache()
    logger.info('Удалено правило реферального уровня', level=level)
    return True
