from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SystemSetting


async def upsert_system_setting(
    db: AsyncSession,
    key: str,
    value: Optional[str],
    description: Optional[str] = None,
) -> SystemSetting:
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = SystemSetting(key=key, value=value, description=description)
        db.add(setting)
    else:
        setting.value = value
        if description is not None:
            setting.description = description

    await db.flush()
    return setting


async def delete_system_setting(db: AsyncSession, key: str) -> None:
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    if setting is not None:
        await db.delete(setting)
        await db.flush()

