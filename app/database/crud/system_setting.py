from typing import Optional

from typing import Optional, Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SystemSetting, SystemSettingChange


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


async def log_system_setting_change(
    db: AsyncSession,
    *,
    key: str,
    old_value: Optional[str],
    new_value: Optional[str],
    changed_by: Optional[int] = None,
    changed_by_username: Optional[str] = None,
    source: str = "bot",
    reason: Optional[str] = None,
) -> SystemSettingChange:
    change = SystemSettingChange(
        key=key,
        old_value=old_value,
        new_value=new_value,
        changed_by=changed_by,
        changed_by_username=changed_by_username,
        source=source,
        reason=reason,
    )
    db.add(change)
    await db.flush()
    return change


async def get_recent_system_setting_changes(
    db: AsyncSession,
    limit: int = 10,
) -> Sequence[SystemSettingChange]:
    stmt = (
        select(SystemSettingChange)
        .order_by(desc(SystemSettingChange.created_at))
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()

