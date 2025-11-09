from typing import List, Optional

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ServerCategory


async def get_all_server_categories(db: AsyncSession, include_inactive: bool = False) -> List[ServerCategory]:
    query = select(ServerCategory).order_by(ServerCategory.sort_order, ServerCategory.name)
    if not include_inactive:
        query = query.where(ServerCategory.is_active.is_(True))
    result = await db.execute(query)
    return result.scalars().all()


async def get_server_category_by_id(db: AsyncSession, category_id: int) -> Optional[ServerCategory]:
    result = await db.execute(
        select(ServerCategory).where(ServerCategory.id == category_id)
    )
    return result.scalar_one_or_none()


async def create_server_category(
    db: AsyncSession,
    name: str,
    sort_order: int = 0,
    is_active: bool = True,
) -> ServerCategory:
    category = ServerCategory(name=name, sort_order=sort_order, is_active=is_active)
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


async def update_server_category(
    db: AsyncSession,
    category_id: int,
    **updates,
) -> Optional[ServerCategory]:
    valid_fields = {"name", "sort_order", "is_active"}
    filtered_updates = {k: v for k, v in updates.items() if k in valid_fields}
    if not filtered_updates:
        return await get_server_category_by_id(db, category_id)

    await db.execute(
        update(ServerCategory).where(ServerCategory.id == category_id).values(**filtered_updates)
    )
    await db.commit()
    return await get_server_category_by_id(db, category_id)


async def delete_server_category(db: AsyncSession, category_id: int) -> bool:
    await db.execute(delete(ServerCategory).where(ServerCategory.id == category_id))
    await db.commit()
    return True
