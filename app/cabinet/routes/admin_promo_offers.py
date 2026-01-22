"""Admin promo offers routes for cabinet."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.discount_offer import (
    count_discount_offers,
    list_discount_offers,
    upsert_discount_offer,
)
from app.database.crud.promo_offer_log import list_promo_offer_logs
from app.database.crud.promo_offer_template import (
    ensure_default_templates,
    get_promo_offer_template_by_id,
    list_promo_offer_templates,
    update_promo_offer_template,
)
from app.database.crud.user import get_user_by_telegram_id
from app.database.models import DiscountOffer, PromoOfferLog, PromoOfferTemplate, User
from app.handlers.admin.messages import get_custom_users, get_target_users
from app.utils.miniapp_buttons import build_miniapp_or_callback_button

from ..dependencies import get_cabinet_db, get_current_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/promo-offers", tags=["Admin Promo Offers"])


# ============== Schemas ==============

class PromoOfferUserInfo(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None


class PromoOfferResponse(BaseModel):
    id: int
    user_id: int
    subscription_id: Optional[int] = None
    notification_type: Optional[str] = None
    discount_percent: Optional[int] = None
    bonus_amount_kopeks: Optional[int] = None
    expires_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    is_active: bool
    effect_type: Optional[str] = None
    extra_data: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    user: Optional[PromoOfferUserInfo] = None


class PromoOfferListResponse(BaseModel):
    items: List[PromoOfferResponse]
    total: int
    limit: int
    offset: int


class PromoOfferTemplateResponse(BaseModel):
    id: int
    name: str
    offer_type: str
    message_text: str
    button_text: str
    valid_hours: int
    discount_percent: int
    bonus_amount_kopeks: int
    active_discount_hours: Optional[int] = None
    test_duration_hours: Optional[int] = None
    test_squad_uuids: List[str] = Field(default_factory=list)
    is_active: bool
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PromoOfferTemplateListResponse(BaseModel):
    items: List[PromoOfferTemplateResponse]


class PromoOfferTemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    message_text: Optional[str] = None
    button_text: Optional[str] = None
    valid_hours: Optional[int] = Field(None, ge=1)
    discount_percent: Optional[int] = Field(None, ge=0)
    bonus_amount_kopeks: Optional[int] = Field(None, ge=0)
    active_discount_hours: Optional[int] = Field(None, ge=1)
    test_duration_hours: Optional[int] = Field(None, ge=1)
    test_squad_uuids: Optional[List[str]] = None
    is_active: Optional[bool] = None


class PromoOfferBroadcastRequest(BaseModel):
    notification_type: str = Field(..., min_length=1)
    valid_hours: int = Field(..., ge=1)
    discount_percent: int = Field(0, ge=0)
    bonus_amount_kopeks: int = Field(0, ge=0)
    effect_type: str = Field("percent_discount", min_length=1)
    extra_data: Dict[str, Any] = Field(default_factory=dict)
    target: Optional[str] = None
    user_id: Optional[int] = None
    telegram_id: Optional[int] = None
    # Telegram notification options
    send_notification: bool = Field(False, description="Send Telegram notification to users")
    message_text: Optional[str] = Field(None, description="Custom message text (HTML)")
    button_text: Optional[str] = Field(None, description="Button text")


class PromoOfferBroadcastResponse(BaseModel):
    created_offers: int
    user_ids: List[int]
    target: Optional[str] = None
    notifications_sent: int = 0
    notifications_failed: int = 0


class PromoOfferLogOfferInfo(BaseModel):
    id: int
    notification_type: Optional[str] = None
    discount_percent: Optional[int] = None
    bonus_amount_kopeks: Optional[int] = None
    effect_type: Optional[str] = None
    expires_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    is_active: Optional[bool] = None


class PromoOfferLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    offer_id: Optional[int] = None
    action: str
    source: Optional[str] = None
    percent: Optional[int] = None
    effect_type: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    user: Optional[PromoOfferUserInfo] = None
    offer: Optional[PromoOfferLogOfferInfo] = None


class PromoOfferLogListResponse(BaseModel):
    items: List[PromoOfferLogResponse]
    total: int
    limit: int
    offset: int


# ============== Helpers ==============

def _serialize_user(user: Optional[User]) -> Optional[PromoOfferUserInfo]:
    if not user:
        return None
    return PromoOfferUserInfo(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        full_name=getattr(user, "full_name", None),
    )


def _serialize_offer(offer: DiscountOffer) -> PromoOfferResponse:
    return PromoOfferResponse(
        id=offer.id,
        user_id=offer.user_id,
        subscription_id=offer.subscription_id,
        notification_type=offer.notification_type,
        discount_percent=offer.discount_percent,
        bonus_amount_kopeks=offer.bonus_amount_kopeks,
        expires_at=offer.expires_at,
        claimed_at=offer.claimed_at,
        is_active=offer.is_active,
        effect_type=offer.effect_type,
        extra_data=offer.extra_data or {},
        created_at=offer.created_at,
        updated_at=offer.updated_at,
        user=_serialize_user(getattr(offer, "user", None)),
    )


def _serialize_template(template: PromoOfferTemplate) -> PromoOfferTemplateResponse:
    return PromoOfferTemplateResponse(
        id=template.id,
        name=template.name,
        offer_type=template.offer_type,
        message_text=template.message_text,
        button_text=template.button_text,
        valid_hours=template.valid_hours,
        discount_percent=template.discount_percent,
        bonus_amount_kopeks=template.bonus_amount_kopeks,
        active_discount_hours=template.active_discount_hours,
        test_duration_hours=template.test_duration_hours,
        test_squad_uuids=[str(uuid) for uuid in (template.test_squad_uuids or [])],
        is_active=template.is_active,
        created_by=template.created_by,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _serialize_log(entry: PromoOfferLog) -> PromoOfferLogResponse:
    user_info = _serialize_user(getattr(entry, "user", None))

    offer = getattr(entry, "offer", None)
    offer_info: Optional[PromoOfferLogOfferInfo] = None
    if offer:
        offer_info = PromoOfferLogOfferInfo(
            id=offer.id,
            notification_type=offer.notification_type,
            discount_percent=offer.discount_percent,
            bonus_amount_kopeks=offer.bonus_amount_kopeks,
            effect_type=offer.effect_type,
            expires_at=offer.expires_at,
            claimed_at=offer.claimed_at,
            is_active=offer.is_active,
        )

    return PromoOfferLogResponse(
        id=entry.id,
        user_id=entry.user_id,
        offer_id=entry.offer_id,
        action=entry.action,
        source=entry.source,
        percent=entry.percent,
        effect_type=entry.effect_type,
        details=entry.details or {},
        created_at=entry.created_at,
        user=user_info,
        offer=offer_info,
    )


async def _resolve_target_users(db: AsyncSession, target: str) -> list[User]:
    normalized = target.strip().lower()
    if normalized.startswith("custom_"):
        criteria = normalized[len("custom_"):]
        return await get_custom_users(db, criteria)
    return await get_target_users(db, normalized)


# ============== Template Endpoints ==============

@router.get("/templates", response_model=PromoOfferTemplateListResponse)
async def list_templates(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> PromoOfferTemplateListResponse:
    """Get list of promo offer templates."""
    templates = await list_promo_offer_templates(db)

    # Initialize default templates if none exist
    if not templates:
        templates = await ensure_default_templates(db, created_by=admin.id)

    return PromoOfferTemplateListResponse(
        items=[_serialize_template(template) for template in templates]
    )


@router.get("/templates/{template_id}", response_model=PromoOfferTemplateResponse)
async def get_template(
    template_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> PromoOfferTemplateResponse:
    """Get a promo offer template."""
    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    return _serialize_template(template)


@router.patch("/templates/{template_id}", response_model=PromoOfferTemplateResponse)
async def update_template(
    template_id: int,
    payload: PromoOfferTemplateUpdateRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> PromoOfferTemplateResponse:
    """Update a promo offer template."""
    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")

    if payload.test_squad_uuids is not None:
        normalized_squads = [str(uuid).strip() for uuid in payload.test_squad_uuids if str(uuid).strip()]
    else:
        normalized_squads = None

    updated_template = await update_promo_offer_template(
        db,
        template,
        name=payload.name,
        message_text=payload.message_text,
        button_text=payload.button_text,
        valid_hours=payload.valid_hours,
        discount_percent=payload.discount_percent,
        bonus_amount_kopeks=payload.bonus_amount_kopeks,
        active_discount_hours=payload.active_discount_hours,
        test_duration_hours=payload.test_duration_hours,
        test_squad_uuids=normalized_squads,
        is_active=payload.is_active,
    )

    return _serialize_template(updated_template)


# ============== Offer Endpoints ==============

@router.get("", response_model=PromoOfferListResponse)
async def list_offers(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: Optional[int] = Query(None, ge=1),
    is_active: Optional[bool] = Query(None),
) -> PromoOfferListResponse:
    """Get list of promo offers."""
    offers = await list_discount_offers(
        db,
        offset=offset,
        limit=limit,
        user_id=user_id,
        is_active=is_active,
    )
    total = await count_discount_offers(
        db,
        user_id=user_id,
        is_active=is_active,
    )

    return PromoOfferListResponse(
        items=[_serialize_offer(offer) for offer in offers],
        total=total,
        limit=limit,
        offset=offset,
    )


def _get_bot() -> Bot:
    """Create bot instance for sending notifications."""
    return Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def _build_default_promo_message(
    discount_percent: int,
    bonus_amount_kopeks: int,
    valid_hours: int,
) -> str:
    """Build default promo notification message."""
    lines = ["🎁 <b>Специальное предложение для вас!</b>\n"]

    if discount_percent > 0:
        lines.append(f"🔥 Скидка <b>{discount_percent}%</b> на подписку")
    if bonus_amount_kopeks > 0:
        bonus_rub = bonus_amount_kopeks / 100
        lines.append(f"💰 Бонус <b>{bonus_rub:.0f}₽</b> на баланс")

    lines.append(f"\n⏰ Предложение действует <b>{valid_hours} ч.</b>")
    lines.append("\nНажмите кнопку ниже, чтобы активировать!")

    return "\n".join(lines)


async def _send_promo_notifications(
    offers_to_notify: List[tuple[User, DiscountOffer]],
    message_text: Optional[str],
    button_text: Optional[str],
    discount_percent: int,
    bonus_amount_kopeks: int,
    valid_hours: int,
) -> tuple[int, int]:
    """Send Telegram notifications for promo offers.

    Returns:
        Tuple of (sent_count, failed_count)
    """
    if not offers_to_notify:
        return 0, 0

    bot = _get_bot()
    sent = 0
    failed = 0

    # Build message text
    text = message_text or _build_default_promo_message(
        discount_percent=discount_percent,
        bonus_amount_kopeks=bonus_amount_kopeks,
        valid_hours=valid_hours,
    )

    # Default button text
    btn_text = button_text or "🎁 Получить"

    semaphore = asyncio.Semaphore(20)

    async def send_single(user: User, offer: DiscountOffer) -> bool:
        async with semaphore:
            try:
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            build_miniapp_or_callback_button(
                                text=btn_text,
                                callback_data=f"claim_discount_{offer.id}",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="❌ Закрыть",
                                callback_data="promo_offer_close",
                            )
                        ],
                    ]
                )

                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    reply_markup=keyboard,
                )
                return True
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                logger.warning(
                    "Failed to send promo notification to user %s: %s",
                    user.telegram_id,
                    exc,
                )
                return False
            except Exception as exc:
                logger.error(
                    "Error sending promo notification to user %s: %s",
                    user.telegram_id,
                    exc,
                )
                return False

    # Send in batches
    batch_size = 50
    for i in range(0, len(offers_to_notify), batch_size):
        batch = offers_to_notify[i : i + batch_size]
        tasks = [send_single(user, offer) for user, offer in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, bool) and result:
                sent += 1
            else:
                failed += 1

        # Small delay between batches
        if i + batch_size < len(offers_to_notify):
            await asyncio.sleep(0.1)

    # Close bot session
    await bot.session.close()

    return sent, failed


@router.post("/broadcast", response_model=PromoOfferBroadcastResponse, status_code=status.HTTP_201_CREATED)
async def broadcast_offer(
    payload: PromoOfferBroadcastRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> PromoOfferBroadcastResponse:
    """Broadcast promo offer to users with optional Telegram notification."""
    recipients: dict[int, User] = {}

    # Resolve target segment
    if payload.target:
        users = await _resolve_target_users(db, payload.target)
        recipients.update({user.id: user for user in users if user and user.id})

    # Resolve specific user
    target_user_id = payload.user_id
    user: Optional[User] = None

    if payload.telegram_id is not None:
        user = await get_user_by_telegram_id(db, payload.telegram_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if target_user_id and target_user_id != user.id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Provided user_id does not match telegram_id",
            )
        target_user_id = user.id

    if target_user_id is not None:
        if user is None:
            user = await db.get(User, target_user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        recipients[target_user_id] = user

    if not recipients:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No recipients: specify target or user",
        )

    # Create offers for all recipients and collect (user, offer) pairs
    created_offers = 0
    offers_to_notify: List[tuple[User, DiscountOffer]] = []

    for recipient in recipients.values():
        offer = await upsert_discount_offer(
            db,
            user_id=recipient.id,
            subscription_id=None,
            notification_type=payload.notification_type.strip(),
            discount_percent=payload.discount_percent,
            bonus_amount_kopeks=payload.bonus_amount_kopeks,
            valid_hours=payload.valid_hours,
            effect_type=payload.effect_type,
            extra_data=payload.extra_data,
        )
        if offer:
            created_offers += 1
            offers_to_notify.append((recipient, offer))

    # Send Telegram notifications if requested
    notifications_sent = 0
    notifications_failed = 0

    if payload.send_notification and offers_to_notify:
        notifications_sent, notifications_failed = await _send_promo_notifications(
            offers_to_notify=offers_to_notify,
            message_text=payload.message_text,
            button_text=payload.button_text,
            discount_percent=payload.discount_percent,
            bonus_amount_kopeks=payload.bonus_amount_kopeks,
            valid_hours=payload.valid_hours,
        )

    return PromoOfferBroadcastResponse(
        created_offers=created_offers,
        user_ids=list(recipients.keys()),
        target=payload.target,
        notifications_sent=notifications_sent,
        notifications_failed=notifications_failed,
    )


# ============== Log Endpoints ==============

@router.get("/logs", response_model=PromoOfferLogListResponse)
async def get_logs(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: Optional[int] = Query(None, ge=1),
    action: Optional[str] = Query(None, min_length=1),
) -> PromoOfferLogListResponse:
    """Get promo offer logs."""
    logs, total = await list_promo_offer_logs(
        db,
        offset=offset,
        limit=limit,
        user_id=user_id,
        action=action,
    )

    return PromoOfferLogListResponse(
        items=[_serialize_log(entry) for entry in logs],
        total=int(total),
        limit=limit,
        offset=offset,
    )
