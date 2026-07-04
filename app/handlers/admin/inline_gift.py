"""Admin inline query handler for gifting subscriptions, discounts and balance.

Syntax:

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

  Multi-activation (первым N):
    @botname -r 5 30               — 5 активаций, 30 дней
    @botname -r 5 30 500 3         — 5 активаций, 30 дней, 500 ГБ, 3 уст.
    @botname -r 5 30               — 5 активаций, 30 дней
    @botname -r 5 30 500 3         — 5 активаций, 30 дней, 500 ГБ, 3 уст.

  Discount:
    @botname @user -d 15           — скидка 15%

  Balance:
    @botname @user -b 1500         — +1500 ₽

Special values: -1 for days = forever; -1 for traffic = unlimited; -1 for devices = 999
Placeholder: - (dash) = пропустить позицию, не менять значение
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

_GIFT_PREFIX = "bs_"

_FOREVER_DAYS = (2099 - 2025) * 365
_MAX_DEVICES = 999


def _is_admin(telegram_id: int) -> bool:
    return settings.is_admin(telegram_id)


@dataclass
class ParsedQuery:
    gift_type: Literal["subscription", "discount", "balance", "multi", "temp_traffic"]
    username: str
    target_id: int  # >0 when user entered numeric ID
    multi_count: int  # >0 for -r mode
    days: int | None  # None = no change
    traffic_gb: int | None  # None = no change; -1 = unlimited; 0 = unlimited in DB
    devices: int | None  # None = no change
    discount_percent: int
    balance_rub: int
    temp_traffic_gb: int = field(default=0)  # >0 for -t mode (временный трафик)


def _parse_val(s: str, allow_neg_one: bool = False) -> int | None:
    """Parse a token: '-' → None (skip), '-1' → -1 (special), else int ≥ 0."""
    if s == "-":
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
        return ParsedQuery("subscription", "", 0, 0, None, None, None, 0, 0)

    # -r N [days [traffic [devices]]]
    if tokens[0] == "-r":
        rest = tokens[1:]
        count = max(1, int(rest[0]) if rest and rest[0].isdigit() else 1)
        rest = rest[1:]
        days, traffic, devices = _parse_sub_args(rest)
        return ParsedQuery("multi", "", 0, count, days, traffic, devices, 0, 0)

    # Extract target
    first = tokens[0]
    if first.startswith("@"):
        username = first.lstrip("@")
        target_id = 0
        rest = tokens[1:]
    elif first.lstrip("-").isdigit() and not first.startswith("-"):
        target_id = int(first)
        username = ""
        rest = tokens[1:]
    else:
        return ParsedQuery("subscription", "", 0, 0, None, None, None, 0, 0)

    if not rest:
        return ParsedQuery(
            "subscription", username, target_id, 0, None, None, None, 0, 0
        )

    flag = rest[0]
    args = rest[1:]

    if flag == "-d":
        pct = max(1, min(99, int(args[0]) if args and args[0].isdigit() else 0))
        return ParsedQuery(
            "discount", username, target_id, 0, None, None, None, pct, 0
        )

    if flag == "-b":
        rub = int(args[0]) if args and args[0].isdigit() else 0
        return ParsedQuery(
            "balance", username, target_id, 0, None, None, None, 0, rub
        )

    if flag == "-t":
        gb = max(1, int(args[0])) if args and args[0].isdigit() else 0
        return ParsedQuery(
            "temp_traffic", username, target_id, 0, None, None, None, 0, 0, gb
        )

    if flag == "-p":
        days, traffic, devices = _parse_sub_args(args)
        return ParsedQuery(
            "subscription", username, target_id, 0, days, traffic, devices, 0, 0
        )

    # No flag: plain values after target
    days, traffic, devices = _parse_sub_args(rest)
    return ParsedQuery(
        "subscription", username, target_id, 0, days, traffic, devices, 0, 0
    )


def _days_label_short(days: int, texts) -> str:
    if days >= _FOREVER_DAYS:
        return texts.t("INLINE_GIFT_LABEL_FOREVER", "Навсегда")
    if days < 365:
        return texts.t("INLINE_GIFT_LABEL_DAYS_SHORT", "{n} дн.").format(n=days)
    if days == 365:
        return texts.t("INLINE_GIFT_LABEL_YEAR", "1 г.")
    if days % 365 == 0:
        return texts.t("INLINE_GIFT_LABEL_YEARS", "{n} г.").format(n=days // 365)
    return texts.t("INLINE_GIFT_LABEL_DAYS_SHORT", "{n} дн.").format(n=days)


def _fmt_traffic(gb: int, texts) -> str:
    if gb <= 0 or gb == -1:
        return texts.t("INLINE_GIFT_TRAFFIC_UNLIMITED", "Безлимит")
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
    return ", ".join(parts) if parts else "—"


def _build_subscription_caption(
    display: str,
    days: int | None,
    traffic_gb: int | None,
    devices: int | None,
    texts,
    multi_count: int = 0,
) -> str:
    safe = html.escape(display) if display else ""
    lines = []
    if days is not None:
        lines.append(_days_label_short(days, texts))
    if traffic_gb is not None:
        lines.append(_fmt_traffic(traffic_gb, texts))
    if devices is not None:
        lines.append(f'{devices} {texts.t("INLINE_GIFT_DEVICES_SUFFIX", "уст.")}')
    body = "\n".join(lines) if lines else "—"
    hint = texts.t(
        "INLINE_GIFT_CAPTION_HINT", "Нажмите кнопку ниже, чтобы активировать."
    )

    if multi_count > 0:
        header = texts.t(
            "INLINE_GIFT_CAPTION_HEADER_RANDOM", "Подарочная подписка — первым {n}"
        ).format(n=multi_count)
        return (
            f"<b>{header}</b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>"
        )

    header = texts.t("INLINE_GIFT_CAPTION_HEADER", "Подарочная подписка для")
    return f"<b>{header} <code>{safe}</code></b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>"


def _build_discount_caption(display: str, pct: int, texts) -> str:
    safe = html.escape(display)
    header = texts.t("INLINE_GIFT_DISCOUNT_HEADER", "Скидка для")
    hint = texts.t(
        "INLINE_GIFT_CAPTION_HINT", "Нажмите кнопку ниже, чтобы активировать."
    )
    body = texts.t(
        "INLINE_GIFT_DISCOUNT_BODY", "Скидка {pct}% на следующую покупку"
    ).format(pct=pct)
    return f"<b>{header} <code>{safe}</code></b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>"


def _build_balance_caption(display: str, rub: int, texts) -> str:
    safe = html.escape(display)
    header = texts.t("INLINE_GIFT_BALANCE_HEADER", "Пополнение баланса для")
    hint = texts.t(
        "INLINE_GIFT_CAPTION_HINT", "Нажмите кнопку ниже, чтобы активировать."
    )
    body = texts.t("INLINE_GIFT_BALANCE_BODY", "+{rub} ₽ на баланс").format(rub=rub)
    return f"<b>{header} <code>{safe}</code></b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>"


def _build_syntax_hint(texts) -> list[types.InlineQueryResultArticle]:
    thumb = texts.t(
        "INLINE_GIFT_THUMBNAIL_URL",
        "https://raw.githubusercontent.com/cy7su/cy7su/refs/heads/main/GIFT.png",
    )
    hints = [
        (
            "hint_sub",
            "@user дни [гб [уст.]]",
            "Подписка: @user 30  /  @user 30 500 3  /  @user 30 - 3",
            "@user 30",
        ),
        (
            "hint_multi",
            "-r N дни [гб [уст.]]",
            "Первым N: -r 5 30  /  -r 5 30 500 3",
            "-r 5 30",
        ),
        ("hint_disc", "@user -d процент", "Скидка: @user -d 15", "@user -d 15"),
        ("hint_bal", "@user -b сумма", "Баланс: @user -b 1500", "@user -b 1500"),
        (
            "hint_t",
            "@user -t гб",
            "Временный трафик (30 дн.): @user -t 100",
            "@user -t 100",
        ),
    ]
    results = []
    for rid, title, desc, _ in hints:
        results.append(
            types.InlineQueryResultArticle(
                id=rid,
                title=title,
                description=desc,
                input_message_content=types.InputTextMessageContent(
                    message_text=title, parse_mode="HTML"
                ),
                thumbnail_url=thumb,
                thumbnail_width=512,
                thumbnail_height=512,
            )
        )
    return results


def _flag_hint(query_text: str, texts) -> str:
    t = query_text.strip()
    if "-r" in t:
        return "-r N дни [гб [уст.]]  — первым N  |  - для пропуска"
    if "-d" in t:
        return "-d процент  — скидка"
    if "-b" in t:
        return "-b сумма  — пополнить баланс"
    if "-t" in t:
        return "-t гб  — временный трафик 30 дней"
    if "-p" in t:
        return "-p дни [гб [уст.]]  |  - для пропуска позиции"
    return "@user дни [гб [уст.]] | -r N | -d % | -b ₽ | -t гб  |  - пропуск"


async def handle_admin_inline_query(inline_query: types.InlineQuery) -> None:
    if not _is_admin(inline_query.from_user.id):
        await inline_query.answer([], cache_time=5)
        return

    texts = get_texts(DEFAULT_LANGUAGE)
    query_text = (inline_query.query or "").strip()
    parsed = _parse_query(query_text)

    thumb = texts.t(
        "INLINE_GIFT_THUMBNAIL_URL",
        "https://raw.githubusercontent.com/cy7su/cy7su/refs/heads/main/GIFT.png",
    )

    hint_text = _flag_hint(query_text, texts)
    hint_kwargs = dict(
        cache_time=1, switch_pm_text=hint_text, switch_pm_parameter="help"
    )

    if not query_text:
        await inline_query.answer(_build_syntax_hint(texts), **hint_kwargs)
        return

    # Multi-activation mode (-r N ...)
    if parsed.gift_type == "multi":
        has_params = (
            parsed.days is not None
            or parsed.traffic_gb is not None
            or parsed.devices is not None
        )
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
        bot_username = settings.BOT_USERNAME or ""
        deep_link = f"https://t.me/{bot_username}?start={_GIFT_PREFIX}{gift_code}"
        caption = _build_subscription_caption(
            "",
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
                            "INLINE_GIFT_ACTIVATE_BUTTON_N",
                            "Активировать (осталось: {n})",
                        ).format(n=parsed.multi_count),
                        url=deep_link,
                    )
                ]
            ]
        )
        results = [
            types.InlineQueryResultArticle(
                id=gift_code,
                title=texts.t("INLINE_GIFT_RANDOM_TITLE", "Первым {n} — {gift}").format(
                    n=parsed.multi_count, gift=summary
                ),
                description=summary,
                thumbnail_url=thumb,
                thumbnail_width=512,
                thumbnail_height=512,
                input_message_content=types.InputTextMessageContent(
                    message_text=caption,
                    parse_mode="HTML",
                    link_preview_options=types.LinkPreviewOptions(
                        show_above_text=True, url=thumb
                    ),
                ),
                reply_markup=keyboard,
            )
        ]
        await inline_query.answer(
            results,
            cache_time=0,
            is_personal=True,
            switch_pm_text=hint_text,
            switch_pm_parameter="help",
        )
        return

    # Named target
    username = parsed.username
    target_id = parsed.target_id

    if not username and not target_id:
        await inline_query.answer([], **hint_kwargs)
        return

    recipient_display = f"@{username}" if username else str(target_id)
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
                full = " ".join(
                    p for p in [db_user.first_name or "", db_user.last_name or ""] if p
                ).strip()
                recipient_display = (
                    f"{full} (@{db_user.username})" if full else f"@{db_user.username}"
                )
            else:
                full = " ".join(
                    p for p in [db_user.first_name or "", db_user.last_name or ""] if p
                ).strip()
                recipient_display = (
                    f"{full} (id:{db_user.telegram_id})"
                    if full
                    else f"id:{db_user.telegram_id}"
                )

            sub = await get_subscription_by_user_id(db, db_user.id)
            if sub:
                cur_days = max(0, sub.days_left) if hasattr(sub, "days_left") else 0
                cur_traffic = sub.traffic_limit_gb or 0
                cur_devices = sub.device_limit or 1
                gb = sub.traffic_limit_gb
                traffic_str = (
                    texts.t("INLINE_GIFT_TRAFFIC_UNLIMITED", "Безлимит")
                    if gb == 0
                    else f"{gb} ГБ"
                )
                sub_info_lines = [
                    f"{sub.days_left} дн.",
                    traffic_str,
                    f"{sub.device_limit} уст.",
                ]
            else:
                sub_info_lines = [texts.t("INLINE_GIFT_NO_SUB", "нет подписки")]

    # Info-only
    is_info_only = (
        parsed.gift_type == "subscription"
        and parsed.days is None
        and parsed.traffic_gb is None
        and parsed.devices is None
        and not parsed.discount_percent
        and not parsed.balance_rub
    )
    if is_info_only:
        current_info = " | ".join(sub_info_lines) if sub_info_lines else "—"
        safe_target = html.escape(username) if username else str(target_id)
        results = [
            types.InlineQueryResultArticle(
                id="info_only",
                title=recipient_display,
                description=current_info,
                input_message_content=types.InputTextMessageContent(
                    message_text=f"<code>{safe_target}</code>",
                    parse_mode="HTML",
                ),
                thumbnail_url=thumb,
                thumbnail_width=512,
                thumbnail_height=512,
            )
        ]
        await inline_query.answer(results, cache_time=0, is_personal=True)
        return

    gift_code = secrets.token_urlsafe(32)
    bot_username = settings.BOT_USERNAME or ""
    deep_link = f"https://t.me/{bot_username}?start={_GIFT_PREFIX}{gift_code}"
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("INLINE_GIFT_ACTIVATE_BUTTON", "Активировать"),
                    url=deep_link,
                )
            ]
        ]
    )

    if parsed.gift_type == "discount":
        pct = parsed.discount_percent
        caption = _build_discount_caption(recipient_display, pct, texts)
        description = texts.t("INLINE_GIFT_DISCOUNT_DESC", "Скидка {pct}%").format(
            pct=pct
        )
        title = f"{recipient_display} — скидка {pct}%"

    elif parsed.gift_type == "balance":
        rub = parsed.balance_rub
        caption = _build_balance_caption(recipient_display, rub, texts)
        description = texts.t("INLINE_GIFT_BALANCE_DESC", "+{rub} ₽ на баланс").format(
            rub=rub
        )
        title = f"{recipient_display} — +{rub} ₽"

    elif parsed.gift_type == "temp_traffic":
        gb = parsed.temp_traffic_gb
        if not gb:
            await inline_query.answer([], **hint_kwargs)
            return
        safe = html.escape(recipient_display)
        hint = texts.t(
            "INLINE_GIFT_CAPTION_HINT", "Нажмите кнопку ниже, чтобы активировать."
        )
        body = texts.t(
            "INLINE_GIFT_TEMP_TRAFFIC_BODY", "+{gb} ГБ трафика (на 30 дней)"
        ).format(gb=gb)
        caption = (
            f"<b>{safe}</b>\n\n<blockquote>{body}</blockquote>\n\n<code>{hint}</code>"
        )
        description = f"+{gb} ГБ трафика (30 дней)"
        title = f"{recipient_display} — +{gb} ГБ трафика"

    else:
        summary = _gift_summary(
            parsed.days,
            parsed.traffic_gb,
            parsed.devices,
            texts,
        )

        if sub_info_lines and sub:
            result_parts = []
            if parsed.days is not None:
                result_days = cur_days + parsed.days
                result_parts.append(_days_label_short(result_days, texts))
            if parsed.traffic_gb is not None:
                result_parts.append(_fmt_traffic(parsed.traffic_gb, texts))
            if parsed.devices is not None:
                result_parts.append(f"{max(cur_devices, parsed.devices)} уст.")
            description = (
                f'{" | ".join(sub_info_lines)} → {", ".join(result_parts)}'
                if result_parts
                else " | ".join(sub_info_lines)
            )
        else:
            description = summary

        caption = _build_subscription_caption(
            recipient_display,
            parsed.days,
            parsed.traffic_gb,
            parsed.devices,
            texts,
        )
        title = f"{recipient_display} — {summary}"

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
                parse_mode="HTML",
                link_preview_options=types.LinkPreviewOptions(
                    show_above_text=True, url=thumb
                ),
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
        "info_only",
        "hint_sub",
        "hint_multi",
        "hint_disc",
        "hint_bal",
        "hint_t",
    ):
        return

    inline_message_id = chosen.inline_message_id
    query_text = chosen.query or ""
    parsed = _parse_query(query_text)

    async with AsyncSessionLocal() as db:
        admin_user = await get_user_by_telegram_id(db, chosen.from_user.id)
        from sqlalchemy import func as sql_func

        if parsed.gift_type == "multi":
            has_params = (
                parsed.days is not None
                or parsed.traffic_gb is not None
                or parsed.devices is not None
            )
            if not has_params:
                return
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=0,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type="subscription",
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
                "Multi-activation gift created",
                gift_code=gift_code,
                count=parsed.multi_count,
            )
            return

        # Resolve recipient
        if parsed.target_id:
            result = await db.execute(
                select(User).where(User.telegram_id == parsed.target_id)
            )
        elif parsed.username:
            result = await db.execute(
                select(User).where(
                    sql_func.lower(User.username) == parsed.username.lower()
                )
            )
        else:
            return

        db_user = result.scalars().first()
        recipient_telegram_id = (
            db_user.telegram_id if db_user else (parsed.target_id or 0)
        )

        intended_sentinel = None
        if not db_user:
            if parsed.target_id:
                intended_sentinel = f"tid:{parsed.target_id}"
            else:
                intended_sentinel = f"u:{parsed.username}"

        if parsed.gift_type == "discount":
            if not parsed.discount_percent:
                return
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=recipient_telegram_id,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type="discount",
                days=0,
                traffic_limit_gb=0,
                device_limit=1,
                discount_percent=parsed.discount_percent,
                inline_message_id=inline_message_id or intended_sentinel,
            )
        elif parsed.gift_type == "balance":
            if not parsed.balance_rub:
                return
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=recipient_telegram_id,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type="balance",
                days=0,
                traffic_limit_gb=0,
                device_limit=1,
                balance_amount_kopeks=parsed.balance_rub * 100,
                inline_message_id=inline_message_id or intended_sentinel,
            )
        elif parsed.gift_type == "temp_traffic":
            if not parsed.temp_traffic_gb:
                return
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=recipient_telegram_id,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type="temp_traffic",
                days=0,
                traffic_limit_gb=parsed.temp_traffic_gb,  # используем это поле для хранения кол-ва ГБ
                device_limit=1,
                inline_message_id=inline_message_id or intended_sentinel,
            )
        else:
            has_params = (
                parsed.days is not None
                or parsed.traffic_gb is not None
                or parsed.devices is not None
            )
            if not has_params:
                return
            gift = InlineGiftSubscription(
                gift_code=gift_code,
                recipient_telegram_id=recipient_telegram_id,
                sender_user_id=admin_user.id if admin_user else None,
                gift_type="subscription",
                days=parsed.days,
                traffic_limit_gb=parsed.traffic_gb,
                device_limit=parsed.devices,
                inline_message_id=inline_message_id or intended_sentinel,
            )

        db.add(gift)
        await db.commit()
        logger.info(
            "Inline gift created",
            gift_code=gift_code,
            recipient_telegram_id=recipient_telegram_id,
            gift_type=parsed.gift_type,
        )


def register_handlers(dp: Dispatcher) -> None:
    dp.inline_query.register(handle_admin_inline_query)
    dp.chosen_inline_result.register(handle_chosen_inline_result)
