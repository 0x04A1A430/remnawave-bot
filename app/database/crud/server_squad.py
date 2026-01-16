import logging
import random
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import (
    select,
    and_,
    func,
    update,
    delete,
    text,
    or_,
    cast,
    String,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    PromoGroup,
    ServerSquad,
    SubscriptionServer,
    Subscription,
    SubscriptionStatus,
    User,
)

logger = logging.getLogger(__name__)


async def _get_default_promo_group_id(db: AsyncSession) -> Optional[int]:
    result = await db.execute(
        select(PromoGroup.id).where(PromoGroup.is_default.is_(True)).limit(1)
    )
    return result.scalar_one_or_none()


async def create_server_squad(
    db: AsyncSession,
    squad_uuid: str,
    display_name: str,
    original_name: str = None,
    country_code: str = None,
    price_kopeks: int = 0,
    description: str = None,
    max_users: int = None,
    is_available: bool = True,
    is_trial_eligible: bool = False,
    sort_order: int = 0,
    promo_group_ids: Optional[Iterable[int]] = None,
) -> ServerSquad:

    normalized_group_ids: Sequence[int]
    if promo_group_ids is None:
        default_id = await _get_default_promo_group_id(db)
        normalized_group_ids = [default_id] if default_id is not None else []
    else:
        normalized_group_ids = [int(pg_id) for pg_id in set(promo_group_ids)]

    if not normalized_group_ids:
        raise ValueError("Server squad must be linked to at least one promo group")

    promo_groups_result = await db.execute(
        select(PromoGroup).where(PromoGroup.id.in_(normalized_group_ids))
    )
    promo_groups = promo_groups_result.scalars().all()

    if len(promo_groups) != len(normalized_group_ids):
        logger.warning(
            "Не все промогруппы найдены при создании сервера %s", display_name
        )

    server_squad = ServerSquad(
        squad_uuid=squad_uuid,
        display_name=display_name,
        original_name=original_name,
        country_code=country_code,
        price_kopeks=price_kopeks,
        description=description,
        max_users=max_users,
        is_available=is_available,
        is_trial_eligible=is_trial_eligible,
        sort_order=sort_order,
        allowed_promo_groups=promo_groups,
    )

    db.add(server_squad)
    await db.commit()
    await db.refresh(server_squad)
    
    logger.info(f"✅ Создан сервер {display_name} (UUID: {squad_uuid})")
    return server_squad


async def get_server_squad_by_uuid(
    db: AsyncSession, 
    squad_uuid: str
) -> Optional[ServerSquad]:
    
    result = await db.execute(
        select(ServerSquad)
        .options(selectinload(ServerSquad.allowed_promo_groups))
        .where(ServerSquad.squad_uuid == squad_uuid)
    )
    return result.scalars().unique().one_or_none()


async def get_server_squad_by_id(
    db: AsyncSession, 
    server_id: int
) -> Optional[ServerSquad]:
    
    result = await db.execute(
        select(ServerSquad)
        .options(selectinload(ServerSquad.allowed_promo_groups))
        .where(ServerSquad.id == server_id)
    )
    return result.scalars().unique().one_or_none()


async def get_all_server_squads(
    db: AsyncSession,
    available_only: bool = False,
    page: int = 1,
    limit: int = 50
) -> Tuple[List[ServerSquad], int]:
    
    query = select(ServerSquad)
    
    if available_only:
        query = query.where(ServerSquad.is_available == True)
    
    count_query = select(func.count(ServerSquad.id))
    if available_only:
        count_query = count_query.where(ServerSquad.is_available == True)
    
    count_result = await db.execute(count_query)
    total_count = count_result.scalar()
    
    offset = (page - 1) * limit
    query = query.order_by(ServerSquad.sort_order, ServerSquad.display_name)
    query = query.offset(offset).limit(limit)
    
    result = await db.execute(query)
    servers = result.scalars().all()
    
    return servers, total_count


async def get_available_server_squads(
    db: AsyncSession,
    promo_group_id: Optional[int] = None,
    exclude_trial_only: bool = False,
) -> List[ServerSquad]:

    query = (
        select(ServerSquad)
        .options(selectinload(ServerSquad.allowed_promo_groups))
        .where(ServerSquad.is_available.is_(True))
        .order_by(ServerSquad.sort_order, ServerSquad.display_name)
    )

    if exclude_trial_only:
        query = query.where(ServerSquad.is_trial_eligible.is_(False))

    if promo_group_id is not None:
        query = query.join(ServerSquad.allowed_promo_groups).where(
            PromoGroup.id == promo_group_id
        )

    result = await db.execute(query)
    return result.scalars().unique().all()


async def get_active_server_squads(db: AsyncSession) -> List[ServerSquad]:
    """Возвращает список активных серверов, доступных для подключения."""

    squads = await get_available_server_squads(db)

    if not squads:
        return []

    eligible: List[ServerSquad] = []

    for squad in squads:
        max_users = squad.max_users
        current_users = squad.current_users or 0

        if max_users is not None and current_users >= max_users:
            continue

        eligible.append(squad)

    if eligible:
        return eligible

    return squads


async def choose_random_active_server_squad(
    db: AsyncSession,
) -> Optional[ServerSquad]:
    """Возвращает случайный активный сервер."""

    squads = await get_active_server_squads(db)

    if not squads:
        return None

    return random.choice(squads)


async def get_random_active_squad_uuid(
    db: AsyncSession,
    fallback_uuid: Optional[str] = None,
) -> Optional[str]:
    """Возвращает UUID случайного активного сервера или запасной UUID."""

    squad = await choose_random_active_server_squad(db)

    if squad:
        return squad.squad_uuid

    return fallback_uuid


async def update_server_squad_promo_groups(
    db: AsyncSession, server_id: int, promo_group_ids: Iterable[int]
) -> Optional[ServerSquad]:
    unique_ids = [int(pg_id) for pg_id in set(promo_group_ids)]

    if not unique_ids:
        raise ValueError("Нужно выбрать хотя бы одну промогруппу")

    server = await get_server_squad_by_id(db, server_id)
    if not server:
        return None

    result = await db.execute(
        select(PromoGroup).where(PromoGroup.id.in_(unique_ids))
    )
    promo_groups = result.scalars().all()

    if not promo_groups:
        raise ValueError("Не найдены промогруппы для обновления сервера")

    server.allowed_promo_groups = promo_groups
    await db.commit()
    await db.refresh(server)

    logger.info(
        "Обновлены промогруппы сервера %s (ID: %s): %s",
        server.display_name,
        server.id,
        ", ".join(pg.name for pg in promo_groups),
    )

    return server


async def update_server_squad(
    db: AsyncSession,
    server_id: int,
    **updates
) -> Optional[ServerSquad]:
    
    valid_fields = {
        "display_name",
        "original_name",
        "country_code",
        "price_kopeks",
        "description",
        "max_users",
        "is_available",
        "sort_order",
        "is_trial_eligible",
    }
    
    filtered_updates = {k: v for k, v in updates.items() if k in valid_fields}
    
    if not filtered_updates:
        return None
    
    await db.execute(
        update(ServerSquad)
        .where(ServerSquad.id == server_id)
        .values(**filtered_updates)
    )
    
    await db.commit()
    
    return await get_server_squad_by_id(db, server_id)


async def delete_server_squad(db: AsyncSession, server_id: int) -> bool:
    
    connections_result = await db.execute(
        select(func.count(SubscriptionServer.id))
        .where(SubscriptionServer.server_squad_id == server_id)
    )
    connections_count = connections_result.scalar()
    
    if connections_count > 0:
        logger.warning(f"⚠ Нельзя удалить сервер {server_id}: есть активные подключения ({connections_count})")
        return False
    
    await db.execute(
        delete(ServerSquad).where(ServerSquad.id == server_id)
    )
    await db.commit()
    
    logger.info(f"🗑️ Удален сервер (ID: {server_id})")
    return True


async def sync_with_remnawave(
    db: AsyncSession,
    remnawave_squads: List[dict]
) -> Tuple[int, int, int]:

    created = 0
    updated = 0
    removed = 0
    
    existing_servers = {}
    result = await db.execute(select(ServerSquad))
    for server in result.scalars().all():
        existing_servers[server.squad_uuid] = server
    
    remnawave_uuids = {squad['uuid'] for squad in remnawave_squads}
    
    for squad in remnawave_squads:
        squad_uuid = squad['uuid']
        original_name = squad.get('name', f'Squad {squad_uuid[:8]}')
        
        if squad_uuid in existing_servers:
            server = existing_servers[squad_uuid]
            if server.original_name != original_name:
                server.original_name = original_name
                updated += 1
        else:
            await create_server_squad(
                db=db,
                squad_uuid=squad_uuid,
                display_name=_generate_display_name(original_name),
                original_name=original_name,
                country_code=_extract_country_code(original_name),
                price_kopeks=1000, 
                is_available=False 
            )
            created += 1
    
    removed_servers = [
        server for uuid, server in existing_servers.items()
        if uuid not in remnawave_uuids
    ]

    if removed_servers:
        removed_ids = [server.id for server in removed_servers]
        removed_uuids = {server.squad_uuid for server in removed_servers}

        subscription_ids_result = await db.execute(
            select(SubscriptionServer.subscription_id)
            .where(SubscriptionServer.server_squad_id.in_(removed_ids))
        )
        subscription_ids = {row[0] for row in subscription_ids_result.fetchall()}

        for server in removed_servers:
            logger.info(
                "🗑️ Удаляется сервер %s (UUID: %s)",
                server.display_name,
                server.squad_uuid,
            )

        await db.execute(
            delete(SubscriptionServer).where(SubscriptionServer.server_squad_id.in_(removed_ids))
        )

        subscriptions_to_update: dict[int, Subscription] = {}

        if subscription_ids:
            subscriptions_result = await db.execute(
                select(Subscription).where(Subscription.id.in_(subscription_ids))
            )
            for subscription in subscriptions_result.scalars().unique().all():
                subscriptions_to_update[subscription.id] = subscription

        for squad_uuid in removed_uuids:
            if not squad_uuid:
                continue

            extra_result = await db.execute(
                select(Subscription).where(
                    text("connected_squads::text LIKE :uuid_pattern")
                ),
                {"uuid_pattern": f'%"{squad_uuid}"%'}
            )

            for subscription in extra_result.scalars().unique().all():
                subscriptions_to_update[subscription.id] = subscription

        cleaned_subscriptions = 0

        for subscription in subscriptions_to_update.values():
            current_squads = list(subscription.connected_squads or [])
            if not current_squads:
                continue

            filtered_squads = [
                squad_uuid for squad_uuid in current_squads if squad_uuid not in removed_uuids
            ]

            if len(filtered_squads) != len(current_squads):
                subscription.connected_squads = filtered_squads
                subscription.updated_at = datetime.utcnow()
                cleaned_subscriptions += 1

        await db.execute(delete(ServerSquad).where(ServerSquad.id.in_(removed_ids)))
        removed = len(removed_servers)

        if cleaned_subscriptions:
            logger.info(
                "🧹 Обновлены подписки после удаления серверов: %s",
                cleaned_subscriptions,
            )

    await db.commit()

    logger.info(f"🔄 Синхронизация завершена: +{created} ~{updated} -{removed}")
    return created, updated, removed


async def get_server_connected_users(
    db: AsyncSession,
    server_id: int
) -> List[User]:

    server_uuid_result = await db.execute(
        select(ServerSquad.squad_uuid).where(ServerSquad.id == server_id)
    )
    server_uuid = server_uuid_result.scalar_one_or_none()

    connection_filters = [SubscriptionServer.id.isnot(None)]

    if server_uuid:
        connection_filters.append(
            cast(Subscription.connected_squads, String).like(
                f'%"{server_uuid}"%'
            )
        )

    result = await db.execute(
        select(User)
        .join(Subscription, Subscription.user_id == User.id)
        .outerjoin(
            SubscriptionServer,
            and_(
                SubscriptionServer.subscription_id == Subscription.id,
                SubscriptionServer.server_squad_id == server_id,
            ),
        )
        .where(or_(*connection_filters))
        .options(selectinload(User.subscription))
        .order_by(User.id)
    )

    return result.scalars().unique().all()


async def get_trial_eligible_server_squads(
    db: AsyncSession,
    include_unavailable: bool = False,
) -> List[ServerSquad]:

    query = select(ServerSquad).where(ServerSquad.is_trial_eligible.is_(True))

    result = await db.execute(query)
    squads = result.scalars().unique().all()

    if include_unavailable:
        return squads

    preferred_squads: List[ServerSquad] = []
    fallback_squads: List[ServerSquad] = []

    for squad in squads:
        current_users = squad.current_users or 0
        is_full = squad.max_users is not None and current_users >= squad.max_users

        if is_full:
            continue

        if squad.is_available:
            preferred_squads.append(squad)
        else:
            fallback_squads.append(squad)

    if preferred_squads:
        return preferred_squads

    if fallback_squads:
        return fallback_squads

    return squads


async def choose_random_trial_server_squad(
    db: AsyncSession,
) -> Optional[ServerSquad]:

    squads = await get_trial_eligible_server_squads(db)

    if not squads:
        return None

    return random.choice(squads)


async def get_random_trial_squad_uuid(
    db: AsyncSession,
) -> Optional[str]:

    squad = await choose_random_trial_server_squad(db)

    if squad:
        return squad.squad_uuid

    return None


def _generate_display_name(original_name: str) -> str:
    """Генерирует отображаемое название сервера на основе оригинального имени."""

    country_names = {
        # Европа
        'NL': '🇳🇱 Нидерланды',
        'DE': '🇩🇪 Германия',
        'FR': '🇫🇷 Франция',
        'GB': '🇬🇧 Великобритания',
        'UK': '🇬🇧 Великобритания',
        'IT': '🇮🇹 Италия',
        'ES': '🇪🇸 Испания',
        'PT': '🇵🇹 Португалия',
        'PL': '🇵🇱 Польша',
        'CZ': '🇨🇿 Чехия',
        'AT': '🇦🇹 Австрия',
        'CH': '🇨🇭 Швейцария',
        'SE': '🇸🇪 Швеция',
        'NO': '🇳🇴 Норвегия',
        'FI': '🇫🇮 Финляндия',
        'DK': '🇩🇰 Дания',
        'BE': '🇧🇪 Бельгия',
        'IE': '🇮🇪 Ирландия',
        'RO': '🇷🇴 Румыния',
        'BG': '🇧🇬 Болгария',
        'HU': '🇭🇺 Венгрия',
        'GR': '🇬🇷 Греция',
        'LV': '🇱🇻 Латвия',
        'LT': '🇱🇹 Литва',
        'EE': '🇪🇪 Эстония',
        'SK': '🇸🇰 Словакия',
        'SI': '🇸🇮 Словения',
        'HR': '🇭🇷 Хорватия',
        'RS': '🇷🇸 Сербия',
        'UA': '🇺🇦 Украина',
        'MD': '🇲🇩 Молдова',
        'BY': '🇧🇾 Беларусь',
        'LU': '🇱🇺 Люксембург',

        # СНГ и Азия
        'RU': '🇷🇺 Россия',
        'KZ': '🇰🇿 Казахстан',
        'UZ': '🇺🇿 Узбекистан',
        'GE': '🇬🇪 Грузия',
        'AM': '🇦🇲 Армения',
        'AZ': '🇦🇿 Азербайджан',

        # Америка
        'US': '🇺🇸 США',
        'CA': '🇨🇦 Канада',
        'MX': '🇲🇽 Мексика',
        'BR': '🇧🇷 Бразилия',
        'AR': '🇦🇷 Аргентина',
        'CL': '🇨🇱 Чили',
        'CO': '🇨🇴 Колумбия',

        # Азия
        'JP': '🇯🇵 Япония',
        'KR': '🇰🇷 Южная Корея',
        'CN': '🇨🇳 Китай',
        'HK': '🇭🇰 Гонконг',
        'TW': '🇹🇼 Тайвань',
        'SG': '🇸🇬 Сингапур',
        'TH': '🇹🇭 Таиланд',
        'VN': '🇻🇳 Вьетнам',
        'MY': '🇲🇾 Малайзия',
        'ID': '🇮🇩 Индонезия',
        'PH': '🇵🇭 Филиппины',
        'IN': '🇮🇳 Индия',
        'PK': '🇵🇰 Пакистан',

        # Ближний Восток
        'IL': '🇮🇱 Израиль',
        'TR': '🇹🇷 Турция',
        'AE': '🇦🇪 ОАЭ',
        'SA': '🇸🇦 Саудовская Аравия',
        'QA': '🇶🇦 Катар',
        'BH': '🇧🇭 Бахрейн',
        'KW': '🇰🇼 Кувейт',

        # Океания
        'AU': '🇦🇺 Австралия',
        'NZ': '🇳🇿 Новая Зеландия',

        # Африка
        'ZA': '🇿🇦 ЮАР',
        'EG': '🇪🇬 Египет',
        'NG': '🇳🇬 Нигерия',
        'KE': '🇰🇪 Кения',
    }

    name_upper = original_name.upper()

    # Сначала ищем код как отдельный элемент (через - или _)
    for code, display_name in country_names.items():
        if f'-{code}' in name_upper or f'_{code}' in name_upper:
            return display_name
        if name_upper.startswith(code + '-') or name_upper.startswith(code + '_'):
            return display_name
        if name_upper.endswith('-' + code) or name_upper.endswith('_' + code):
            return display_name
        if name_upper == code:
            return display_name

    # Потом ищем просто вхождение кода
    for code, display_name in country_names.items():
        if code in name_upper:
            return display_name

    return f"🌍 {original_name}"


def _extract_country_code(original_name: str) -> Optional[str]:
    """Извлекает код страны из оригинального названия."""

    # Полный список кодов стран
    codes = [
        # Европа
        'NL', 'DE', 'FR', 'GB', 'UK', 'IT', 'ES', 'PT', 'PL', 'CZ', 'AT', 'CH',
        'SE', 'NO', 'FI', 'DK', 'BE', 'IE', 'RO', 'BG', 'HU', 'GR', 'LV', 'LT',
        'EE', 'SK', 'SI', 'HR', 'RS', 'UA', 'MD', 'BY', 'LU',
        # СНГ
        'RU', 'KZ', 'UZ', 'GE', 'AM', 'AZ',
        # Америка
        'US', 'CA', 'MX', 'BR', 'AR', 'CL', 'CO',
        # Азия
        'JP', 'KR', 'CN', 'HK', 'TW', 'SG', 'TH', 'VN', 'MY', 'ID', 'PH', 'IN', 'PK',
        # Ближний Восток
        'IL', 'TR', 'AE', 'SA', 'QA', 'BH', 'KW',
        # Океания
        'AU', 'NZ',
        # Африка
        'ZA', 'EG', 'NG', 'KE',
    ]

    name_upper = original_name.upper()

    # Сначала ищем код как отдельный элемент
    for code in codes:
        if f'-{code}' in name_upper or f'_{code}' in name_upper:
            return code
        if name_upper.startswith(code + '-') or name_upper.startswith(code + '_'):
            return code
        if name_upper.endswith('-' + code) or name_upper.endswith('_' + code):
            return code
        if name_upper == code:
            return code

    # Потом просто ищем вхождение
    for code in codes:
        if code in name_upper:
            return code

    return None


async def get_server_statistics(db: AsyncSession) -> dict:

    total_result = await db.execute(select(func.count(ServerSquad.id)))
    total_servers = total_result.scalar()

    available_result = await db.execute(
        select(func.count(ServerSquad.id))
        .where(ServerSquad.is_available == True)
    )
    available_servers = available_result.scalar()
    
    servers_with_connections = 0
    all_servers_result = await db.execute(select(ServerSquad.squad_uuid))
    all_server_uuids = [row[0] for row in all_servers_result.fetchall()]
    
    for squad_uuid in all_server_uuids:
        count_result = await db.execute(
            text("""
                SELECT COUNT(s.id) 
                FROM subscriptions s 
                WHERE s.status IN ('active', 'trial') 
                AND s.connected_squads::text LIKE :uuid_pattern
            """),
            {"uuid_pattern": f'%"{squad_uuid}"%'}
        )
        user_count = count_result.scalar() or 0
        if user_count > 0:
            servers_with_connections += 1
    
    revenue_result = await db.execute(
        select(func.coalesce(func.sum(SubscriptionServer.paid_price_kopeks), 0))
    )
    total_revenue_kopeks = revenue_result.scalar()
    
    return {
        'total_servers': total_servers,
        'available_servers': available_servers,
        'unavailable_servers': total_servers - available_servers,
        'servers_with_connections': servers_with_connections,
        'total_revenue_kopeks': total_revenue_kopeks,
        'total_revenue_rubles': total_revenue_kopeks / 100
    }


async def count_active_users_for_squad(db: AsyncSession, squad_uuid: str) -> int:
    """Возвращает количество активных подписок, подключенных к указанному скваду."""

    result = await db.execute(
        select(func.count(Subscription.id)).where(
            Subscription.status.in_(
                [
                    SubscriptionStatus.ACTIVE.value,
                    SubscriptionStatus.TRIAL.value,
                ]
            ),
            cast(Subscription.connected_squads, String).like(f'%"{squad_uuid}"%'),
        )
    )

    return result.scalar() or 0


async def add_user_to_servers(
    db: AsyncSession,
    server_squad_ids: List[int]
) -> bool:
    
    try:
        for server_id in server_squad_ids:
            await db.execute(
                update(ServerSquad)
                .where(ServerSquad.id == server_id)
                .values(current_users=ServerSquad.current_users + 1)
            )
        
        await db.commit()
        logger.info(f"✅ Увеличен счетчик пользователей для серверов: {server_squad_ids}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка увеличения счетчика пользователей: {e}")
        await db.rollback()
        return False


async def remove_user_from_servers(
    db: AsyncSession,
    server_squad_ids: List[int]
) -> bool:
    
    try:
        for server_id in server_squad_ids:
            await db.execute(
                update(ServerSquad)
                .where(ServerSquad.id == server_id)
                .values(current_users=func.greatest(ServerSquad.current_users - 1, 0))
            )
        
        await db.commit()
        logger.info(f"✅ Уменьшен счетчик пользователей для серверов: {server_squad_ids}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка уменьшения счетчика пользователей: {e}")
        await db.rollback()
        return False


async def get_server_ids_by_uuids(
    db: AsyncSession,
    squad_uuids: List[str]
) -> List[int]:
    
    result = await db.execute(
        select(ServerSquad.id)
        .where(ServerSquad.squad_uuid.in_(squad_uuids))
    )
    return [row[0] for row in result.fetchall()]


async def ensure_servers_synced(db: AsyncSession) -> None:
    """
    Проверяет и синхронизирует серверы при запуске.
    Если серверов нет в БД, загружает их из RemnaWave.
    Вызывается при старте бота.
    """
    try:
        # Проверяем есть ли серверы в БД
        result = await db.execute(select(func.count(ServerSquad.id)))
        server_count = result.scalar() or 0

        if server_count > 0:
            logger.info(f"✅ В базе уже есть {server_count} серверов, пропускаем синхронизацию")
            return

        logger.info("🔄 Серверов в БД нет, начинаем синхронизацию с RemnaWave...")

        # Импортируем сервис здесь чтобы избежать циклических импортов
        from app.services.subscription_service import SubscriptionService

        subscription_service = SubscriptionService()
        if not subscription_service.is_configured:
            logger.warning("⚠️ RemnaWave не настроен, серверы не синхронизированы")
            return

        # Получаем скводы из RemnaWave
        squads = await subscription_service.get_remnawave_squads()
        if squads is None:
            logger.error("❌ Не удалось получить список серверов из RemnaWave")
            return

        if not squads:
            logger.warning("⚠️ RemnaWave вернул пустой список серверов")
            return

        # Синхронизируем
        created, updated, removed = await sync_with_remnawave(db, squads)
        logger.info(f"✅ Серверы синхронизированы: +{created} ~{updated} -{removed}")

    except Exception as e:
        logger.error(f"❌ Ошибка синхронизации серверов: {e}")


async def sync_server_user_counts(db: AsyncSession) -> int:
    
    try:
        all_servers_result = await db.execute(select(ServerSquad.id, ServerSquad.squad_uuid))
        all_servers = all_servers_result.fetchall()
        
        logger.info(f"🔍 Найдено серверов для синхронизации: {len(all_servers)}")
        
        updated_count = 0
        for server_id, squad_uuid in all_servers:
            count_result = await db.execute(
                text("""
                    SELECT COUNT(s.id) 
                    FROM subscriptions s 
                    WHERE s.status IN ('active', 'trial') 
                    AND s.connected_squads::text LIKE :uuid_pattern
                """),
                {"uuid_pattern": f'%"{squad_uuid}"%'}
            )
            actual_users = count_result.scalar() or 0
            
            logger.info(f"📊 Сервер {server_id} ({squad_uuid[:8]}): {actual_users} пользователей")
            
            await db.execute(
                update(ServerSquad)
                .where(ServerSquad.id == server_id)
                .values(current_users=actual_users)
            )
            updated_count += 1
        
        await db.commit()
        logger.info(f"✅ Синхронизированы счетчики для {updated_count} серверов")
        return updated_count
        
    except Exception as e:
        logger.error(f"Ошибка синхронизации счетчиков пользователей: {e}")
        await db.rollback()
        return 0
