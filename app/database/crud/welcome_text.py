import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WelcomeText

logger = logging.getLogger(__name__)

WELCOME_TEXT_KEY = "welcome_text"

async def get_active_welcome_text(db: AsyncSession) -> Optional[str]:
    result = await db.execute(
        select(WelcomeText)
        .where(WelcomeText.is_active == True)
        .order_by(WelcomeText.updated_at.desc())
    )
    welcome_text = result.scalar_one_or_none()
    
    if welcome_text:
        return welcome_text.text_content
    
    return None

async def set_welcome_text(db: AsyncSession, text_content: str, admin_id: int) -> bool:
    try:
        await db.execute(
            update(WelcomeText).values(is_active=False)
        )
        
        new_welcome_text = WelcomeText(
            text_content=text_content,
            is_active=True,
            created_by=admin_id
        )
        
        db.add(new_welcome_text)
        await db.commit()
        await db.refresh(new_welcome_text)
        
        logger.info(f"Установлен новый приветственный текст администратором {admin_id}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при установке приветственного текста: {e}")
        await db.rollback()
        return False

async def get_current_welcome_text_or_default() -> str:
    return (
        f"Привет, {{user_name}}! 🎁 3 дней VPN бесплатно! "
        f"Подключайтесь за минуту и забудьте о блокировках. "
        f"✅ До 1 Гбит/с скорость "
        f"✅ Умный VPN — можно не отключать для большинства российских сервисов "
        f"✅ Современные протоколы — максимум защиты и анонимности "
        f"👉 Всего 99₽/мес за 1 устройство "
        f"👇 Жмите кнопку и подключайтесь!"
    )

def replace_placeholders(text: str, user) -> str:
    replacements = {
        '{user_name}': user.first_name or user.username or "друг",
        '{first_name}': user.first_name or "друг", 
        '{username}': f"@{user.username}" if user.username else user.first_name or "друг",
        '{username_clean}': user.username or user.first_name or "друг",
        'User': user.first_name or user.username or "друг" 
    }
    
    result = text
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    
    return result

async def get_welcome_text_for_user(db: AsyncSession, user) -> str:
    welcome_text = await get_active_welcome_text(db)
    
    if not welcome_text:
        welcome_text = await get_current_welcome_text_or_default()
    
    return replace_placeholders(welcome_text, user)

def get_available_placeholders() -> dict:
    return {
        '{user_name}': 'Имя или username пользователя (приоритет: имя → username → "друг")',
        '{first_name}': 'Только имя пользователя (или "друг" если не указано)',
        '{username}': 'Username с символом @ (или имя если username не указан)',
        '{username_clean}': 'Username без символа @ (или имя если username не указан)'
    }
