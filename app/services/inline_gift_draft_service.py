"""Redis drafts for admin inline gifts.

Telegram delivers ``chosen_inline_result`` updates only when inline feedback is
enabled for the bot in @BotFather (and even then the delivery is probabilistic
because of result caching). The admin inline-gift flow used to create the
``inline_gift_subscriptions`` row exclusively from that update, so a bot without
feedback produced buttons pointing at codes that were never persisted: the
recipient pressed "Активировать", sent ``/start bs_<code>`` and silently got the
ordinary main menu.

Every answered inline query now also stores the gift payload in Redis under the
generated code. The first deep-link hit materialises the payload into the DB if
``chosen_inline_result`` never arrived, so activation no longer depends on
Telegram's feedback setting. The chosen-result handler still runs first in the
normal case and simply drops the draft after writing the real row.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import InlineGiftSubscription
from app.utils.cache import cache


logger = structlog.get_logger(__name__)

DRAFT_KEY_PREFIX = 'inline_gift_draft:'
DRAFT_TTL = 7 * 24 * 3600  # 7 days — matches the pending-referral retention

# Model columns that make up a gift. Kept as an explicit allow-list so a stale or
# malformed cache entry can never inject arbitrary attributes into the ORM row.
_DRAFT_FIELDS: tuple[str, ...] = (
    'recipient_telegram_id',
    'sender_user_id',
    'gift_type',
    'days',
    'traffic_limit_gb',
    'device_limit',
    'discount_percent',
    'balance_amount_kopeks',
    'temp_traffic_gb',
    'temp_traffic_days',
    'reset_traffic',
    'max_activations',
    'intended_recipient',
)


def _draft_key(gift_code: str) -> str:
    return f'{DRAFT_KEY_PREFIX}{gift_code}'


async def save_gift_draft(gift_code: str, **fields: Any) -> bool:
    """Persist the gift payload under ``gift_code`` so /start can materialise it.

    ``fields`` must use model column names. Missing values are stored as ``None``
    and the model defaults (``max_activations=1``, ``activated_count=0``,
    ``is_activated=False``) are applied when the row is materialised.
    """
    if not gift_code:
        return False

    payload: dict[str, Any] = {name: fields.get(name) for name in _DRAFT_FIELDS}
    payload.setdefault('recipient_telegram_id', 0)
    if payload.get('recipient_telegram_id') is None:
        payload['recipient_telegram_id'] = 0
    payload['max_activations'] = payload.get('max_activations') or 1

    saved = await cache.set(_draft_key(gift_code), payload, expire=DRAFT_TTL)
    if saved:
        logger.debug(
            'Inline gift draft saved',
            gift_code_prefix=gift_code[:8],
            gift_type=payload.get('gift_type'),
        )
    else:
        logger.warning(
            'Failed to save inline gift draft (Redis unavailable); '
            'activation will rely on chosen_inline_result delivery',
            gift_code_prefix=gift_code[:8],
        )
    return saved


async def load_gift_draft(gift_code: str) -> dict[str, Any] | None:
    if not gift_code:
        return None
    draft = await cache.get(_draft_key(gift_code))
    return draft if isinstance(draft, dict) else None


async def drop_gift_draft(gift_code: str) -> None:
    if not gift_code:
        return
    await cache.delete(_draft_key(gift_code))


async def materialize_gift_draft(session: AsyncSession, gift_code: str) -> InlineGiftSubscription | None:
    """Create the gift row from its Redis draft if it is still missing.

    Idempotent and safe under a concurrent ``chosen_inline_result`` insert: the
    unique ``gift_code`` index turns the race into an IntegrityError, after which
    the already-inserted row is returned.

    The caller's session is committed here so the newly created row survives even
    when the caller has nothing else to flush (the preview path is read-only).
    """
    draft = await load_gift_draft(gift_code)
    if draft is None:
        return None

    existing = await session.execute(
        select(InlineGiftSubscription).where(InlineGiftSubscription.gift_code == gift_code).limit(1)
    )
    gift = existing.scalar_one_or_none()
    if gift is not None:
        await drop_gift_draft(gift_code)
        return gift

    values = {name: draft.get(name) for name in _DRAFT_FIELDS}
    if values.get('recipient_telegram_id') is None:
        values['recipient_telegram_id'] = 0
    values['max_activations'] = values.get('max_activations') or 1
    values['activated_count'] = 0
    values['is_activated'] = False

    gift = InlineGiftSubscription(gift_code=gift_code, inline_message_id=None, **values)
    session.add(gift)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.execute(
            select(InlineGiftSubscription).where(InlineGiftSubscription.gift_code == gift_code).limit(1)
        )
        gift = existing.scalar_one_or_none()
        if gift is None:
            logger.warning(
                'Failed to materialize inline gift draft after unique conflict',
                gift_code_prefix=gift_code[:8],
            )
            return None
    except Exception:
        await session.rollback()
        logger.exception(
            'Failed to materialize inline gift draft',
            gift_code_prefix=gift_code[:8],
        )
        return None

    await session.refresh(gift)
    await drop_gift_draft(gift_code)
    logger.info(
        'Inline gift materialized from Redis draft',
        gift_code_prefix=gift_code[:8],
        gift_type=gift.gift_type,
        recipient_telegram_id=gift.recipient_telegram_id,
        max_activations=gift.max_activations,
    )
    return gift
