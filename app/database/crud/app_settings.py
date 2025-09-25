from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AppSetting


class AppSettingsCRUD:
    @staticmethod
    async def list_all(db: AsyncSession) -> List[AppSetting]:
        result = await db.execute(select(AppSetting).order_by(AppSetting.setting_key))
        return list(result.scalars())

    @staticmethod
    async def get_by_key(db: AsyncSession, setting_key: str) -> Optional[AppSetting]:
        result = await db.execute(
            select(AppSetting).where(AppSetting.setting_key == setting_key)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert(db: AsyncSession, setting_key: str, value: str) -> AppSetting:
        record = await AppSettingsCRUD.get_by_key(db, setting_key)
        now = datetime.utcnow()

        if record:
            record.value = value
            record.updated_at = now
        else:
            record = AppSetting(
                setting_key=setting_key,
                value=value,
                created_at=now,
                updated_at=now,
            )
            db.add(record)

        await db.flush()
        return record

    @staticmethod
    async def delete_by_key(db: AsyncSession, setting_key: str) -> bool:
        record = await AppSettingsCRUD.get_by_key(db, setting_key)
        if not record:
            return False
        await db.delete(record)
        await db.flush()
        return True
