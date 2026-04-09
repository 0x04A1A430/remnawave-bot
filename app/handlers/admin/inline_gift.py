"""Admin inline query handler for gifting subscriptions.

Usage (for admin):
  @botusername @user              — shows current subscription info
  @botusername @user 30          — 30 days
  @botusername @user 30 500      — 30 days + 500 GB traffic
  @botusername @user 30 500 3    — 30 days, 500 GB, 3 devices
  @botusername @user 0 500       — set 500 GB only
  @botusername @user 0 0 3       — set 3 devices only

All numeric params default to 0 = no change.
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


def _is_admin(telegram_id: int) -> bool:
    return settings.is_admin(telegram_id)


def _parse_query(query_text: str) -> tuple[str, int, int, int]:
    """Parse '@user [days] [traffic_gb] [devices]'.
    Returns (username, days, traffic_gb, devices); numeric params default to 0.
    """
    parts = query_text.strip().split()
    if not parts:
        return '', 0, 0, 0

    username = parts[0].lstrip('@')

    def _int(s: str) -> int:
        try:
            return max(0, int(s))
        except (ValueError, TypeError):
            return 0

    days = _int(parts[1]) if len(parts) > 1 else 0
    traffic_gb = _int(parts[2]) if len(parts) > 2 else 0
    devices = _int(parts[3]) if len(parts) > 3 else 0

    return username, days, traffic_gb, devices


def _days_label_short(days: int, texts) -> str:
    if days < 365:
        return texts.t('INLINE_GIFT_LABEL_DAYS_SHORT', '{n} дн.').format(n=days)
    if days == 365:
        return texts.t('INLINE_GIFT_LABEL_YEAR', '1 г.')
    if days % 365 == 0:
        return texts.t('INLINE_GIFT_LABEL_YEARS', '{n} г.').format(n=days // 365)
    return texts.t('INLINE_GIFT_LABEL_DAYS_SHORT', '{n} дн.').format(n=days)


def _fmt_traffic(gb: int, texts) -> str:
    if gb == 0:
        return texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит')
    return f'{gb} {texts.t("INLINE_GIFT_GB_SUFFIX", "ГБ")}'


def _build_caption(username: str, days: int, traffic_gb: int, devices: int, texts) -> str:
    """Build the message text sent to the chat (no emojis, clean text)."""
    safe_name = html.escape(username)
    lines = []
    if days:
        lines.append(f'+{_days_label_short(days, texts)}')
    if traffic_gb:
        lines.append(f'{traffic_gb} {texts.t("INLINE_GIFT_GB_SUFFIX", "ГБ")}')
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
        if db_user:
            db_user_found = True
            name_parts = [db_user.first_name or '', db_user.last_name or '']
            full_name = ' '.join(p for p in name_parts if p).strip()
            if full_name:
                recipient_display = f'{full_name} (@{username})'
            sub = await get_subscription_by_user_id(db, db_user.id)
            if sub:
                gb = sub.traffic_limit_gb
                traffic_str = texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит') if gb == 0 else f'{gb} ГБ'
                sub_info_lines.append(f'{sub.days_left} дн.')
                sub_info_lines.append(traffic_str)
                sub_info_lines.append(f'{sub.device_limit} уст.')
            else:
                sub_info_lines.append(texts.t('INLINE_GIFT_NO_SUB', 'нет подписки'))

    thumbnail_url = texts.t('INLINE_GIFT_THUMBNAIL_URL', 'https://i.imgur.com/dPMGC9b.png')

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
    deep_link = f'https://t.me/{bot_username}?start={_GIFT_PREFIX}{gift_code}'

    caption = _build_caption(username, days, traffic_gb, devices, texts)

    parts_summary = []
    if days:
        parts_summary.append(_days_label_short(days, texts))
    if traffic_gb:
        parts_summary.append(f'{traffic_gb} ГБ')
    if devices:
        parts_summary.append(f'{devices} уст.')
    gift_summary = ', '.join(parts_summary)

    # Description shows: what user has now → what they'll get
    if sub_info_lines:
        description = f'{" | ".join(sub_info_lines)} → {gift_summary}'
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
