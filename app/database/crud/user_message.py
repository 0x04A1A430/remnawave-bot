import logging
import random
from datetime import datetime
from typing import Optional, List
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import UserMessage

logger = logging.getLogger(__name__)


async def create_user_message(
    db: AsyncSession,
    message_text: str,
    created_by: int,
    is_active: bool = True,
    sort_order: int = 0
) -> UserMessage:
    message = UserMessage(
        message_text=message_text,
        is_active=is_active,
        sort_order=sort_order,
        created_by=created_by
    )
    
    db.add(message)
    await db.commit()
    await db.refresh(message)
    
    logger.info(f"✅ Создано сообщение ID {message.id} пользователем {created_by}")
    return message


async def get_user_message_by_id(db: AsyncSession, message_id: int) -> Optional[UserMessage]:
    result = await db.execute(
        select(UserMessage).where(UserMessage.id == message_id)
    )
    return result.scalar_one_or_none()


async def get_active_user_messages(db: AsyncSession) -> List[UserMessage]:
    result = await db.execute(
        select(UserMessage)
        .where(UserMessage.is_active == True)
        .order_by(UserMessage.sort_order.asc(), UserMessage.created_at.desc())
    )
    return result.scalars().all()


async def get_random_active_message(db: AsyncSession) -> Optional[str]:
    active_messages = await get_active_user_messages(db)
    
    if not active_messages:
        return None
    
    random_message = random.choice(active_messages)
    return random_message.message_text


async def get_all_user_messages(
    db: AsyncSession,
    offset: int = 0,
    limit: int = 50
) -> List[UserMessage]:
    result = await db.execute(
        select(UserMessage)
        .order_by(UserMessage.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


async def get_user_messages_count(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(UserMessage.id)))
    return result.scalar()


async def update_user_message(
    db: AsyncSession,
    message_id: int,
    message_text: Optional[str] = None,
    is_active: Optional[bool] = None,
    sort_order: Optional[int] = None
) -> Optional[UserMessage]:
    message = await get_user_message_by_id(db, message_id)
    
    if not message:
        return None
    
    if message_text is not None:
        message.message_text = message_text
    
    if is_active is not None:
        message.is_active = is_active
    
    if sort_order is not None:
        message.sort_order = sort_order
    
    message.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(message)
    
    logger.info(f"📝 Обновлено сообщение ID {message_id}")
    return message


async def toggle_user_message_status(
    db: AsyncSession,
    message_id: int
) -> Optional[UserMessage]:
    message = await get_user_message_by_id(db, message_id)
    
    if not message:
        return None
    
    message.is_active = not message.is_active
    message.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(message)
    
    status_text = "активировано" if message.is_active else "деактивировано"
    logger.info(f"🔄 Сообщение ID {message_id} {status_text}")
    
    return message


async def delete_user_message(db: AsyncSession, message_id: int) -> bool:
    message = await get_user_message_by_id(db, message_id)
    
    if not message:
        return False
    
    await db.delete(message)
    await db.commit()
    
    logger.info(f"🗑️ Удалено сообщение ID {message_id}")
    return True


async def get_user_messages_stats(db: AsyncSession) -> dict:
    total_result = await db.execute(select(func.count(UserMessage.id)))
    total_messages = total_result.scalar()
    
    active_result = await db.execute(
        select(func.count(UserMessage.id)).where(UserMessage.is_active == True)
    )
    active_messages = active_result.scalar()
    
    return {
        "total_messages": total_messages,
        "active_messages": active_messages,
        "inactive_messages": total_messages - active_messages
    }
