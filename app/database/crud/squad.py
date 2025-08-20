import logging
from typing import Optional, List
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Squad

logger = logging.getLogger(__name__)


async def get_squad_by_uuid(db: AsyncSession, uuid: str) -> Optional[Squad]:
    result = await db.execute(
        select(Squad).where(Squad.uuid == uuid)
    )
    return result.scalar_one_or_none()


async def get_available_squads(db: AsyncSession) -> List[Squad]:
    result = await db.execute(
        select(Squad).where(Squad.is_available == True)
    )
    return result.scalars().all()


async def create_squad(
    db: AsyncSession,
    uuid: str,
    name: str,
    country_code: str = None,
    price_kopeks: int = 0,
    description: str = None
) -> Squad:
    squad = Squad(
        uuid=uuid,
        name=name,
        country_code=country_code,
        price_kopeks=price_kopeks,
        description=description
    )
    
    db.add(squad)
    await db.commit()
    await db.refresh(squad)
    
    logger.info(f"✅ Создан сквад: {name}")
    return squad


async def update_squad(
    db: AsyncSession,
    squad: Squad,
    **kwargs
) -> Squad:
    for field, value in kwargs.items():
        if hasattr(squad, field):
            setattr(squad, field, value)
    
    await db.commit()
    await db.refresh(squad)
    
    return squad