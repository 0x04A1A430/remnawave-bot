"""Сервис истории изменений конфигурации меню."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MenuLayoutHistory


class MenuLayoutHistoryService:
    """Сервис для управления историей изменений меню."""

    @classmethod
    async def save_history(
        cls,
        db: AsyncSession,
        config: Dict[str, Any],
        action: str,
        changes_summary: Optional[str] = None,
        user_info: Optional[str] = None,
    ) -> MenuLayoutHistory:
        """Сохранить запись в историю изменений."""
        history = MenuLayoutHistory(
            config_json=json.dumps(config, ensure_ascii=False),
            action=action,
            changes_summary=changes_summary or f"Action: {action}",
            user_info=user_info,
        )
        db.add(history)
        await db.commit()
        await db.refresh(history)
        return history

    @classmethod
    async def get_history(
        cls,
        db: AsyncSession,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Получить историю изменений."""
        result = await db.execute(
            select(MenuLayoutHistory)
            .order_by(desc(MenuLayoutHistory.created_at))
            .limit(limit)
            .offset(offset)
        )
        entries = result.scalars().all()

        return [
            {
                "id": entry.id,
                "action": entry.action,
                "changes_summary": entry.changes_summary,
                "user_info": entry.user_info,
                "created_at": entry.created_at,
            }
            for entry in entries
        ]

    @classmethod
    async def get_history_count(cls, db: AsyncSession) -> int:
        """Получить общее количество записей истории."""
        result = await db.execute(
            select(func.count(MenuLayoutHistory.id))
        )
        return result.scalar() or 0

    @classmethod
    async def get_history_entry(
        cls,
        db: AsyncSession,
        history_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Получить конкретную запись истории с конфигурацией."""
        result = await db.execute(
            select(MenuLayoutHistory).where(MenuLayoutHistory.id == history_id)
        )
        entry = result.scalar_one_or_none()

        if not entry:
            return None

        return {
            "id": entry.id,
            "action": entry.action,
            "changes_summary": entry.changes_summary,
            "user_info": entry.user_info,
            "created_at": entry.created_at,
            "config": json.loads(entry.config_json),
        }

    @classmethod
    async def rollback_to_history(
        cls,
        db: AsyncSession,
        history_id: int,
        get_config_func,
        save_config_func,
        user_info: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Откатить конфигурацию к записи из истории.

        Args:
            db: Сессия базы данных
            history_id: ID записи истории
            get_config_func: Функция для получения текущей конфигурации
            save_config_func: Функция для сохранения конфигурации
            user_info: Информация о пользователе
        """
        entry = await cls.get_history_entry(db, history_id)
        if not entry:
            raise KeyError(f"History entry {history_id} not found")

        config = entry["config"]

        # Сохраняем текущую конфигурацию в историю перед откатом
        current_config = await get_config_func(db)
        await cls.save_history(
            db, current_config, "rollback_backup",
            f"Backup before rollback to history #{history_id}",
            user_info
        )

        # Применяем конфигурацию из истории
        await save_config_func(db, config)

        # Сохраняем запись об откате
        await cls.save_history(
            db, config, "rollback",
            f"Rollback to history #{history_id}",
            user_info
        )

        return config
