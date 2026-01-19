"""CRUD operations for TicketNotification."""

import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, func, desc, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import TicketNotification, Ticket, User


logger = logging.getLogger(__name__)


class TicketNotificationCRUD:
    """CRUD operations for ticket notifications in cabinet."""

    @staticmethod
    async def get_by_id(db: AsyncSession, notification_id: int) -> Optional[TicketNotification]:
        """Get notification by ID."""
        query = select(TicketNotification).where(TicketNotification.id == notification_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        ticket_id: int,
        user_id: int,
        notification_type: str,
        message: Optional[str] = None,
        is_for_admin: bool = False,
    ) -> TicketNotification:
        """Create a new ticket notification."""
        notification = TicketNotification(
            ticket_id=ticket_id,
            user_id=user_id,
            notification_type=notification_type,
            message=message,
            is_for_admin=is_for_admin,
            is_read=False,
            created_at=datetime.utcnow(),
        )
        db.add(notification)
        await db.commit()
        await db.refresh(notification)
        return notification

    @staticmethod
    async def get_user_notifications(
        db: AsyncSession,
        user_id: int,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[TicketNotification]:
        """Get notifications for a user (not admin)."""
        query = (
            select(TicketNotification)
            .where(
                TicketNotification.user_id == user_id,
                TicketNotification.is_for_admin == False,
            )
            .options(selectinload(TicketNotification.ticket))
            .order_by(desc(TicketNotification.created_at))
        )

        if unread_only:
            query = query.where(TicketNotification.is_read == False)

        query = query.offset(offset).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_admin_notifications(
        db: AsyncSession,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[TicketNotification]:
        """Get notifications for admins."""
        query = (
            select(TicketNotification)
            .where(TicketNotification.is_for_admin == True)
            .options(selectinload(TicketNotification.ticket))
            .order_by(desc(TicketNotification.created_at))
        )

        if unread_only:
            query = query.where(TicketNotification.is_read == False)

        query = query.offset(offset).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def count_unread_user(db: AsyncSession, user_id: int) -> int:
        """Count unread notifications for a user."""
        query = select(func.count()).select_from(TicketNotification).where(
            TicketNotification.user_id == user_id,
            TicketNotification.is_for_admin == False,
            TicketNotification.is_read == False,
        )
        result = await db.execute(query)
        return result.scalar() or 0

    @staticmethod
    async def count_unread_admin(db: AsyncSession) -> int:
        """Count unread notifications for admins."""
        query = select(func.count()).select_from(TicketNotification).where(
            TicketNotification.is_for_admin == True,
            TicketNotification.is_read == False,
        )
        result = await db.execute(query)
        return result.scalar() or 0

    @staticmethod
    async def mark_as_read(db: AsyncSession, notification_id: int) -> bool:
        """Mark a notification as read."""
        query = (
            update(TicketNotification)
            .where(TicketNotification.id == notification_id)
            .values(is_read=True, read_at=datetime.utcnow())
        )
        result = await db.execute(query)
        await db.commit()
        return result.rowcount > 0

    @staticmethod
    async def mark_all_as_read_user(db: AsyncSession, user_id: int) -> int:
        """Mark all notifications as read for a user."""
        query = (
            update(TicketNotification)
            .where(
                TicketNotification.user_id == user_id,
                TicketNotification.is_for_admin == False,
                TicketNotification.is_read == False,
            )
            .values(is_read=True, read_at=datetime.utcnow())
        )
        result = await db.execute(query)
        await db.commit()
        return result.rowcount

    @staticmethod
    async def mark_all_as_read_admin(db: AsyncSession) -> int:
        """Mark all admin notifications as read."""
        query = (
            update(TicketNotification)
            .where(
                TicketNotification.is_for_admin == True,
                TicketNotification.is_read == False,
            )
            .values(is_read=True, read_at=datetime.utcnow())
        )
        result = await db.execute(query)
        await db.commit()
        return result.rowcount

    @staticmethod
    async def mark_ticket_notifications_as_read(
        db: AsyncSession, ticket_id: int, user_id: int, is_admin: bool = False
    ) -> int:
        """Mark all notifications for a specific ticket as read."""
        query = (
            update(TicketNotification)
            .where(
                TicketNotification.ticket_id == ticket_id,
                TicketNotification.is_read == False,
            )
            .values(is_read=True, read_at=datetime.utcnow())
        )

        if is_admin:
            query = query.where(TicketNotification.is_for_admin == True)
        else:
            query = query.where(
                TicketNotification.user_id == user_id,
                TicketNotification.is_for_admin == False,
            )

        result = await db.execute(query)
        await db.commit()
        return result.rowcount

    @staticmethod
    async def create_admin_notification_for_new_ticket(
        db: AsyncSession, ticket: Ticket
    ) -> Optional[TicketNotification]:
        """Create notification for admins about new ticket."""
        from app.services.support_settings_service import SupportSettingsService

        if not SupportSettingsService.get_cabinet_admin_notifications_enabled():
            return None

        title = (ticket.title or "").strip()[:50]
        message = f"Новый тикет #{ticket.id}: {title}"

        return await TicketNotificationCRUD.create(
            db=db,
            ticket_id=ticket.id,
            user_id=ticket.user_id,
            notification_type="new_ticket",
            message=message,
            is_for_admin=True,
        )

    @staticmethod
    async def create_user_notification_for_admin_reply(
        db: AsyncSession, ticket: Ticket, reply_preview: str
    ) -> Optional[TicketNotification]:
        """Create notification for user about admin reply."""
        from app.services.support_settings_service import SupportSettingsService

        if not SupportSettingsService.get_cabinet_user_notifications_enabled():
            return None

        preview = (reply_preview or "").strip()[:100]
        message = f"Ответ на тикет #{ticket.id}: {preview}..."

        return await TicketNotificationCRUD.create(
            db=db,
            ticket_id=ticket.id,
            user_id=ticket.user_id,
            notification_type="admin_reply",
            message=message,
            is_for_admin=False,
        )

    @staticmethod
    async def create_admin_notification_for_user_reply(
        db: AsyncSession, ticket: Ticket, reply_preview: str
    ) -> Optional[TicketNotification]:
        """Create notification for admins about user reply."""
        from app.services.support_settings_service import SupportSettingsService

        if not SupportSettingsService.get_cabinet_admin_notifications_enabled():
            return None

        preview = (reply_preview or "").strip()[:100]
        message = f"Ответ в тикете #{ticket.id}: {preview}..."

        return await TicketNotificationCRUD.create(
            db=db,
            ticket_id=ticket.id,
            user_id=ticket.user_id,
            notification_type="user_reply",
            message=message,
            is_for_admin=True,
        )
