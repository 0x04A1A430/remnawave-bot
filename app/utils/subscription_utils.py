import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote, urlparse
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import Subscription, User
from app.config import settings

logger = logging.getLogger(__name__)


async def ensure_single_subscription(db: AsyncSession, user_id: int) -> Optional[Subscription]:
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc())
    )
    subscriptions = result.scalars().all()
    
    if len(subscriptions) <= 1:
        return subscriptions[0] if subscriptions else None
    
    latest_subscription = subscriptions[0]
    old_subscriptions = subscriptions[1:]
    
    logger.warning(f"🚨 Обнаружено {len(subscriptions)} подписок у пользователя {user_id}. Удаляем {len(old_subscriptions)} старых.")
    
    for old_sub in old_subscriptions:
        await db.delete(old_sub)
        logger.info(f"🗑️ Удалена подписка ID {old_sub.id} от {old_sub.created_at}")
    
    await db.commit()
    await db.refresh(latest_subscription)
    
    logger.info(f"✅ Оставлена подписка ID {latest_subscription.id} от {latest_subscription.created_at}")
    return latest_subscription


async def update_or_create_subscription(
    db: AsyncSession,
    user_id: int,
    **subscription_data
) -> Subscription:
    existing_subscription = await ensure_single_subscription(db, user_id)
    
    if existing_subscription:
        for key, value in subscription_data.items():
            if hasattr(existing_subscription, key):
                setattr(existing_subscription, key, value)
        
        existing_subscription.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(existing_subscription)
        
        logger.info(f"🔄 Обновлена существующая подписка ID {existing_subscription.id}")
        return existing_subscription
    
    else:
        new_subscription = Subscription(
            user_id=user_id,
            **subscription_data
        )
        
        db.add(new_subscription)
        await db.commit()
        await db.refresh(new_subscription)
        
        logger.info(f"🆕 Создана новая подписка ID {new_subscription.id}")
        return new_subscription


async def cleanup_duplicate_subscriptions(db: AsyncSession) -> int:
    result = await db.execute(
        select(Subscription.user_id)
        .group_by(Subscription.user_id)
        .having(func.count(Subscription.id) > 1)
    )
    users_with_duplicates = result.scalars().all()
    
    total_deleted = 0
    
    for user_id in users_with_duplicates:
        subscriptions_result = await db.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.created_at.desc())
        )
        subscriptions = subscriptions_result.scalars().all()
        
        for old_subscription in subscriptions[1:]:
            await db.delete(old_subscription)
            total_deleted += 1
            logger.info(f"🗑️ Удалена дублирующаяся подписка ID {old_subscription.id} пользователя {user_id}")
    
    await db.commit()
    logger.info(f"🧹 Очищено {total_deleted} дублирующихся подписок")

    return total_deleted


def get_display_subscription_link(subscription: Optional[Subscription]) -> Optional[str]:
    if not subscription:
        return None

    base_link = getattr(subscription, "subscription_url", None)

    if settings.is_happ_cryptolink_mode():
        crypto_link = getattr(subscription, "subscription_crypto_link", None)
        return crypto_link or base_link

    return base_link


def get_happ_cryptolink_redirect_link(subscription_link: Optional[str]) -> Optional[str]:
    if not subscription_link:
        return None

    template = settings.get_happ_cryptolink_redirect_template()
    if not template:
        return None

    encoded_link = quote(subscription_link, safe="")
    replacements = {
        "{subscription_link}": encoded_link,
        "{link}": encoded_link,
        "{subscription_link_raw}": subscription_link,
        "{link_raw}": subscription_link,
    }

    replaced = False
    for placeholder, value in replacements.items():
        if placeholder in template:
            template = template.replace(placeholder, value)
            replaced = True

    if replaced:
        return template

    if template.endswith(("=", "?", "&")):
        return f"{template}{encoded_link}"

    return f"{template}{encoded_link}"


def build_happ_scheme_link(subscription_link: Optional[str]) -> Optional[str]:
    """Convert a crypto link into the happ:// URL scheme if possible."""

    if not subscription_link:
        return None

    if subscription_link.startswith("happ://"):
        return subscription_link

    parsed = urlparse(subscription_link)

    if parsed.scheme not in {"http", "https"}:
        return None

    path_parts = []

    if parsed.netloc:
        path_parts.append(parsed.netloc)

    if parsed.path:
        path_parts.append(parsed.path.lstrip("/"))

    path = "/".join(part for part in path_parts if part)

    if not path and not parsed.query and not parsed.fragment:
        return None

    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""

    return f"happ://{path}{query}{fragment}"
