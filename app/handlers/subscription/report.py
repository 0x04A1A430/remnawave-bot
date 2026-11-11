"""Экран «Отчёт по подписке» в rich-сообщении.

Экран переиспользует общие блоки из :mod:`app.utils.rich_ui` и штатный поток
перевыпуска подписки (``subscription_revoke`` / ``sr:{id}``). Сбор данных отделён
от рендера: :func:`build_subscription_report_html` — чистая функция, поэтому её
можно покрывать тестами без БД и Telegram.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

import structlog
from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramNotFound
from aiogram.fsm.context import FSMContext
from aiogram.types import InaccessibleMessage, InlineKeyboardButton, InlineKeyboardMarkup, InputRichMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.subscription import (
    check_and_update_subscription_status,
    get_subscription_by_id_for_user,
)
from app.database.models import Subscription, SubscriptionStatus, User
from app.localization.texts import Texts, get_texts
from app.utils.decorators import error_handler
from app.utils.photo_message import edit_or_answer_photo
from app.utils.rich_menu import mark_rich_menu_taken_over
from app.utils.rich_ui import details, heading, kv_table, progress_bar, rich_footer, rich_logo_html
from app.utils.subscription_utils import get_display_subscription_link
from app.utils.timezone import format_local_datetime

from .revoke import _check_revoke_cooldown


logger = structlog.get_logger(__name__)


# status code -> (localization key, default label, emoji)
_STATUS_META: dict[str, tuple[str, str, str]] = {
    'active': ('SUBSCRIPTION_STATUS_ACTIVE', 'Активна', '🟢'),
    'trial': ('SUBSCRIPTION_STATUS_TRIAL', 'Тестовая', '🧪'),
    'expired': ('SUBSCRIPTION_STATUS_EXPIRED', 'Истекла', '🔴'),
    'disabled': ('SUBSCRIPTION_STATUS_DISABLED', 'Приостановлена', '⏸'),
    'limited': ('SUBSCRIPTION_STATUS_LIMITED', 'Трафик исчерпан', '🟠'),
}


def _status_label(status_code: str, texts) -> str:
    key, default, emoji = _STATUS_META.get(
        status_code,
        ('SUBSCRIPTION_STATUS_UNKNOWN', 'Неизвестно', '⚪'),
    )
    return f'{emoji} {texts.t(key, default)}'


def _traffic_value(limit_gb: int, used_gb: float, texts) -> str:
    if limit_gb > 0:
        return f'{used_gb:.1f} / {limit_gb} ГБ'
    return texts.t('SUBSCRIPTION_REPORT_TRAFFIC_UNLIMITED', '∞ (безлимит)')


def build_subscription_report_html(
    data: dict[str, Any],
    texts,
    *,
    logo_html: str = '',
    footer_label: str = '@x1lagent',
) -> str:
    """Собирает rich-HTML отчёта. Чистая функция — без БД и Telegram."""
    blocks: list[str] = []
    if logo_html:
        blocks.append(logo_html)

    blocks.append(heading(texts.t('SUBSCRIPTION_REPORT_TITLE', '📊 Отчёт по подписке')))

    rows: list[tuple[str, str]] = [
        (
            texts.t('SUBSCRIPTION_REPORT_LABEL_STATUS', 'Статус'),
            html.escape(_status_label(str(data.get('status') or ''), texts)),
        )
    ]

    tariff_name = data.get('tariff_name')
    if tariff_name:
        rows.append(
            (
                texts.t('SUBSCRIPTION_REPORT_LABEL_TARIFF', 'Тариф'),
                html.escape(str(tariff_name)),
            )
        )

    end_date = data.get('end_date')
    if end_date:
        rows.append(
            (
                texts.t('SUBSCRIPTION_REPORT_LABEL_END_DATE', 'Действует до'),
                html.escape(str(end_date)),
            )
        )

    rows.append(
        (
            texts.t('SUBSCRIPTION_REPORT_LABEL_TIME_LEFT', 'Осталось'),
            html.escape(str(data.get('time_left') or '—')),
        )
    )

    limit_gb = int(data.get('traffic_limit_gb') or 0)
    used_gb = float(data.get('traffic_used_gb') or 0.0)
    rows.append(
        (
            texts.t('SUBSCRIPTION_REPORT_LABEL_TRAFFIC', 'Трафик'),
            html.escape(_traffic_value(limit_gb, used_gb, texts)),
        )
    )

    devices_used = str(data.get('devices_used') or '—')
    device_limit = Texts.format_device_limit(data.get('device_limit'))
    rows.append(
        (
            texts.t('SUBSCRIPTION_REPORT_LABEL_DEVICES', 'Устройства'),
            html.escape(f'{devices_used} / {device_limit}'),
        )
    )

    autopay_text = (
        texts.t('SUBSCRIPTION_REPORT_AUTOPAY_ON', 'вкл')
        if data.get('autopay_enabled')
        else texts.t('SUBSCRIPTION_REPORT_AUTOPAY_OFF', 'выкл')
    )
    rows.append(
        (
            texts.t('SUBSCRIPTION_REPORT_LABEL_AUTOPAY', 'Автоплатёж'),
            html.escape(str(autopay_text)),
        )
    )

    blocks.append(kv_table(rows))

    if limit_gb > 0:
        percent = min(used_gb / limit_gb * 100, 100.0)
        blocks.append(f'<p>{progress_bar(percent)} {percent:.0f}%</p>')

    link = data.get('link')
    if link and not data.get('hide_link'):
        blocks.append(
            details(
                texts.t('SUBSCRIPTION_REPORT_CONNECTION', '🔗 Подключение'),
                f'<p><code>{html.escape(str(link))}</code></p>',
            )
        )

    if data.get('revoke_enabled'):
        cooldown = data.get('revoke_cooldown_seconds')
        if cooldown:
            minutes, seconds = divmod(int(cooldown), 60)
            revoke_text = texts.t(
                'SUBSCRIPTION_REVOKE_COOLDOWN',
                '⏱ Перевыпуск будет доступен через {minutes} мин. {seconds} сек.',
            ).format(minutes=minutes, seconds=seconds)
        else:
            revoke_text = texts.t(
                'SUBSCRIPTION_REPORT_REVOKE_READY',
                'Перевыпуск доступен — кнопка ниже.',
            )
        blocks.append(
            details(
                texts.t('SUBSCRIPTION_REPORT_REVOKE_TITLE', '⏱ Перевыпуск'),
                f'<p>{html.escape(str(revoke_text))}</p>',
            )
        )

    blocks.append(rich_footer(footer_label))

    return ''.join(blocks)


def build_subscription_report_text(data: dict[str, Any], texts) -> str:
    """Классический fallback: те же данные обычным HTML без table/details."""
    status = _status_label(str(data.get('status') or ''), texts)
    lines = [f'📊 <b>{texts.t("SUBSCRIPTION_REPORT_TITLE_PLAIN", "Отчёт по подписке")}</b>', '']
    lines.append(f'{texts.t("SUBSCRIPTION_REPORT_LABEL_STATUS", "Статус")}: {html.escape(status)}')

    tariff_name = data.get('tariff_name')
    if tariff_name:
        lines.append(
            f'{texts.t("SUBSCRIPTION_REPORT_LABEL_TARIFF", "Тариф")}: {html.escape(str(tariff_name))}'
        )

    end_date = data.get('end_date')
    if end_date:
        lines.append(
            f'{texts.t("SUBSCRIPTION_REPORT_LABEL_END_DATE", "Действует до")}: {html.escape(str(end_date))}'
        )

    lines.append(
        f'{texts.t("SUBSCRIPTION_REPORT_LABEL_TIME_LEFT", "Осталось")}: '
        f'{html.escape(str(data.get("time_left") or "—"))}'
    )

    limit_gb = int(data.get('traffic_limit_gb') or 0)
    used_gb = float(data.get('traffic_used_gb') or 0.0)
    traffic = _traffic_value(limit_gb, used_gb, texts)
    if limit_gb > 0:
        percent = min(used_gb / limit_gb * 100, 100.0)
        traffic = f'{traffic} ({percent:.0f}%)'
    lines.append(f'{texts.t("SUBSCRIPTION_REPORT_LABEL_TRAFFIC", "Трафик")}: {html.escape(traffic)}')

    devices_used = str(data.get('devices_used') or '—')
    device_limit = Texts.format_device_limit(data.get('device_limit'))
    lines.append(
        f'{texts.t("SUBSCRIPTION_REPORT_LABEL_DEVICES", "Устройства")}: '
        f'{html.escape(f"{devices_used} / {device_limit}")}'
    )

    autopay_text = (
        texts.t('SUBSCRIPTION_REPORT_AUTOPAY_ON', 'вкл')
        if data.get('autopay_enabled')
        else texts.t('SUBSCRIPTION_REPORT_AUTOPAY_OFF', 'выкл')
    )
    lines.append(
        f'{texts.t("SUBSCRIPTION_REPORT_LABEL_AUTOPAY", "Автоплатёж")}: {html.escape(str(autopay_text))}'
    )

    link = data.get('link')
    if link and not data.get('hide_link'):
        lines.extend(
            [
                '',
                (
                    f'{texts.t("SUBSCRIPTION_REPORT_CONNECTION", "🔗 Подключение")}: '
                    f'<code>{html.escape(str(link))}</code>'
                ),
            ]
        )

    if data.get('revoke_enabled'):
        cooldown = data.get('revoke_cooldown_seconds')
        if cooldown:
            minutes, seconds = divmod(int(cooldown), 60)
            revoke_text = texts.t(
                'SUBSCRIPTION_REVOKE_COOLDOWN',
                '⏱ Перевыпуск будет доступен через {minutes} мин. {seconds} сек.',
            ).format(minutes=minutes, seconds=seconds)
        else:
            revoke_text = texts.t(
                'SUBSCRIPTION_REPORT_REVOKE_READY',
                'Перевыпуск доступен — кнопка ниже.',
            )
        lines.extend(
            [
                '',
                (
                    f'{texts.t("SUBSCRIPTION_REPORT_REVOKE_TITLE", "⏱ Перевыпуск")}: '
                    f'{html.escape(str(revoke_text))}'
                ),
            ]
        )

    return '\n'.join(lines)


def build_subscription_report_keyboard(
    language: str,
    subscription: Subscription,
    *,
    cooldown_seconds: int | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура отчёта: перевыпуск (если доступен) и возврат назад."""
    texts = get_texts(language)
    multi_tariff = settings.is_multi_tariff_enabled()
    sub_id = getattr(subscription, 'id', None)

    buttons: list[list[InlineKeyboardButton]] = []

    # На кулдауне кнопку не рисуем: оставшееся время видно в details отчёта.
    if settings.is_subscription_revoke_enabled() and not cooldown_seconds:
        revoke_callback = f'sr:{sub_id}' if multi_tariff and sub_id is not None else 'subscription_revoke'
        buttons.append(
            [
                InlineKeyboardButton(
                    text=texts.t('SUBSCRIPTION_REPORT_REVOKE_BTN', '🔄 Перевыпустить подписку'),
                    callback_data=revoke_callback,
                    style='danger',
                )
            ]
        )

    back_callback = f'sm:{sub_id}' if multi_tariff and sub_id is not None else 'menu_subscription'
    buttons.append(
        [
            InlineKeyboardButton(
                text=texts.BACK,
                callback_data=back_callback,
                style='primary',
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _resolve_status(subscription: Subscription, now: datetime) -> str:
    status = str(getattr(subscription, 'status', '') or '')
    end_date = getattr(subscription, 'end_date', None)

    if status == SubscriptionStatus.LIMITED.value:
        return 'limited'
    if status == SubscriptionStatus.DISABLED.value:
        return 'disabled'
    if status == SubscriptionStatus.EXPIRED.value:
        return 'expired'
    if end_date is not None and end_date <= now:
        return 'expired'
    if status == SubscriptionStatus.ACTIVE.value:
        return 'trial' if getattr(subscription, 'is_trial', False) else 'active'
    if status in _STATUS_META:
        return status
    return 'unknown'


def _format_time_left(subscription: Subscription, texts, now: datetime) -> str:
    end_date = getattr(subscription, 'end_date', None)
    if end_date is None or end_date <= now:
        return texts.t('SUBSCRIPTION_TIME_LEFT_EXPIRED', 'истёк')
    if end_date.year > 2050:
        return texts.t('SUBSCRIPTION_REPORT_TIME_FOREVER', 'навсегда')

    delta = end_date - now
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return texts.t('SUBSCRIPTION_REPORT_TIME_LEFT', '{days} дн. {hours} ч.').format(days=days, hours=hours)
    if hours > 0:
        return texts.t('SUBSCRIPTION_TIME_LEFT_HOURS', '{hours} ч.').format(hours=hours)
    minutes = (delta.seconds % 3600) // 60
    return texts.t('SUBSCRIPTION_TIME_LEFT_MINUTES', '{minutes} мин.').format(minutes=minutes)


async def collect_subscription_report_data(
    db: AsyncSession,
    db_user: User,
    subscription: Subscription,
) -> dict[str, Any]:
    """Собирает данные для отчёта (тариф, устройства, кулдаун перевыпуска)."""
    now = datetime.now(UTC)
    texts = get_texts(db_user.language)

    tariff_name = None
    if settings.is_tariffs_mode() and getattr(subscription, 'tariff_id', None):
        try:
            from app.database.crud.tariff import get_tariff_by_id

            tariff = await get_tariff_by_id(db, subscription.tariff_id)
            if tariff:
                tariff_name = tariff.name
        except Exception as error:
            logger.warning(
                'Не удалось получить тариф для отчёта по подписке',
                subscription_id=getattr(subscription, 'id', None),
                error=error,
            )

    try:
        from .devices import get_current_devices_count

        devices_used = await get_current_devices_count(db_user, subscription)
    except Exception as error:
        logger.warning(
            'Не удалось получить количество устройств для отчёта',
            user_id=getattr(db_user, 'id', None),
            error=error,
        )
        devices_used = '—'

    return {
        'status': _resolve_status(subscription, now),
        'tariff_name': tariff_name,
        'end_date': format_local_datetime(subscription.end_date, '%d.%m.%Y %H:%M')
        if getattr(subscription, 'end_date', None)
        else None,
        'time_left': _format_time_left(subscription, texts, now),
        'traffic_limit_gb': int(getattr(subscription, 'traffic_limit_gb', 0) or 0),
        'traffic_used_gb': float(getattr(subscription, 'traffic_used_gb', 0.0) or 0.0),
        'devices_used': devices_used,
        'device_limit': getattr(subscription, 'device_limit', None),
        'autopay_enabled': bool(getattr(subscription, 'autopay_enabled', False)),
        'link': get_display_subscription_link(subscription),
        'hide_link': settings.should_hide_subscription_link(),
        'revoke_enabled': settings.is_subscription_revoke_enabled(),
        'revoke_cooldown_seconds': _check_revoke_cooldown(subscription),
    }


async def _resolve_report_subscription(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    payload = callback.data or ''
    if not payload.startswith('subscription_report:'):
        return db_user.subscription

    raw_id = payload.split(':', 1)[1].strip()
    try:
        sub_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    # IDOR protection: выбираем только подписку текущего пользователя.
    return await get_subscription_by_id_for_user(db, sub_id, db_user.id)


@error_handler
async def show_subscription_report(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext | None = None,
) -> None:
    """Показывает rich-отчёт по подписке с кнопкой перевыпуска."""
    message = callback.message
    if message is None or callback.bot is None:
        await callback.answer()
        return

    texts = get_texts(db_user.language)

    await db.refresh(db_user)

    subscription = await _resolve_report_subscription(callback, db_user, db)
    if not subscription:
        await callback.answer(
            texts.t('SUBSCRIPTION_NOT_FOUND', 'Подписка не найдена'),
            show_alert=True,
        )
        return

    subscription = await check_and_update_subscription_status(db, subscription)
    await db.refresh(subscription)
    await db.refresh(db_user)

    await callback.answer()

    report_data = await collect_subscription_report_data(db, db_user, subscription)
    rich_html = build_subscription_report_html(
        report_data,
        texts,
        logo_html=rich_logo_html(),
    )
    classic_text = build_subscription_report_text(report_data, texts)
    keyboard = build_subscription_report_keyboard(
        db_user.language,
        subscription,
        cooldown_seconds=report_data.get('revoke_cooldown_seconds'),
    )

    # Сообщение-меню могло быть rich — отмечаем, что новый экран его переиспользует,
    # иначе cleanup-middleware удалит только что отредактированное сообщение.
    mark_rich_menu_taken_over(message.chat.id)

    is_editable_rich = (
        not isinstance(message, InaccessibleMessage)
        and not getattr(message, 'photo', None)
        and (message.text is not None or getattr(message, 'rich_message', None) is not None)
    )

    try:
        if is_editable_rich:
            await callback.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.message_id,
                rich_message=InputRichMessage(html=rich_html),
                reply_markup=keyboard,
                parse_mode=None,
            )
        else:
            if not isinstance(message, InaccessibleMessage):
                try:
                    await message.delete()
                except (TelegramBadRequest, TelegramNotFound, TelegramNetworkError):
                    pass
            await callback.bot.send_rich_message(
                chat_id=message.chat.id,
                rich_message=InputRichMessage(html=rich_html),
                reply_markup=keyboard,
            )
    except (TelegramBadRequest, TelegramNotFound, TelegramNetworkError) as error:
        logger.warning(
            'Не удалось показать rich-отчёт по подписке, фоллбек на классику',
            user_id=getattr(db_user, 'id', None),
            subscription_id=getattr(subscription, 'id', None),
            error=error,
        )
        await edit_or_answer_photo(callback, classic_text, keyboard)
