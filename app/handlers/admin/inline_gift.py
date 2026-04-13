"""Admin inline query handler for gifting subscriptions, discounts and balance.

Syntax reference (all commands unified):

  Subscription gift:
    @botname @user 30              — +30 days
    @botname @user 30 500          — +30 days, 500 GB traffic
    @botname @user 30 500 3        — +30 days, 500 GB, 3 devices
    @botname @user 0 500           — set 500 GB only
    @botname @user 0 0 3           — set 3 devices only
    @botname @user -1              — forever (year 2099)
    @botname @user 30 -1           — 30 days + unlimited traffic

  Random first-come gift (no recipient):
    @botname $1 30                 — gift +30 days to the first user who clicks
    @botname $5 30                 — gift to 5 random first-comers (5 separate codes)

  Discount:
    @botname @user %15             — give 15% discount promocode

  Balance top-up:
    @botname @user :1500           — add 1500 ₽ to balance

Special -1 values:
  -1 for days = forever (~year 2099)
  -1 for traffic_gb = unlimited (0 in DB)
  -1 for devices = 999
"""

import html
import secrets
from dataclasses import dataclass
from typing import Literal

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
_GIFT_PREFIX_NEW_USER = 'rbs_'

_FOREVER_DAYS = (2099 - 2025) * 365
_UNLIMITED_TRAFFIC = 0
_MAX_DEVICES = 999


def _is_admin(telegram_id: int) -> bool:
    return settings.is_admin(telegram_id)


@dataclass
class ParsedQuery:
    gift_type: Literal['subscription', 'discount', 'balance', 'random_subscription']
    username: str          # '' for random mode
    random_count: int      # >0 in random mode
    days: int
    traffic_gb: int
    devices: int
    discount_percent: int  # 1-99
    balance_rub: int       # rubles (converted to kopeks on save)


def _parse_query(query_text: str) -> ParsedQuery:
    """Parse the inline query text into a ParsedQuery.

    Supported patterns:
      @user [days] [traffic] [devices]   → subscription
      $N [days] [traffic] [devices]      → random_subscription (N winners)
      @user %15                          → discount 15%
      @user :1500                        → balance +1500 ₽
      (empty / @user only)               → info-only (all zeros)
    """
    text = query_text.strip()
    parts = text.split()

    def _int(s: str, allow_neg_one: bool = False) -> int:
        try:
            v = int(s)
        except (ValueError, TypeError):
            return 0
        if v == -1 and allow_neg_one:
            return -1
        return max(0, v)

    # Random mode: $N ...
    if parts and parts[0].startswith('$'):
        count_str = parts[0][1:]
        random_count = max(1, _int(count_str) or 1)
        rest = parts[1:]

        days = _int(rest[0], allow_neg_one=True) if len(rest) > 0 else 0
        traffic_gb = _int(rest[1], allow_neg_one=True) if len(rest) > 1 else 0
        devices = _int(rest[2], allow_neg_one=True) if len(rest) > 2 else 0

        if days == -1:
            days = _FOREVER_DAYS
        if devices == -1:
            devices = _MAX_DEVICES

        return ParsedQuery(
            gift_type='random_subscription',
            username='',
            random_count=random_count,
            days=days,
            traffic_gb=traffic_gb,
            devices=devices,
            discount_percent=0,
            balance_rub=0,
        )

    # All other modes require @username as first token
    if not parts:
        return ParsedQuery('subscription', '', 0, 0, 0, 0, 0, 0)

    username = parts[0].lstrip('@')
    rest = parts[1:]

    if not rest:
        return ParsedQuery('subscription', username, 0, 0, 0, 0, 0, 0)

    first = rest[0]

    # Discount mode: %15
    if first.startswith('%'):
        pct = _int(first[1:])
        pct = max(1, min(99, pct))
        return ParsedQuery('discount', username, 0, 0, 0, 0, pct, 0)

    # Balance mode: :1500
    if first.startswith(':'):
        rub = _int(first[1:])
        return ParsedQuery('balance', username, 0, 0, 0, 0, 0, rub)

    # Subscription mode
    days = _int(rest[0], allow_neg_one=True) if len(rest) > 0 else 0
    traffic_gb = _int(rest[1], allow_neg_one=True) if len(rest) > 1 else 0
    devices = _int(rest[2], allow_neg_one=True) if len(rest) > 2 else 0

    if days == -1:
        days = _FOREVER_DAYS
    if devices == -1:
        devices = _MAX_DEVICES

    return ParsedQuery('subscription', username, 0, days, traffic_gb, devices, 0, 0)


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


def _build_subscription_caption(username: str, days: int, traffic_gb: int, devices: int, texts, random_count: int = 0) -> str:
    safe_name = html.escape(username) if username else ''
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

    if random_count > 0:
        header = texts.t('INLINE_GIFT_CAPTION_HEADER_RANDOM', 'Подарочная подписка — первым {n}').format(n=random_count)
        hint = texts.t('INLINE_GIFT_CAPTION_HINT', 'Нажмите кнопку ниже, чтобы активировать.')
        return (
            f'<b>{header}</b>\n\n'
            f'<blockquote>{body}</blockquote>\n\n'
            f'<code>{hint}</code>'
        )

    header = texts.t('INLINE_GIFT_CAPTION_HEADER', 'Подарочная подписка для')
    hint = texts.t('INLINE_GIFT_CAPTION_HINT', 'Нажмите кнопку ниже, чтобы активировать.')
    return (
        f'<b>{header} <code>{safe_name}</code></b>\n\n'
        f'<blockquote>{body}</blockquote>\n\n'
        f'<code>{hint}</code>'
    )


def _build_discount_caption(username: str, pct: int, texts) -> str:
    safe_name = html.escape(username)
    header = texts.t('INLINE_GIFT_DISCOUNT_HEADER', 'Скидка для')
    hint = texts.t('INLINE_GIFT_CAPTION_HINT', 'Нажмите кнопку ниже, чтобы активировать.')
    body = texts.t('INLINE_GIFT_DISCOUNT_BODY', 'Скидка {pct}% на следующую покупку').format(pct=pct)
    return (
        f'<b>{header} <code>{safe_name}</code></b>\n\n'
        f'<blockquote>{body}</blockquote>\n\n'
        f'<code>{hint}</code>'
    )


def _build_balance_caption(username: str, rub: int, texts) -> str:
    safe_name = html.escape(username)
    header = texts.t('INLINE_GIFT_BALANCE_HEADER', 'Пополнение баланса для')
    hint = texts.t('INLINE_GIFT_CAPTION_HINT', 'Нажмите кнопку ниже, чтобы активировать.')
    body = texts.t('INLINE_GIFT_BALANCE_BODY', '+{rub} ₽ на баланс').format(rub=rub)
    return (
        f'<b>{header} <code>{safe_name}</code></b>\n\n'
        f'<blockquote>{body}</blockquote>\n\n'
        f'<code>{hint}</code>'
    )


def _build_syntax_hint(texts) -> list[types.InlineQueryResultArticle]:
    """Return hint results shown when query is empty."""
    thumbnail_url = texts.t(
        'INLINE_GIFT_THUMBNAIL_URL',
        'https://raw.githubusercontent.com/cy7su/cy7su/refs/heads/main/GIFT.png',
    )
    hints = [
        (
            'hint_sub',
            '@user дни [гб [устройств]]',
            'Подписка: @user 30 / @user 30 500 3 / @user -1 (навсегда)',
            '@user 30',
        ),
        (
            'hint_random',
            '$N дни [гб [устройств]]',
            'Первым N пользователям: $1 30 / $5 30 500',
            '$1 30',
        ),
        (
            'hint_discount',
            '@user %процент',
            'Скидка: @user %15 (скидка 15% на покупку)',
            '@user %15',
        ),
        (
            'hint_balance',
            '@user :сумма',
            'Баланс: @user :1500 (добавить 1500 ₽)',
            '@user :1500',
        ),
    ]
    results = []
    for result_id, title, description, _ in hints:
        results.append(
            types.InlineQueryResultArticle(
                id=result_id,
                title=title,
                description=description,
                input_message_content=types.InputTextMessageContent(
                    message_text=title,
                    parse_mode='HTML',
                ),
                thumbnail_url=thumbnail_url,
                thumbnail_width=512,
                thumbnail_height=512,
            )
        )
    return results


async def handle_admin_inline_query(inline_query: types.InlineQuery) -> None:
    if not _is_admin(inline_query.from_user.id):
        await inline_query.answer([], cache_time=5)
        return

    texts = get_texts(DEFAULT_LANGUAGE)
    query_text = (inline_query.query or '').strip()
    parsed = _parse_query(query_text)

    thumbnail_url = texts.t(
        'INLINE_GIFT_THUMBNAIL_URL',
        'https://raw.githubusercontent.com/cy7su/cy7su/refs/heads/main/GIFT.png',
    )

    # Empty query — show syntax hints
    if not query_text:
        hint_results = _build_syntax_hint(texts)
        await inline_query.answer(
            hint_results,
            cache_time=1,
            switch_pm_text=texts.t('INLINE_GIFT_QUERY_HINT', '@user дни | $N дни | @user %скидка | @user :баланс'),
            switch_pm_parameter='help',
        )
        return

    # Random-mode: no lookup, just show gift template
    if parsed.gift_type == 'random_subscription':
        has_params = parsed.days or parsed.traffic_gb or parsed.devices
        if not has_params:
            await inline_query.answer(
                [],
                cache_time=1,
                switch_pm_text=texts.t('INLINE_GIFT_QUERY_HINT', '@user дни | $N дни | @user %скидка | @user :баланс'),
                switch_pm_parameter='help',
            )
            return

        parts_summary = []
        if parsed.days:
            parts_summary.append(_days_label_short(parsed.days, texts))
        if parsed.traffic_gb:
            parts_summary.append(_fmt_traffic(parsed.traffic_gb, texts))
        if parsed.devices:
            parts_summary.append(f'{parsed.devices} уст.')
        gift_summary = ', '.join(parts_summary)

        gift_code = secrets.token_urlsafe(32)
        bot_username = settings.BOT_USERNAME or ''
        deep_link = f'https://t.me/{bot_username}?start={_GIFT_PREFIX_NEW_USER}{gift_code}'
        caption = _build_subscription_caption('', parsed.days, parsed.traffic_gb, parsed.devices, texts, random_count=parsed.random_count)
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(
                text=texts.t('INLINE_GIFT_ACTIVATE_BUTTON', 'Активировать'),
                url=deep_link,
            )
        ]])
        results = [
            types.InlineQueryResultArticle(
                id=gift_code,
                title=texts.t('INLINE_GIFT_RANDOM_TITLE', 'Первым {n} — {gift}').format(n=parsed.random_count, gift=gift_summary),
                description=gift_summary,
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
        return

    # Named-user modes — look up recipient
    username = parsed.username
    if not username:
        await inline_query.answer(
            [],
            cache_time=1,
            switch_pm_text=texts.t('INLINE_GIFT_QUERY_HINT', '@user дни | $N дни | @user %скидка | @user :баланс'),
            switch_pm_parameter='help',
        )
        return

    recipient_display = f'@{username}'
    sub_info_lines: list[str] = []
    db_user_found = False
    sub = None
    cur_days, cur_traffic, cur_devices = 0, 0, 1

    async with AsyncSessionLocal() as db:
        from sqlalchemy import func as sql_func
        result = await db.execute(
            select(User).where(sql_func.lower(User.username) == username.lower())
        )
        db_user = result.scalars().first()
        if db_user:
            db_user_found = True
            full_name = ' '.join(p for p in [db_user.first_name or '', db_user.last_name or ''] if p).strip()
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

    # Info-only: user entered @user without any params
    is_info_only = (
        parsed.gift_type == 'subscription'
        and parsed.days == 0
        and parsed.traffic_gb == 0
        and parsed.devices == 0
    )
    if is_info_only:
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

    # Build gift result depending on type
    gift_code = secrets.token_urlsafe(32)
    bot_username = settings.BOT_USERNAME or ''
    prefix = _GIFT_PREFIX if db_user_found else _GIFT_PREFIX_NEW_USER
    deep_link = f'https://t.me/{bot_username}?start={prefix}{gift_code}'

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text=texts.t('INLINE_GIFT_ACTIVATE_BUTTON', 'Активировать'),
            url=deep_link,
        )
    ]])

    if parsed.gift_type == 'discount':
        pct = parsed.discount_percent
        caption = _build_discount_caption(username, pct, texts)
        description = texts.t('INLINE_GIFT_DISCOUNT_DESC', 'Скидка {pct}%').format(pct=pct)
        title = f'{recipient_display} — скидка {pct}%'
    elif parsed.gift_type == 'balance':
        rub = parsed.balance_rub
        caption = _build_balance_caption(username, rub, texts)
        description = texts.t('INLINE_GIFT_BALANCE_DESC', '+{rub} ₽ на баланс').format(rub=rub)
        title = f'{recipient_display} — +{rub} ₽'
    else:
        # subscription
        parts_summary = []
        if parsed.days:
            parts_summary.append(_days_label_short(parsed.days, texts))
        if parsed.traffic_gb:
            parts_summary.append(_fmt_traffic(parsed.traffic_gb, texts))
        if parsed.devices:
            parts_summary.append(f'{parsed.devices} уст.')
        gift_summary = ', '.join(parts_summary)

        result_parts = []
        if parsed.days:
            result_days = cur_days + parsed.days
            result_parts.append(_days_label_short(result_days, texts))
        if parsed.traffic_gb:
            result_parts.append(_fmt_traffic(parsed.traffic_gb, texts))
        if parsed.devices:
            result_parts.append(f'{max(cur_devices, parsed.devices)} уст.')

        if sub_info_lines and sub:
            description = f'{" | ".join(sub_info_lines)} → {", ".join(result_parts)}' if result_parts else f'{" | ".join(sub_info_lines)} → {gift_summary}'
        else:
            description = gift_summary

        caption = _build_subscription_caption(username, parsed.days, parsed.traffic_gb, parsed.devices, texts)
        title = f'{recipient_display} — {gift_summary}'

    results = [
        types.InlineQueryResultArticle(
            id=gift_code,
            title=title,
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
    if gift_code in ('info_only', 'hint_sub', 'hint_random', 'hint_discount', 'hint_balance'):
        return

    inline_message_id = chosen.inline_message_id
    query_text = chosen.query or ''
    parsed = _parse_query(query_text)

    async with AsyncSessionLocal() as db:
        admin_user = await get_user_by_telegram_id(db, chosen.from_user.id)

        if parsed.gift_type == 'random_subscription':
            # Create N gift records with recipient_telegram_id = 0 (first-come)
            has_params = parsed.days or parsed.traffic_gb or parsed.devices
            if not has_params:
                return
            count = max(1, parsed.random_count)
            # First gift gets the actual inline_message_id; rest get None (no button to update)
            for i in range(count):
                code = gift_code if i == 0 else secrets.token_urlsafe(32)
                gift = InlineGiftSubscription(
                    gift_code=code,
                    recipient_telegram_id=0,
                    sender_user_id=admin_user.id if admin_user else None,
                    gift_type='subscription',
                    days=parsed.days,
                    traffic_limit_gb=parsed.traffic_gb,
                    device_limit=parsed.devices or 1,
                    inline_message_id=inline_message_id if i == 0 else None,
                )
                db.add(gift)
            await db.commit()
            logger.info(
                'Random inline gifts created',
                gift_code=gift_code,
                count=count,
                days=parsed.days,
            )
            return

        username = parsed.username
        if not username:
            return

        from sqlalchemy import func as sql_func
        result = await db.execute(
            select(User).where(sql_func.lower(User.username) == username.lower())
        )
        db_user = result.scalars().first()
        recipient_telegram_id = db_user.telegram_id if db_user else 0

        if parsed.gift_type == 'discount':
            if not parsed.discount_percent:
                return
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=recipient_telegram_id,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type='discount',
                days=0,
                traffic_limit_gb=0,
                device_limit=1,
                discount_percent=parsed.discount_percent,
                inline_message_id=inline_message_id or f'u:{username}',
            )
        elif parsed.gift_type == 'balance':
            if not parsed.balance_rub:
                return
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=recipient_telegram_id,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type='balance',
                days=0,
                traffic_limit_gb=0,
                device_limit=1,
                balance_amount_kopeks=parsed.balance_rub * 100,
                inline_message_id=inline_message_id or f'u:{username}',
            )
        else:
            # subscription
            has_params = parsed.days or parsed.traffic_gb or parsed.devices
            if not has_params:
                logger.warning('chosen_inline_result with empty params', gift_code=gift_code, query=query_text)
                return
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=recipient_telegram_id,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type='subscription',
                days=parsed.days,
                traffic_limit_gb=parsed.traffic_gb,
                device_limit=parsed.devices or 1,
                inline_message_id=inline_message_id or f'u:{username}',
            )

        db.add(gift)
        await db.commit()
        logger.info(
            'Inline gift created',
            gift_code=gift_code,
            username=username,
            gift_type=parsed.gift_type,
        )


def register_handlers(dp: Dispatcher) -> None:
    dp.inline_query.register(handle_admin_inline_query)
    dp.chosen_inline_result.register(handle_chosen_inline_result)
