"""Admin inline query handler for gifting subscriptions.

Usage (for admin):
  @botusername @user              — shows current subscription info
  @botusername @user 30          — 30 days
  @botusername @user 30 500      — 30 days + 500 GB traffic
  @botusername @user 30 500 3    — 30 days, 500 GB, 3 devices
  @botusername @user 0 500       — set 500 GB only
  @botusername @user 0 0 3       — set 3 devices only
  @botusername @user -1          — forever (year 2099)
  @botusername @user 30 -1       — 30 days + unlimited traffic
  @botusername @user 30 500 -1   — 30 days, 500 GB, 999 devices

All numeric params default to 0 = no change.
Special: -1 for days = forever (~year 2099), -1 for traffic_gb = unlimited (0),
         -1 for devices = 999.
Deep link prefix: bs_<gift_code>
"""

import html
import secrets

import structlog
from aiogram import Dispatcher, types
from sqlalchemy import select

from app.config import settings
from app.database.crud.subscription import get_subscription_by_user_id
from app.database.crud.user import get_user_by_telegram_id
from app.database.database import AsyncSessionLocal
from app.database.models import InlineGiftSubscription, User
from app.localization.loader import DEFAULT_LANGUAGE
from app.localization.texts import get_texts


logger = structlog.get_logger(__name__)

_GIFT_PREFIX = 'bs_'
_GIFT_PREFIX_NEW_USER = 'rbs_'  # for recipients not yet registered in the bot


def _is_admin(telegram_id: int) -> bool:
    return settings.is_admin(telegram_id)


_FOREVER_DAYS = (2099 - 2025) * 365  # approx days from now to year 2099
_UNLIMITED_TRAFFIC = 0  # 0 = unlimited in the DB model
_MAX_DEVICES = 999


def _parse_query(query_text: str) -> tuple[str, int, int, int]:
    """Parse '@user [days] [traffic_gb] [devices]'.
    Returns (username, days, traffic_gb, devices); numeric params default to 0.
    Special values: -1 for days = forever, -1 for traffic_gb = unlimited, -1 for devices = 999.
    """
    parts = query_text.strip().split()
    if not parts:
        return '', 0, 0, 0

    username = parts[0].lstrip('@')

    def _int_param(s: str, allow_neg_one: bool = False) -> int:
        try:
            v = int(s)
        except (ValueError, TypeError):
            return 0
        if v == -1 and allow_neg_one:
            return -1
        return max(0, v)

    days = _int_param(parts[1], allow_neg_one=True) if len(parts) > 1 else 0
    traffic_gb = _int_param(parts[2], allow_neg_one=True) if len(parts) > 2 else 0
    devices = _int_param(parts[3], allow_neg_one=True) if len(parts) > 3 else 0

    # Resolve -1 sentinel values
    if days == -1:
        days = _FOREVER_DAYS
    # traffic_gb == -1 means "set to unlimited"; keep as -1 to distinguish from 0 (no change)
    if devices == -1:
        devices = _MAX_DEVICES

    return username, days, traffic_gb, devices


def _days_label_short(days: int, texts) -> str:
    if days >= _FOREVER_DAYS:
        return texts.t('INLINE_GIFT_LABEL_FOREVER', 'Навсегда')
    if days < 365:
        return texts.t('INLINE_GIFT_LABEL_DAYS_SHORT', '{n} дн.').format(n=days)
    if days == 365:
        return texts.t('INLINE_GIFT_LABEL_YEAR', '1 г.')
    if days % 365 == 0:
        return texts.t('INLINE_GIFT_LABEL_YEARS', '{n} г.').format(n=days // 365)
    return texts.t('INLINE_GIFT_LABEL_DAYS_SHORT', '{n} дн.').format(n=days)


def _fmt_traffic(gb: int, texts) -> str:
    if gb == 0 or gb == -1:
        return texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит')
    return f'{gb} {texts.t("INLINE_GIFT_GB_SUFFIX", "ГБ")}'


def _build_caption(username: str, days: int, traffic_gb: int, devices: int, texts) -> str:
    """Build the message text sent to the chat (no emojis, clean text)."""
    safe_name = html.escape(username)
    lines = []
    if days:
        lines.append(f'+{_days_label_short(days, texts)}')
    if traffic_gb:
        if traffic_gb == -1:
            lines.append(texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит'))
        else:
            lines.append(_fmt_traffic(traffic_gb, texts))
    if devices:
        lines.append(f'{devices} {texts.t("INLINE_GIFT_DEVICES_SUFFIX", "уст.")}')

    body = '\n'.join(lines) if lines else '—'
    header = texts.t('INLINE_GIFT_CAPTION_HEADER', 'Подарочная подписка для')
    hint = texts.t('INLINE_GIFT_CAPTION_HINT', 'Нажмите кнопку ниже, чтобы активировать.')
    return (
        f'<b>{header} <code>{safe_name}</code></b>\n\n'
        f'<blockquote>{body}</blockquote>\n\n'
        f'<code>{hint}</code>'
    )


async def handle_admin_inline_query(inline_query: types.InlineQuery) -> None:
    if not _is_admin(inline_query.from_user.id):
        await inline_query.answer([], cache_time=5)
        return

    texts = get_texts(DEFAULT_LANGUAGE)
    query_text = (inline_query.query or '').strip()
    username, days, traffic_gb, devices = _parse_query(query_text)

    if not username:
        await inline_query.answer(
            [],
            cache_time=1,
            switch_pm_text=texts.t('INLINE_GIFT_QUERY_HINT', '@user дни [гб [устройств]]'),
            switch_pm_parameter='help',
        )
        return

    # Look up recipient
    recipient_display = f'@{username}'
    sub_info_lines: list[str] = []
    db_user_found = False

    async with AsyncSessionLocal() as db:
        from sqlalchemy import func as sql_func
        result = await db.execute(
            select(User).where(sql_func.lower(User.username) == username.lower())
        )
        db_user = result.scalars().first()
        cur_days = 0
        cur_traffic = 0
        cur_devices = 1
        sub = None
        if db_user:
            db_user_found = True
            name_parts = [db_user.first_name or '', db_user.last_name or '']
            full_name = ' '.join(p for p in name_parts if p).strip()
            if full_name:
                recipient_display = f'{full_name} (@{username})'
            sub = await get_subscription_by_user_id(db, db_user.id)
            if sub:
                cur_days = max(0, sub.days_left) if hasattr(sub, 'days_left') else 0
                cur_traffic = sub.traffic_limit_gb or 0
                cur_devices = sub.device_limit or 1
                gb = sub.traffic_limit_gb
                traffic_str = texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит') if gb == 0 else f'{gb} ГБ'
                sub_info_lines.append(f'{sub.days_left} дн.')
                sub_info_lines.append(traffic_str)
                sub_info_lines.append(f'{sub.device_limit} уст.')
            else:
                sub_info_lines.append(texts.t('INLINE_GIFT_NO_SUB', 'нет подписки'))

    thumbnail_url = texts.t('INLINE_GIFT_THUMBNAIL_URL', 'https://raw.githubusercontent.com/cy7su/cy7su/refs/heads/main/GIFT.png')

    # If no params entered yet — show current subscription state only (no gift to create)
    if days == 0 and traffic_gb == 0 and devices == 0:
        current_info = ' | '.join(sub_info_lines) if sub_info_lines else '—'
        results = [
            types.InlineQueryResultArticle(
                id='info_only',
                title=recipient_display,
                description=current_info,
                input_message_content=types.InputTextMessageContent(
                    message_text=f'@{html.escape(username)}',
                    parse_mode='HTML',
                ),
                thumbnail_url=thumbnail_url,
                thumbnail_width=512,
                thumbnail_height=512,
            )
        ]
        await inline_query.answer(results, cache_time=0, is_personal=True)
        return

    # Build gift result
    gift_code = secrets.token_urlsafe(32)
    bot_username = settings.BOT_USERNAME or ''
    # Use rbs_ prefix when recipient is not registered in the bot yet
    prefix = _GIFT_PREFIX if db_user_found else _GIFT_PREFIX_NEW_USER
    deep_link = f'https://t.me/{bot_username}?start={prefix}{gift_code}'

    caption = _build_caption(username, days, traffic_gb, devices, texts)

    parts_summary = []
    if days:
        parts_summary.append(_days_label_short(days, texts))
    if traffic_gb:
        parts_summary.append(_fmt_traffic(traffic_gb, texts))
    if devices:
        parts_summary.append(f'{devices} уст.')
    gift_summary = ', '.join(parts_summary)

    # Description shows: result values after activation
    result_parts = []
    if days:
        result_days = cur_days + days
        result_parts.append(_days_label_short(result_days, texts))
    if traffic_gb:
        # traffic_gb == -1 means unlimited
        result_parts.append(_fmt_traffic(traffic_gb, texts))
    elif sub_info_lines and sub:
        # no traffic change, show current
        pass
    if devices:
        result_devices = max(cur_devices, devices)
        result_parts.append(f'{result_devices} уст.')

    if sub_info_lines and sub:
        description = f'{" | ".join(sub_info_lines)} → {", ".join(result_parts)}' if result_parts else f'{" | ".join(sub_info_lines)} → {gift_summary}'
    else:
        description = gift_summary

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text=texts.t('INLINE_GIFT_ACTIVATE_BUTTON', 'Активировать'),
            url=deep_link,
        )
    ]])

    results = [
        types.InlineQueryResultArticle(
            id=gift_code,
            title=f'{recipient_display} — {gift_summary}',
            description=description,
            thumbnail_url=thumbnail_url,
            thumbnail_width=512,
            thumbnail_height=512,
            input_message_content=types.InputTextMessageContent(
                message_text=caption,
                parse_mode='HTML',
                link_preview_options=types.LinkPreviewOptions(
                    show_above_text=True,
                    url=thumbnail_url,
                ),
            ),
            reply_markup=keyboard,
        )
    ]

    await inline_query.answer(results, cache_time=0, is_personal=True)


async def handle_chosen_inline_result(chosen: types.ChosenInlineResult) -> None:
    """Create the gift DB record on selection."""
    if not _is_admin(chosen.from_user.id):
        return

    gift_code = chosen.result_id
    if gift_code == 'info_only':
        return

    inline_message_id = chosen.inline_message_id
    query_text = chosen.query or ''

    username, days, traffic_gb, devices = _parse_query(query_text)
    if not username or (days == 0 and traffic_gb == 0 and devices == 0):
        logger.warning('chosen_inline_result with empty params', gift_code=gift_code, query=query_text)
        return

    async with AsyncSessionLocal() as db:
        from sqlalchemy import func as sql_func
        result = await db.execute(
            select(User).where(sql_func.lower(User.username) == username.lower())
        )
        db_user = result.scalars().first()
        recipient_telegram_id = db_user.telegram_id if db_user else 0

        admin_user = await get_user_by_telegram_id(db, chosen.from_user.id)

        gift = InlineGiftSubscription(
            gift_code=gift_code,
            recipient_telegram_id=recipient_telegram_id,
            sender_user_id=admin_user.id if admin_user else None,
            days=days,
            traffic_limit_gb=traffic_gb,
            device_limit=devices,
            inline_message_id=inline_message_id or f'u:{username}',
        )
        db.add(gift)
        await db.commit()

        logger.info(
            'Inline gift created',
            gift_code=gift_code,
            username=username,
            days=days,
            traffic_gb=traffic_gb,
            devices=devices,
            has_inline_message_id=bool(inline_message_id),
        )


def register_handlers(dp: Dispatcher) -> None:
    dp.inline_query.register(handle_admin_inline_query)
    dp.chosen_inline_result.register(handle_chosen_inline_result)
