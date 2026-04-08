"""Admin inline query handler for gifting subscriptions.

Admin uses @botusername <username> [days] in a chat to send a gift result.
The result message contains an "Активировать" button with a deep-link.
When the target user clicks it they land in the bot and get a confirmation screen.

Usage (for admin):
  @botusername johndoe      — shows options for all day presets
  @botusername johndoe 30   — can be used to filter (optional)
"""

import html
import secrets
from datetime import UTC, datetime

import structlog
from aiogram import Dispatcher, types
from sqlalchemy import select

from app.config import settings
from app.database.crud.user import get_user_by_telegram_id
from app.database.database import AsyncSessionLocal
from app.database.models import InlineGiftSubscription, User
from app.localization.loader import DEFAULT_LANGUAGE
from app.localization.texts import get_texts


logger = structlog.get_logger(__name__)

_GIFT_DAY_OPTIONS = [7, 30, 90, 180, 365, 992]


def _is_admin(telegram_id: int) -> bool:
    return settings.is_admin(telegram_id)


def _label_days(days: int, texts) -> str:
    if days < 365:
        return texts.t('INLINE_GIFT_LABEL_DAYS_SHORT', '{n} дн.').format(n=days)
    if days == 365:
        return texts.t('INLINE_GIFT_LABEL_YEAR', '1 г.')
    if days % 365 == 0:
        return texts.t('INLINE_GIFT_LABEL_YEARS', '{n} г.').format(n=days // 365)
    return texts.t('INLINE_GIFT_LABEL_DAYS_SHORT', '{n} дн.').format(n=days)


async def handle_admin_inline_query(inline_query: types.InlineQuery) -> None:
    """Handle inline queries from admin to send gift subscriptions."""
    if not _is_admin(inline_query.from_user.id):
        await inline_query.answer([], cache_time=5)
        return

    texts = get_texts(DEFAULT_LANGUAGE)

    query_text = (inline_query.query or '').strip()
    parts = query_text.split()

    if not parts:
        await inline_query.answer(
            [],
            cache_time=1,
            switch_pm_text=texts.t('INLINE_GIFT_QUERY_ENTER_USERNAME', 'Введите @username'),
            switch_pm_parameter='help',
        )
        return

    username = parts[0].lstrip('@')
    if not username:
        await inline_query.answer([], cache_time=1)
        return

    # Try to find recipient in DB
    recipient_display = f'@{username}'
    recipient_telegram_id: int | None = None

    async with AsyncSessionLocal() as db:
        from sqlalchemy import func as sql_func
        result = await db.execute(
            select(User).where(sql_func.lower(User.username) == username.lower())
        )
        db_user = result.scalars().first()
        if db_user:
            recipient_telegram_id = db_user.telegram_id
            name_parts = [db_user.first_name or '', db_user.last_name or '']
            full_name = ' '.join(p for p in name_parts if p).strip()
            if full_name:
                recipient_display = f'{full_name} (@{username})'

    bot_username = settings.BOT_USERNAME or ''
    thumbnail_url = texts.t('INLINE_GIFT_THUMBNAIL_URL', 'https://i.imgur.com/dPMGC9b.png')
    results: list[types.InlineQueryResult] = []

    for days in _GIFT_DAY_OPTIONS:
        gift_code = secrets.token_urlsafe(32)
        deep_link = f'https://t.me/{bot_username}?start={gift_code}'
        safe_name = html.escape(username)
        caption = texts.t(
            'INLINE_GIFT_CAPTION',
            '🎁 <b>Подарочная подписка для <code>{username}</code> на <code>{days}</code> дней</b>\n\n'
            '<code>Нажмите кнопку ниже, чтобы получить ссылку.</code>',
        ).format(username=safe_name, days=days)

        # Persist gift record before sending inline result
        async with AsyncSessionLocal() as db:
            admin_user = await get_user_by_telegram_id(db, inline_query.from_user.id)
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=recipient_telegram_id or 0,
                sender_user_id=admin_user.id if admin_user else None,
                days=days,
                traffic_limit_gb=settings.DEFAULT_TRAFFIC_LIMIT_GB,
                device_limit=settings.DEFAULT_DEVICE_LIMIT,
                # inline_message_id is used temporarily to store @username
                # It will be overwritten with the real inline_message_id
                # in chosen_inline_result. We prefix with "u:" to distinguish.
                inline_message_id=f'u:{username}',
            )
            db.add(gift)
            await db.commit()

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t('INLINE_GIFT_ACTIVATE_BUTTON', 'Активировать'),
                        url=deep_link,
                    )
                ]
            ]
        )

        label_days = _label_days(days, texts)

        results.append(
            types.InlineQueryResultArticle(
                id=gift_code,
                title=texts.t('INLINE_GIFT_ITEM_TITLE', 'Подарок {label_days} — {recipient}').format(
                    label_days=label_days, recipient=recipient_display
                ),
                description=texts.t('INLINE_GIFT_ITEM_DESCRIPTION', 'Подарочная подписка на {days} дней для @{username}').format(
                    days=days, username=username
                ),
                input_message_content=types.InputTextMessageContent(
                    message_text=caption,
                    parse_mode='HTML',
                ),
                reply_markup=keyboard,
                thumbnail_url=thumbnail_url,
                thumbnail_width=512,
                thumbnail_height=512,
            )
        )

    await inline_query.answer(results, cache_time=1, is_personal=True)


async def handle_chosen_inline_result(chosen: types.ChosenInlineResult) -> None:
    """Called when admin picks a result — update inline_message_id."""
    gift_code = chosen.result_id
    inline_message_id = chosen.inline_message_id  # real inline message id from Telegram

    if not inline_message_id:
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(InlineGiftSubscription).where(
                InlineGiftSubscription.gift_code == gift_code
            )
        )
        gift = result.scalars().first()
        if gift:
            gift.inline_message_id = inline_message_id
            await db.commit()
            logger.info(
                'Inline gift chosen, inline_message_id saved',
                gift_code=gift_code,
                inline_message_id=inline_message_id,
            )


def register_handlers(dp: Dispatcher) -> None:
    dp.inline_query.register(handle_admin_inline_query)
    dp.chosen_inline_result.register(handle_chosen_inline_result)
