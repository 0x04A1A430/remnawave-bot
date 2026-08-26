"""Admin inline query handler for gifting subscriptions, discounts, balance and temp traffic.

Syntax (flags are mixable in any order; each applies its component):

  Subscription (to specific user by @username or numeric TG ID):
    @botname @user 30              — +30 days only, traffic/devices не меняются
    @botname 123456789 30          — то же по ID
    @botname @user 30 500 3        — 30 дней, 500 ГБ, 3 устройства
    @botname @user 30 - 3          — 30 дней, трафик не меняем, 3 устройства
    @botname @user - - 3           — только устройства
    @botname @user -p 30 500 3     — то же с явным флагом -p
    @botname @user -p -1           — навсегда

  Temp traffic (временный трафик, не меняет постоянный лимит):
    @botname @user -t 100          — +100 ГБ временного трафика (30 дней)
    @botname @user -t 100 60       — +100 ГБ временного трафика на 60 дней

  Discount:
    @botname @user -d 15           — скидка 15%

  Balance:
    @botname @user -b 1500         — +1500 ₽

  Reset traffic usage:
    @botname @user -g              — сброс использованного трафика

  Mixed (одним подарком, флаги в любом порядке):
    @botname @user -p 30 500 3 -t 100 60 -b 1500 -d 15
    @botname @user -b 1500 -t 100

  Multi-activation (первым N, standalone — не миксируется):
    @botname -r 5 30               — 5 активаций, 30 дней
    @botname -r 5 30 500 3         — 5 активаций, 30 дней, 500 ГБ, 3 уст.

Special values: -1 for days = forever; -1 for traffic = unlimited; -1 for devices = 999
Placeholder: - (dash) = пропустить позицию, не менять значение
"""

import html
import re
import secrets
from dataclasses import dataclass

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

_FOREVER_DAYS = (2099 - 2025) * 365
_MAX_DEVICES = 999

# Mixable flag tokens. A single '-' is the "skip position" sentinel, not a flag.
# `-r` doubles as the standalone multi-activation prefix ("-r N days..."); as a
# mixable flag it means "reset traffic usage". Both coexist: multi requires a
# trailing numeric count and a bare "@user -r" target, so they never collide.
# Mixable flag tokens. A single '-' is the "skip position" sentinel, not a flag.
# `-g` = reset traffic usage. `-r` is reserved for the standalone multi-activation
# prefix ("-r N days...") and is NOT mixable.
_FLAG_TOKENS = {'-d', '-b', '-t', '-p', '-g'}


def _is_admin(telegram_id: int) -> bool:
    return settings.is_admin(telegram_id)


@dataclass
class ParsedQuery:
    username: str
    target_id: int = 0  # >0 when user entered numeric ID
    multi_count: int = 0  # >0 for -r mode (standalone multi-activation)
    # Subscription component
    days: int | None = None  # None = no change
    traffic_gb: int | None = None  # None = no change; -1 = unlimited; 0 = unlimited in DB
    devices: int | None = None  # None = no change
    # Discount component
    discount_percent: int = 0  # >0 = present
    # Balance component (rubles)
    balance_rub: int = 0  # >0 = present
    # Temp-traffic component
    temp_traffic_gb: int = 0  # !=0 = present
    temp_traffic_days: int = 0  # >0 = present; 0 = default 30
    # Reset-traffic component
    reset_traffic: bool = False  # True = reset usage

    @property
    def is_multi(self) -> bool:
        return self.multi_count > 0

    @property
    def has_subscription(self) -> bool:
        return self.days is not None or self.traffic_gb is not None or self.devices is not None

    @property
    def has_discount(self) -> bool:
        return self.discount_percent > 0

    @property
    def has_balance(self) -> bool:
        return self.balance_rub > 0

    @property
    def has_temp(self) -> bool:
        return self.temp_traffic_gb != 0

    @property
    def has_reset(self) -> bool:
        return self.reset_traffic

    @property
    def has_any(self) -> bool:
        return self.has_subscription or self.has_discount or self.has_balance or self.has_temp or self.has_reset

    @property
    def is_combo(self) -> bool:
        return (
            sum(
                [
                    self.has_subscription,
                    self.has_discount,
                    self.has_balance,
                    self.has_temp,
                    self.has_reset,
                ]
            )
            > 1
        )


def _parse_val(s: str, allow_neg_one: bool = False) -> int | None:
    """Parse a token: '-' → None (skip), '-1' → -1 (special), else int ≥ 0."""
    if s == '-':
        return None
    try:
        v = int(s)
    except (ValueError, TypeError):
        return None
    if v == -1 and allow_neg_one:
        return -1
    return max(0, v)


def _resolve_days(v: int | None) -> int | None:
    if v is None:
        return None
    if v == -1:
        return _FOREVER_DAYS
    return v if v > 0 else None


def _resolve_traffic(v: int | None) -> int | None:
    if v is None:
        return None
    if v == -1:
        return -1  # sentinel: unlimited
    return v if v > 0 else None


def _resolve_devices(v: int | None) -> int | None:
    if v is None:
        return None
    if v == -1:
        return _MAX_DEVICES
    return v if v > 0 else None


def _parse_sub_args(args: list[str]) -> tuple[int | None, int | None, int | None]:
    """Parse [days [traffic [devices]]] with '-' as skip."""
    days = _resolve_days(_parse_val(args[0], True) if len(args) > 0 else None)
    traffic = _resolve_traffic(_parse_val(args[1], True) if len(args) > 1 else None)
    devices = _resolve_devices(_parse_val(args[2], True) if len(args) > 2 else None)
    return days, traffic, devices


def _parse_query(query_text: str) -> ParsedQuery:
    text = query_text.strip()
    tokens = text.split()

    if not tokens:
        return ParsedQuery('', 0)

    # -r N [days [traffic [devices]]] — standalone multi-activation
    if tokens[0] == '-r':
        rest = tokens[1:]
        count = max(1, int(rest[0]) if rest and rest[0].isdigit() else 1)
        days, traffic, devices = _parse_sub_args(rest[1:] if len(rest) > 1 else [])
        return ParsedQuery('', 0, multi_count=count, days=days, traffic_gb=traffic, devices=devices)

    # Extract target
    first = tokens[0]
    username = ''
    target_id = 0
    if first.startswith('@'):
        username = first.lstrip('@')
    elif first.lstrip('-').isdigit() and not first.startswith('-'):
        target_id = int(first)
    else:
        return ParsedQuery('', 0)

    rest = tokens[1:]

    if not rest:
        return ParsedQuery(username, target_id)

    # If any mixable flag is present → parse by flags; otherwise legacy positional
    # subscription args (@user [days [traffic [devices]]]) with '-' as skip.
    if not (set(rest) & _FLAG_TOKENS):
        days, traffic, devices = _parse_sub_args(rest)
        return ParsedQuery(username, target_id, days=days, traffic_gb=traffic, devices=devices)

    parsed = ParsedQuery(username, target_id)

    i = 0
    while i < len(rest):
        tok = rest[i]

        if tok == '-d':
            if i + 1 < len(rest) and rest[i + 1].isdigit():
                parsed.discount_percent = max(1, min(99, int(rest[i + 1])))
                i += 2
            else:
                i += 1
            continue

        if tok == '-b':
            if i + 1 < len(rest) and rest[i + 1].isdigit():
                parsed.balance_rub = int(rest[i + 1])
                i += 2
            else:
                i += 1
            continue

        if tok == '-t':
            gb = 0
            if i + 1 < len(rest) and rest[i + 1].lstrip('-').isdigit():
                gb = int(rest[i + 1])
                i += 2
            else:
                i += 1
            # Optional trailing day count (positive number, not a flag)
            if i < len(rest) and rest[i].lstrip('-').isdigit() and not rest[i].startswith('-'):
                parsed.temp_traffic_days = max(1, int(rest[i]))
                i += 1
            parsed.temp_traffic_gb = max(-999, min(999, gb))
            continue

        if tok == '-p':
            args = []
            j = i + 1
            while j < len(rest) and rest[j] not in _FLAG_TOKENS:
                args.append(rest[j])
                j += 1
            days, traffic, devices = _parse_sub_args(args)
            parsed.days, parsed.traffic_gb, parsed.devices = days, traffic, devices
            i = j
            continue

        if tok == '-g':
            parsed.reset_traffic = True
            i += 1
            continue

        # Stray/unrecognized token: ignore and move on
        i += 1

    return parsed


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


def _gift_summary(
    days: int | None,
    traffic_gb: int | None,
    devices: int | None,
    texts,
) -> str:
    """Build short summary showing only specified values."""
    parts = []
    if days is not None:
        parts.append(_days_label_short(days, texts))
    if traffic_gb is not None:
        parts.append(_fmt_traffic(traffic_gb, texts))
    if devices is not None:
        parts.append(f'{devices} {texts.t("INLINE_GIFT_DEVICES_SUFFIX", "уст.")}')
    return ', '.join(parts) if parts else '—'


def _subscription_body_lines(
    days: int | None,
    traffic_gb: int | None,
    devices: int | None,
    texts,
) -> list[str]:
    lines = []
    if days is not None:
        lines.append(_days_label_short(days, texts))
    if traffic_gb is not None:
        lines.append(_fmt_traffic(traffic_gb, texts))
    if devices is not None:
        lines.append(f'{devices} {texts.t("INLINE_GIFT_DEVICES_SUFFIX", "уст.")}')
    return lines


def _recipient_html(display: str) -> str:
    """Оборачивает в моноспейс только идентификатор получателя.

    «clayx. (@clayxk)» → clayx. (<code>@clayxk</code>) — ник удобно копировать.
    «@clayxk» / «id:123» (без имени) → целиком в <code>.
    """
    safe = html.escape(display) if display else ''
    m = re.search(r'\((@[^\s)]+)\)$', safe) or re.search(r'\((id:\d+)\)$', safe)
    if m:
        return f'{safe[: m.start(1)]}<code>{m.group(1)}</code>{safe[m.end(1) :]}'
    return f'<code>{safe}</code>'


def _build_subscription_caption(
    display: str,
    days: int | None,
    traffic_gb: int | None,
    devices: int | None,
    texts,
    multi_count: int = 0,
) -> str:
    lines = _subscription_body_lines(days, traffic_gb, devices, texts)
    body = '\n'.join(lines) if lines else '—'
    hint = texts.t('INLINE_GIFT_CAPTION_HINT', 'Нажмите кнопку ниже, чтобы активировать.')

    if multi_count > 0:
        header = texts.t('INLINE_GIFT_CAPTION_HEADER_RANDOM', 'Подарочная подписка — первым {n}').format(n=multi_count)
        return f'<b>{header}</b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>'

    header = texts.t('INLINE_GIFT_CAPTION_HEADER', 'Подарочная подписка для')
    return f'<b>{header} {_recipient_html(display)}</b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>'


def _temp_body_line(gb: int, days: int, texts) -> str:
    sign = '+' if gb >= 0 else ''
    return texts.t(
        'INLINE_GIFT_TEMP_TRAFFIC_BODY',
        '{sign}{gb} ГБ трафика (на {days} дн.)',
    ).format(sign=sign, gb=gb, days=days)


def _build_combo_caption(
    display: str,
    parsed: ParsedQuery,
    texts,
) -> str:
    """Full gift caption: header + all component lines."""
    lines: list[str] = []

    if parsed.has_subscription:
        lines.extend(_subscription_body_lines(parsed.days, parsed.traffic_gb, parsed.devices, texts))
    if parsed.has_discount:
        lines.append(
            texts.t('INLINE_GIFT_DISCOUNT_BODY', 'Скидка {pct}% на следующую покупку').format(
                pct=parsed.discount_percent
            )
        )
    if parsed.has_balance:
        lines.append(texts.t('INLINE_GIFT_BALANCE_BODY', '+{rub} ₽ на баланс').format(rub=parsed.balance_rub))
    if parsed.has_temp:
        lines.append(_temp_body_line(parsed.temp_traffic_gb, parsed.temp_traffic_days or 30, texts))
    if parsed.has_reset:
        lines.append(texts.t('INLINE_GIFT_RESET_BODY', 'Сброс использованного трафика'))

    body = '\n'.join(lines) if lines else '—'
    hint = texts.t('INLINE_GIFT_CAPTION_HINT', 'Нажмите кнопку ниже, чтобы активировать.')
    header = texts.t('INLINE_GIFT_CAPTION_HEADER', 'Подарочная подписка для')
    return f'<b>{header} {_recipient_html(display)}</b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>'


def _build_syntax_hint(texts) -> list[types.InlineQueryResultArticle]:
    thumb = texts.t(
        'INLINE_GIFT_THUMBNAIL_URL',
        'https://raw.githubusercontent.com/0x04A1A430/storage/refs/heads/main/bot/GIFT.png',
    )
    error_text = texts.t(
        'INLINE_GIFT_HINT_TAP_ERROR',
        'Ошибка: незаполненное поле',
    )
    hints = [
        ('hint_sub', '@user -p 30 500 3', 'Подписка: дни [гб [уст.]]  (или просто @user 30)'),
        ('hint_multi', '-r 5 30 500 3', 'Первым N: -r N дни [гб [уст.]]'),
        ('hint_disc', '@user -d 15', 'Скидка 15%'),
        ('hint_bal', '@user -b 1500', 'Пополнить баланс на 1500 ₽'),
        ('hint_t', '@user -t 100 60', 'Временный трафик: 100 ГБ на 60 дней (без дней — 30)'),
        ('hint_g', '@user -g', 'Сброс использованного трафика'),
        ('hint_mix', '@user -p 1 500 3 -t 100 -b 1500 -d 15', 'Микс флагов одним подарком'),
    ]
    results = []
    for rid, chat_text, desc in hints:
        # InlineQueryResultArticle requires message content on tap (Telegram
        # rejects content-less articles), so a hint tap returns a short error
        # note instead of echoing the flag template into the chat.
        results.append(
            types.InlineQueryResultArticle(
                id=rid,
                title=chat_text,
                description=desc,
                thumbnail_url=thumb,
                thumbnail_width=512,
                thumbnail_height=512,
                input_message_content=types.InputTextMessageContent(
                    message_text=error_text,
                    parse_mode='HTML',
                ),
            )
        )
    return results


def _flag_hint(query_text: str, texts) -> str:
    t = query_text.strip()
    if t.startswith('-r'):
        return '-r N дни [гб [уст.]]  — первым N  |  - для пропуска'
    if '-d' in t:
        return '-d 15  — скидка 15%  (миксируется)'
    if '-b' in t:
        return '-b 1500  — пополнить баланс  (миксируется)'
    if '-t' in t:
        return '-t 100 [60]  — временный трафик, по умолчанию 30 дней  (миксируется)'
    if '-p' in t:
        return '-p дни [гб [уст.]]  |  - пропуск позиции  (миксируется)'
    if '-g' in t:
        return '-g  — сброс использованного трафика  (миксируется)'
    return '@user -p дни [гб [уст.]] | -t гб [дней] | -d % | -b ₽ | -g сброс трафика | флаги миксируются | - пропуск'


async def handle_admin_inline_query(inline_query: types.InlineQuery) -> None:
    if not _is_admin(inline_query.from_user.id):
        await inline_query.answer([], cache_time=5)
        return

    texts = get_texts(DEFAULT_LANGUAGE)
    query_text = (inline_query.query or '').strip()
    parsed = _parse_query(query_text)

    thumb = texts.t(
        'INLINE_GIFT_THUMBNAIL_URL',
        'https://raw.githubusercontent.com/0x04A1A430/storage/refs/heads/main/bot/GIFT.png',
    )

    hint_text = _flag_hint(query_text, texts)
    hint_kwargs = dict(cache_time=1, switch_pm_text=hint_text, switch_pm_parameter='help')

    if not query_text:
        await inline_query.answer(_build_syntax_hint(texts), **hint_kwargs)
        return

    # Multi-activation mode (-r N ...)
    if parsed.is_multi:
        has_params = parsed.days is not None or parsed.traffic_gb is not None or parsed.devices is not None
        if not has_params:
            await inline_query.answer([], **hint_kwargs)
            return

        summary = _gift_summary(
            parsed.days,
            parsed.traffic_gb,
            parsed.devices,
            texts,
        )
        gift_code = secrets.token_urlsafe(32)
        bot_username = settings.BOT_USERNAME or ''
        deep_link = f'https://t.me/{bot_username}?start={_GIFT_PREFIX}{gift_code}'
        caption = _build_subscription_caption(
            '',
            parsed.days,
            parsed.traffic_gb,
            parsed.devices,
            texts,
            multi_count=parsed.multi_count,
        )
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            'INLINE_GIFT_ACTIVATE_BUTTON_N',
                            'Активировать (осталось: {n})',
                        ).format(n=parsed.multi_count),
                        url=deep_link,
                    )
                ]
            ]
        )
        results = [
            types.InlineQueryResultArticle(
                id=gift_code,
                title=texts.t('INLINE_GIFT_RANDOM_TITLE', 'Первым {n} — {gift}').format(
                    n=parsed.multi_count, gift=summary
                ),
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
            )
        ]
        await inline_query.answer(
            results,
            cache_time=0,
            is_personal=True,
            switch_pm_text=hint_text,
            switch_pm_parameter='help',
        )
        return

    # Named target
    username = parsed.username
    target_id = parsed.target_id

    if not username and not target_id:
        await inline_query.answer([], **hint_kwargs)
        return

    recipient_display = f'@{username}' if username else str(target_id)
    sub_info_lines: list[str] = []
    sub = None
    cur_days, cur_devices = 0, 1

    async with AsyncSessionLocal() as db:
        from sqlalchemy import func as sql_func

        if target_id:
            result = await db.execute(select(User).where(User.telegram_id == target_id))
        else:
            result = await db.execute(select(User).where(sql_func.lower(User.username) == username.lower()))
        db_user = result.scalars().first()

        if db_user:
            if db_user.username:
                full = ' '.join(p for p in [db_user.first_name or '', db_user.last_name or ''] if p).strip()
                recipient_display = f'{full} (@{db_user.username})' if full else f'@{db_user.username}'
            else:
                full = ' '.join(p for p in [db_user.first_name or '', db_user.last_name or ''] if p).strip()
                recipient_display = f'{full} (id:{db_user.telegram_id})' if full else f'id:{db_user.telegram_id}'

            sub = await get_subscription_by_user_id(db, db_user.id)
            if sub:
                cur_days = max(0, sub.days_left) if hasattr(sub, 'days_left') else 0
                cur_devices = sub.device_limit or 1
                gb = sub.traffic_limit_gb
                traffic_str = texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит') if gb == 0 else f'{gb} ГБ'
                sub_info_lines = [
                    f'{sub.days_left} дн.',
                    traffic_str,
                    f'{sub.device_limit} уст.',
                ]
            else:
                sub_info_lines = [texts.t('INLINE_GIFT_NO_SUB', 'нет подписки')]

    # Info-only (no components set) → show current subscription info
    if not parsed.has_any:
        current_info = ' | '.join(sub_info_lines) if sub_info_lines else '—'
        safe_target = html.escape(username) if username else str(target_id)
        results = [
            types.InlineQueryResultArticle(
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
            )
        ]
        await inline_query.answer(results, cache_time=0, is_personal=True)
        return

    gift_code = secrets.token_urlsafe(32)
    bot_username = settings.BOT_USERNAME or ''
    deep_link = f'https://t.me/{bot_username}?start={_GIFT_PREFIX}{gift_code}'
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

    # Build combined caption / description / title from all components
    caption = _build_combo_caption(recipient_display, parsed, texts)
    title_parts: list[str] = []
    desc_parts: list[str] = []

    if parsed.has_subscription:
        summary = _gift_summary(parsed.days, parsed.traffic_gb, parsed.devices, texts)
        title_parts.append(summary)
        if sub_info_lines and sub:
            result_parts = []
            if parsed.days is not None:
                result_parts.append(_days_label_short(cur_days + parsed.days, texts))
            if parsed.traffic_gb is not None:
                result_parts.append(_fmt_traffic(parsed.traffic_gb, texts))
            if parsed.devices is not None:
                result_parts.append(f'{max(cur_devices, parsed.devices)} уст.')
            desc_parts.append(' | '.join(result_parts) if result_parts else ' | '.join(sub_info_lines))
        else:
            desc_parts.append(summary)

    if parsed.has_discount:
        title_parts.append(f'скидка {parsed.discount_percent}%')
        desc_parts.append(texts.t('INLINE_GIFT_DISCOUNT_DESC', 'Скидка {pct}%').format(pct=parsed.discount_percent))

    if parsed.has_balance:
        title_parts.append(f'+{parsed.balance_rub} ₽')
        desc_parts.append(texts.t('INLINE_GIFT_BALANCE_DESC', '+{rub} ₽ на баланс').format(rub=parsed.balance_rub))

    if parsed.has_temp:
        temp_days = parsed.temp_traffic_days or 30
        title_parts.append(f'+{parsed.temp_traffic_gb} ГБ врем.')
        desc_parts.append(f'+{parsed.temp_traffic_gb} ГБ трафика на {temp_days} дн.')

    if parsed.has_reset:
        title_parts.append('сброс трафика')
        desc_parts.append(texts.t('INLINE_GIFT_RESET_DESC', 'Сброс использованного трафика'))

    description = ', '.join(desc_parts)
    title = f'{recipient_display} — ' + ' + '.join(title_parts)

    results = [
        types.InlineQueryResultArticle(
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
        )
    ]
    await inline_query.answer(results, cache_time=0, is_personal=True)


async def handle_chosen_inline_result(chosen: types.ChosenInlineResult) -> None:
    if not _is_admin(chosen.from_user.id):
        return

    gift_code = chosen.result_id
    if gift_code in (
        'info_only',
        'hint_sub',
        'hint_multi',
        'hint_disc',
        'hint_bal',
        'hint_t',
        'hint_mix',
    ):
        return

    inline_message_id = chosen.inline_message_id
    query_text = chosen.query or ''
    parsed = _parse_query(query_text)

    async with AsyncSessionLocal() as db:
        admin_user = await get_user_by_telegram_id(db, chosen.from_user.id)
        from sqlalchemy import func as sql_func

        if parsed.is_multi:
            has_params = parsed.days is not None or parsed.traffic_gb is not None or parsed.devices is not None
            if not has_params:
                return
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=0,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type='subscription',
                days=parsed.days,
                traffic_limit_gb=parsed.traffic_gb,
                device_limit=parsed.devices,
                max_activations=parsed.multi_count,
                activated_count=0,
                inline_message_id=inline_message_id,
            )
            db.add(gift)
            await db.commit()
            logger.info(
                'Multi-activation gift created',
                gift_code=gift_code,
                count=parsed.multi_count,
            )
            return

        # Resolve recipient
        if parsed.target_id:
            result = await db.execute(select(User).where(User.telegram_id == parsed.target_id))
        elif parsed.username:
            result = await db.execute(select(User).where(sql_func.lower(User.username) == parsed.username.lower()))
        else:
            return

        db_user = result.scalars().first()
        recipient_telegram_id = db_user.telegram_id if db_user else (parsed.target_id or 0)

        intended_sentinel = None
        if not db_user:
            if parsed.target_id:
                intended_sentinel = f'tid:{parsed.target_id}'
            else:
                intended_sentinel = f'u:{parsed.username}'

        if not parsed.has_any:
            return

        components = []
        if parsed.has_subscription:
            components.append('subscription')
        if parsed.has_discount:
            components.append('discount')
        if parsed.has_balance:
            components.append('balance')
        if parsed.has_temp:
            components.append('temp_traffic')
        if parsed.has_reset:
            components.append('reset')

        gift_type = 'combo' if len(components) > 1 else components[0]

        gift = InlineGiftSubscription(
            gift_code=gift_code,
            recipient_telegram_id=recipient_telegram_id,
            sender_user_id=admin_user.id if admin_user else None,
            gift_type=gift_type,
            days=parsed.days,
            traffic_limit_gb=parsed.traffic_gb,
            device_limit=parsed.devices,
            discount_percent=parsed.discount_percent if parsed.has_discount else None,
            balance_amount_kopeks=parsed.balance_rub * 100 if parsed.has_balance else None,
            temp_traffic_gb=parsed.temp_traffic_gb if parsed.has_temp else None,
            temp_traffic_days=(parsed.temp_traffic_days or 30) if parsed.has_temp else None,
            reset_traffic=parsed.reset_traffic if parsed.has_reset else None,
            inline_message_id=intended_sentinel or inline_message_id,
        )

        db.add(gift)
        await db.commit()
        logger.info(
            'Inline gift created',
            gift_code=gift_code,
            recipient_telegram_id=recipient_telegram_id,
            gift_type=gift_type,
            components=components,
            target_id=parsed.target_id,
            username=parsed.username,
            db_user_found=db_user is not None,
            intended_sentinel=intended_sentinel,
            inline_message_id=inline_message_id,
        )


def register_handlers(dp: Dispatcher) -> None:
    dp.inline_query.register(handle_admin_inline_query)
    dp.chosen_inline_result.register(handle_chosen_inline_result)
