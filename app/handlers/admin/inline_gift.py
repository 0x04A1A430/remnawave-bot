"""Admin inline query handler for gifting subscriptions, discounts and balance.

Syntax (flags replace old symbols):

  Subscription gift (to specific user by @username or numeric TG ID):
    @botname @user 30              — +30 days, defaults for traffic/devices
    @botname 123456789 30          — same but by Telegram ID
    @botname @user -p 30 500 3     — explicit flag: 30 days, 500 GB, 3 devices
    @botname @user -p -1           — forever

  Multi-activation gift (first N users):
    @botname -r 5 30               — 5 activations, 30 days each
    @botname -r 5 30 500 3         — 5 activations, 30 days, 500 GB, 3 devices

  Discount:
    @botname @user -d 15           — 15% discount promocode
    @botname 123456789 -d 15       — same by ID

  Balance top-up:
    @botname @user -b 1500         — add 1500 ₽
    @botname 123456789 -b 1500     — same by ID

  Extra squad (-bc adds recipient to squad 050365af-1377-469c-b625-4e88d3e0e3ae):
    @botname @user 30 -bc
    @botname @user -p 30 500 -bc

Defaults when values omitted: days=7, traffic_gb=300, devices=2
Special values: -1 for days = forever; -1 for traffic = unlimited; -1 for devices = 999
"""

import html
import secrets
from dataclasses import dataclass, field
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
_EXTRA_SQUAD_UUID = '050365af-1377-469c-b625-4e88d3e0e3ae'

_FOREVER_DAYS = (2099 - 2025) * 365
_MAX_DEVICES = 999

# Defaults applied when a value is omitted
_DEFAULT_DAYS = 7
_DEFAULT_TRAFFIC = 300
_DEFAULT_DEVICES = 2


def _is_admin(telegram_id: int) -> bool:
    return settings.is_admin(telegram_id)


@dataclass
class ParsedQuery:
    gift_type: Literal['subscription', 'discount', 'balance', 'multi']
    # Target: either username (str) or telegram_id (int) or 0 for multi
    username: str
    target_id: int          # >0 when user entered numeric ID directly
    multi_count: int        # >0 for -r mode
    days: int
    traffic_gb: int
    devices: int
    discount_percent: int
    balance_rub: int
    add_extra_squad: bool = field(default=False)


def _int(s: str, allow_neg_one: bool = False) -> int:
    try:
        v = int(s)
    except (ValueError, TypeError):
        return 0
    if v == -1 and allow_neg_one:
        return -1
    return max(0, v)


def _parse_query(query_text: str) -> ParsedQuery:
    """Parse inline query into ParsedQuery.

    Token order:
      [target] [-flag [args...]] [-bc]

    target = @username | numeric_id (absent for -r)
    flags  = -p | -d | -b | -r | -bc
    """
    text = query_text.strip()
    tokens = text.split()

    add_bc = '-bc' in tokens
    tokens = [t for t in tokens if t != '-bc']

    if not tokens:
        return ParsedQuery('subscription', '', 0, 0, 0, 0, 0, 0, 0, add_bc)

    # -r N [days [traffic [devices]]]  — multi-activation, no specific target
    if tokens[0] == '-r':
        rest = tokens[1:]
        count = max(1, _int(rest[0]) or 1) if rest else 1
        rest = rest[1:]
        days = _resolve_days(_int(rest[0], True) if rest else 0)
        traffic = _resolve_traffic(_int(rest[1], True) if len(rest) > 1 else 0)
        devices = _resolve_devices(_int(rest[2], True) if len(rest) > 2 else 0)
        return ParsedQuery('multi', '', 0, count, days, traffic, devices, 0, 0, add_bc)

    # Extract target token
    first = tokens[0]
    if first.startswith('@'):
        username = first.lstrip('@')
        target_id = 0
        rest = tokens[1:]
    elif first.lstrip('-').isdigit() and not first.startswith('-'):
        target_id = int(first)
        username = ''
        rest = tokens[1:]
    else:
        # No recognisable target yet — show info/hints
        return ParsedQuery('subscription', '', 0, 0, 0, 0, 0, 0, 0, add_bc)

    if not rest:
        return ParsedQuery('subscription', username, target_id, 0, 0, 0, 0, 0, 0, add_bc)

    flag = rest[0]
    args = rest[1:]

    # -d percent
    if flag == '-d':
        pct = max(1, min(99, _int(args[0]) if args else 0))
        return ParsedQuery('discount', username, target_id, 0, 0, 0, 0, pct, 0, add_bc)

    # -b rubles
    if flag == '-b':
        rub = _int(args[0]) if args else 0
        return ParsedQuery('balance', username, target_id, 0, 0, 0, 0, 0, rub, add_bc)

    # -p [days [traffic [devices]]]  — explicit subscription flag
    if flag == '-p':
        days = _resolve_days(_int(args[0], True) if args else 0)
        traffic = _resolve_traffic(_int(args[1], True) if len(args) > 1 else 0)
        devices = _resolve_devices(_int(args[2], True) if len(args) > 2 else 0)
        return ParsedQuery('subscription', username, target_id, 0, days, traffic, devices, 0, 0, add_bc)

    # No flag: plain numbers after target = subscription
    days = _resolve_days(_int(rest[0], True) if rest else 0)
    traffic = _resolve_traffic(_int(rest[1], True) if len(rest) > 1 else 0)
    devices = _resolve_devices(_int(rest[2], True) if len(rest) > 2 else 0)
    return ParsedQuery('subscription', username, target_id, 0, days, traffic, devices, 0, 0, add_bc)


def _resolve_days(v: int) -> int:
    if v == -1:
        return _FOREVER_DAYS
    return v if v else _DEFAULT_DAYS


def _resolve_traffic(v: int) -> int:
    if v == -1:
        return -1  # sentinel: unlimited
    return v if v else _DEFAULT_TRAFFIC


def _resolve_devices(v: int) -> int:
    if v == -1:
        return _MAX_DEVICES
    return v if v else _DEFAULT_DEVICES


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
    if gb <= 0 or gb == -1:
        return texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит')
    return f'{gb} {texts.t("INLINE_GIFT_GB_SUFFIX", "ГБ")}'


def _gift_summary(days: int, traffic_gb: int, devices: int, texts) -> str:
    parts = []
    parts.append(_days_label_short(days, texts))
    parts.append(_fmt_traffic(traffic_gb, texts))
    parts.append(f'{devices} {texts.t("INLINE_GIFT_DEVICES_SUFFIX", "уст.")}')
    return ', '.join(parts)


def _build_subscription_caption(display: str, days: int, traffic_gb: int, devices: int, texts, multi_count: int = 0, bc: bool = False) -> str:
    safe = html.escape(display) if display else ''
    lines = [
        _days_label_short(days, texts),
        _fmt_traffic(traffic_gb, texts),
        f'{devices} {texts.t("INLINE_GIFT_DEVICES_SUFFIX", "уст.")}',
    ]
    if bc:
        lines.append('+ доп. сервер')
    body = '\n'.join(lines)
    hint = texts.t('INLINE_GIFT_CAPTION_HINT', 'Нажмите кнопку ниже, чтобы активировать.')

    if multi_count > 0:
        header = texts.t('INLINE_GIFT_CAPTION_HEADER_RANDOM', 'Подарочная подписка — первым {n}').format(n=multi_count)
        return f'<b>{header}</b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>'

    header = texts.t('INLINE_GIFT_CAPTION_HEADER', 'Подарочная подписка для')
    return f'<b>{header} <code>{safe}</code></b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>'


def _build_discount_caption(display: str, pct: int, texts) -> str:
    safe = html.escape(display)
    header = texts.t('INLINE_GIFT_DISCOUNT_HEADER', 'Скидка для')
    hint = texts.t('INLINE_GIFT_CAPTION_HINT', 'Нажмите кнопку ниже, чтобы активировать.')
    body = texts.t('INLINE_GIFT_DISCOUNT_BODY', 'Скидка {pct}% на следующую покупку').format(pct=pct)
    return f'<b>{header} <code>{safe}</code></b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>'


def _build_balance_caption(display: str, rub: int, texts) -> str:
    safe = html.escape(display)
    header = texts.t('INLINE_GIFT_BALANCE_HEADER', 'Пополнение баланса для')
    hint = texts.t('INLINE_GIFT_CAPTION_HINT', 'Нажмите кнопку ниже, чтобы активировать.')
    body = texts.t('INLINE_GIFT_BALANCE_BODY', '+{rub} ₽ на баланс').format(rub=rub)
    return f'<b>{header} <code>{safe}</code></b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>'


def _build_syntax_hint(texts) -> list[types.InlineQueryResultArticle]:
    thumb = texts.t(
        'INLINE_GIFT_THUMBNAIL_URL',
        'https://raw.githubusercontent.com/cy7su/cy7su/refs/heads/main/GIFT.png',
    )
    hints = [
        ('hint_sub',    '@user [дни]',       'Подписка: @user 30  /  123456789 30  /  @user -p 30 500 3', '@user 30'),
        ('hint_multi',  '-r N [дни]',        'Первым N: -r 5 30  /  -r 5 30 500 3', '-r 5 30'),
        ('hint_disc',   '@user -d процент',  'Скидка: @user -d 15', '@user -d 15'),
        ('hint_bal',    '@user -b сумма',    'Баланс: @user -b 1500', '@user -b 1500'),
        ('hint_bc',     '... -bc',           'Добавить в доп. сквад: @user 30 -bc', '@user 30 -bc'),
    ]
    results = []
    for rid, title, desc, _ in hints:
        results.append(types.InlineQueryResultArticle(
            id=rid,
            title=title,
            description=desc,
            input_message_content=types.InputTextMessageContent(message_text=title, parse_mode='HTML'),
            thumbnail_url=thumb,
            thumbnail_width=512,
            thumbnail_height=512,
        ))
    return results


def _flag_hint(query_text: str, texts) -> str | None:
    """Return switch_pm_text hint relevant to what admin is currently typing."""
    t = query_text.strip()
    if '-r' in t:
        return '-r N дни [гб [уст.]]  — первым N'
    if '-d' in t:
        return '-d процент  — скидка %'
    if '-b' in t:
        return '-b сумма  — пополнить баланс'
    if '-bc' in t:
        return '-bc  — добавить в доп. сервер'
    if '-p' in t:
        return '-p дни [гб [уст.]]  — подписка'
    return texts.t('INLINE_GIFT_QUERY_HINT', '@user дни | -r N дни | @user -d % | @user -b ₽')


async def handle_admin_inline_query(inline_query: types.InlineQuery) -> None:
    if not _is_admin(inline_query.from_user.id):
        await inline_query.answer([], cache_time=5)
        return

    texts = get_texts(DEFAULT_LANGUAGE)
    query_text = (inline_query.query or '').strip()
    parsed = _parse_query(query_text)

    thumb = texts.t(
        'INLINE_GIFT_THUMBNAIL_URL',
        'https://raw.githubusercontent.com/cy7su/cy7su/refs/heads/main/GIFT.png',
    )

    hint_text = _flag_hint(query_text, texts)
    hint_kwargs = dict(
        cache_time=1,
        switch_pm_text=hint_text,
        switch_pm_parameter='help',
    )

    if not query_text:
        await inline_query.answer(_build_syntax_hint(texts), **hint_kwargs)
        return

    # Multi-activation mode (-r N ...)
    if parsed.gift_type == 'multi':
        summary = _gift_summary(parsed.days, parsed.traffic_gb, parsed.devices, texts)
        gift_code = secrets.token_urlsafe(32)
        bot_username = settings.BOT_USERNAME or ''
        deep_link = f'https://t.me/{bot_username}?start={_GIFT_PREFIX}{gift_code}'
        caption = _build_subscription_caption('', parsed.days, parsed.traffic_gb, parsed.devices, texts, multi_count=parsed.multi_count, bc=parsed.add_extra_squad)
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(
                text=texts.t('INLINE_GIFT_ACTIVATE_BUTTON_N', 'Активировать (осталось: {n})').format(n=parsed.multi_count),
                url=deep_link,
            )
        ]])
        results = [types.InlineQueryResultArticle(
            id=gift_code,
            title=texts.t('INLINE_GIFT_RANDOM_TITLE', 'Первым {n} — {gift}').format(n=parsed.multi_count, gift=summary),
            description=summary,
            thumbnail_url=thumb,
            thumbnail_width=512,
            thumbnail_height=512,
            input_message_content=types.InputTextMessageContent(
                message_text=caption,
                parse_mode='HTML',
                link_preview_options=types.LinkPreviewOptions(show_above_text=True, url=thumb),
            ),
            reply_markup=keyboard,
        )]
        await inline_query.answer(results, cache_time=0, is_personal=True)
        return

    # Named target: lookup by username or telegram_id
    username = parsed.username
    target_id = parsed.target_id

    if not username and not target_id:
        await inline_query.answer([], **hint_kwargs)
        return

    recipient_display = f'@{username}' if username else str(target_id)
    sub_info_lines: list[str] = []
    db_user_found = False
    sub = None
    cur_days, cur_traffic, cur_devices = 0, 0, 1

    async with AsyncSessionLocal() as db:
        from sqlalchemy import func as sql_func

        if target_id:
            result = await db.execute(select(User).where(User.telegram_id == target_id))
        else:
            result = await db.execute(
                select(User).where(sql_func.lower(User.username) == username.lower())
            )
        db_user = result.scalars().first()

        if db_user:
            db_user_found = True
            if db_user.username:
                full = ' '.join(p for p in [db_user.first_name or '', db_user.last_name or ''] if p).strip()
                recipient_display = f'{full} (@{db_user.username})' if full else f'@{db_user.username}'
            else:
                full = ' '.join(p for p in [db_user.first_name or '', db_user.last_name or ''] if p).strip()
                recipient_display = f'{full} (id:{db_user.telegram_id})' if full else f'id:{db_user.telegram_id}'

            sub = await get_subscription_by_user_id(db, db_user.id)
            if sub:
                cur_days = max(0, sub.days_left) if hasattr(sub, 'days_left') else 0
                cur_traffic = sub.traffic_limit_gb or 0
                cur_devices = sub.device_limit or 1
                gb = sub.traffic_limit_gb
                traffic_str = texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит') if gb == 0 else f'{gb} ГБ'
                sub_info_lines = [f'{sub.days_left} дн.', traffic_str, f'{sub.device_limit} уст.']
            else:
                sub_info_lines = [texts.t('INLINE_GIFT_NO_SUB', 'нет подписки')]

    # Info-only: user typed target but no params / no flag yet
    is_info_only = (
        parsed.gift_type == 'subscription'
        and parsed.days == 0
        and parsed.traffic_gb == 0
        and parsed.devices == 0
        and not parsed.discount_percent
        and not parsed.balance_rub
    )
    if is_info_only:
        current_info = ' | '.join(sub_info_lines) if sub_info_lines else '—'
        safe_target = html.escape(username) if username else str(target_id)
        results = [types.InlineQueryResultArticle(
            id='info_only',
            title=recipient_display,
            description=current_info,
            input_message_content=types.InputTextMessageContent(
                message_text=f'<code>{safe_target}</code>',
                parse_mode='HTML',
            ),
            thumbnail_url=thumb,
            thumbnail_width=512,
            thumbnail_height=512,
        )]
        await inline_query.answer(results, cache_time=0, is_personal=True)
        return

    gift_code = secrets.token_urlsafe(32)
    bot_username = settings.BOT_USERNAME or ''
    deep_link = f'https://t.me/{bot_username}?start={_GIFT_PREFIX}{gift_code}'
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text=texts.t('INLINE_GIFT_ACTIVATE_BUTTON', 'Активировать'),
            url=deep_link,
        )
    ]])

    if parsed.gift_type == 'discount':
        pct = parsed.discount_percent
        caption = _build_discount_caption(recipient_display, pct, texts)
        description = texts.t('INLINE_GIFT_DISCOUNT_DESC', 'Скидка {pct}%').format(pct=pct)
        title = f'{recipient_display} — скидка {pct}%'

    elif parsed.gift_type == 'balance':
        rub = parsed.balance_rub
        caption = _build_balance_caption(recipient_display, rub, texts)
        description = texts.t('INLINE_GIFT_BALANCE_DESC', '+{rub} ₽ на баланс').format(rub=rub)
        title = f'{recipient_display} — +{rub} ₽'

    else:
        summary = _gift_summary(parsed.days, parsed.traffic_gb, parsed.devices, texts)
        if parsed.add_extra_squad:
            summary += ' +сервер'

        if sub_info_lines and sub:
            result_days = cur_days + parsed.days
            result_parts = [
                _days_label_short(result_days, texts),
                _fmt_traffic(parsed.traffic_gb, texts),
                f'{max(cur_devices, parsed.devices)} уст.',
            ]
            description = f'{" | ".join(sub_info_lines)} → {", ".join(result_parts)}'
        else:
            description = summary

        caption = _build_subscription_caption(recipient_display, parsed.days, parsed.traffic_gb, parsed.devices, texts, bc=parsed.add_extra_squad)
        title = f'{recipient_display} — {summary}'

    results = [types.InlineQueryResultArticle(
        id=gift_code,
        title=title,
        description=description,
        thumbnail_url=thumb,
        thumbnail_width=512,
        thumbnail_height=512,
        input_message_content=types.InputTextMessageContent(
            message_text=caption,
            parse_mode='HTML',
            link_preview_options=types.LinkPreviewOptions(show_above_text=True, url=thumb),
        ),
        reply_markup=keyboard,
    )]
    await inline_query.answer(results, cache_time=0, is_personal=True)


async def handle_chosen_inline_result(chosen: types.ChosenInlineResult) -> None:
    if not _is_admin(chosen.from_user.id):
        return

    gift_code = chosen.result_id
    if gift_code in ('info_only', 'hint_sub', 'hint_multi', 'hint_disc', 'hint_bal', 'hint_bc'):
        return

    inline_message_id = chosen.inline_message_id
    query_text = chosen.query or ''
    parsed = _parse_query(query_text)

    async with AsyncSessionLocal() as db:
        admin_user = await get_user_by_telegram_id(db, chosen.from_user.id)
        from sqlalchemy import func as sql_func

        if parsed.gift_type == 'multi':
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=0,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type='subscription',
                days=parsed.days,
                traffic_limit_gb=parsed.traffic_gb,
                device_limit=parsed.devices or _DEFAULT_DEVICES,
                max_activations=parsed.multi_count,
                activated_count=0,
                add_extra_squad=parsed.add_extra_squad,
                inline_message_id=inline_message_id,
            )
            db.add(gift)
            await db.commit()
            logger.info('Multi-activation gift created', gift_code=gift_code, count=parsed.multi_count)
            return

        # Resolve recipient
        if parsed.target_id:
            result = await db.execute(select(User).where(User.telegram_id == parsed.target_id))
        elif parsed.username:
            result = await db.execute(
                select(User).where(sql_func.lower(User.username) == parsed.username.lower())
            )
        else:
            return

        db_user = result.scalars().first()
        recipient_telegram_id = db_user.telegram_id if db_user else (parsed.target_id or 0)

        # intended_target stored for unknown users (checked at activation)
        if not db_user:
            if parsed.target_id:
                intended_sentinel = f'tid:{parsed.target_id}'
            else:
                intended_sentinel = f'u:{parsed.username}'
        else:
            intended_sentinel = None

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
                add_extra_squad=False,
                inline_message_id=inline_message_id or intended_sentinel,
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
                add_extra_squad=False,
                inline_message_id=inline_message_id or intended_sentinel,
            )
        else:
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=recipient_telegram_id,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type='subscription',
                days=parsed.days,
                traffic_limit_gb=parsed.traffic_gb,
                device_limit=parsed.devices or _DEFAULT_DEVICES,
                add_extra_squad=parsed.add_extra_squad,
                inline_message_id=inline_message_id or intended_sentinel,
            )

        db.add(gift)
        await db.commit()
        logger.info(
            'Inline gift created',
            gift_code=gift_code,
            recipient_telegram_id=recipient_telegram_id,
            gift_type=parsed.gift_type,
        )


def register_handlers(dp: Dispatcher) -> None:
    dp.inline_query.register(handle_admin_inline_query)
    dp.chosen_inline_result.register(handle_chosen_inline_result)
