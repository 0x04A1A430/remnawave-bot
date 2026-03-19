"""
Multi-tariff subscription list handler.

Shows all user subscriptions with per-subscription management.
Only active when MULTI_TARIFF_ENABLED=True.
"""

from __future__ import annotations

import structlog
from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.subscription import (
    get_active_subscriptions_by_user_id,
    get_subscription_by_id_for_user,
)
from app.database.models import User
from app.localization.texts import get_texts


logger = structlog.get_logger(__name__)

router = Router()


def _format_subscription_line(sub, idx: int) -> str:
    """Format a single subscription for the list view."""
    tariff_name = sub.tariff.name if sub.tariff else 'Подписка'
    status_emoji = '🟢' if sub.is_active else '🔴'

    # Traffic info
    if sub.traffic_limit_gb == 0:
        traffic = '∞'
    else:
        used = f'{sub.traffic_used_gb:.1f}' if sub.traffic_used_gb else '0'
        traffic = f'{used}/{sub.traffic_limit_gb} ГБ'

    # Devices
    devices = f'{sub.device_limit} устр.' if sub.device_limit else ''

    # End date
    end_date = sub.end_date.strftime('%d.%m.%Y') if sub.end_date else '—'

    parts = [f'{status_emoji} <b>{idx}. {tariff_name}</b>']
    parts.append(f'   📊 Трафик: {traffic}')
    if devices:
        parts.append(f'   📱 Устройства: {devices}')
    parts.append(f'   📅 До: {end_date}')

    return '\n'.join(parts)


def _build_subscriptions_keyboard(subscriptions: list, language: str) -> types.InlineKeyboardMarkup:
    """Build inline keyboard with per-subscription management buttons."""
    buttons = []
    for idx, sub in enumerate(subscriptions, 1):
        tariff_name = sub.tariff.name if sub.tariff else f'Подписка #{sub.id}'
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text=f'⚙️ {tariff_name}',
                    callback_data=f'sm:{sub.id}',
                )
            ]
        )

    # "Buy another tariff" button
    texts = get_texts(language)
    buy_text = getattr(texts, 'MENU_BUY_SUBSCRIPTION', 'Купить ещё тариф')
    buttons.append(
        [
            types.InlineKeyboardButton(text=f'➕ {buy_text}', callback_data='menu_buy'),
        ]
    )
    # Back button
    buttons.append(
        [
            types.InlineKeyboardButton(text='◀️ Назад', callback_data='menu_main'),
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_subscription_detail_keyboard(
    sub_id: int,
) -> types.InlineKeyboardMarkup:
    """Build keyboard for single subscription management."""
    buttons = [
        [types.InlineKeyboardButton(text='🔗 Ссылка подключения', callback_data=f'sl:{sub_id}')],
        [types.InlineKeyboardButton(text='🔄 Продлить', callback_data=f'se:{sub_id}')],
        [types.InlineKeyboardButton(text='📊 Трафик', callback_data=f'st:{sub_id}')],
        [types.InlineKeyboardButton(text='📱 Устройства', callback_data=f'sd:{sub_id}')],
        [types.InlineKeyboardButton(text='◀️ К списку подписок', callback_data='my_subscriptions')],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


async def show_my_subscriptions(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext | None = None,
) -> None:
    """Show list of all user subscriptions."""
    if not settings.is_multi_tariff_enabled():
        # Fallback to legacy single subscription view
        return

    subscriptions = await get_active_subscriptions_by_user_id(db, db_user.id)

    if not subscriptions:
        text = '📋 <b>Мои подписки</b>\n\nУ вас нет активных подписок.'
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🛒 Купить подписку', callback_data='menu_buy')],
                [types.InlineKeyboardButton(text='◀️ Назад', callback_data='menu_main')],
            ]
        )
    else:
        lines = ['📋 <b>Мои подписки</b>\n']
        for idx, sub in enumerate(subscriptions, 1):
            lines.append(_format_subscription_line(sub, idx))
            lines.append('')  # empty line between subscriptions
        text = '\n'.join(lines)
        keyboard = _build_subscriptions_keyboard(subscriptions, db_user.language)

    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


async def show_subscription_detail(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Show detail view for a single subscription (IDOR protected)."""
    parts = callback.data.split(':')
    if len(parts) < 2:
        await callback.answer('Неверный формат', show_alert=True)
        return

    sub_id = int(parts[1])
    subscription = await get_subscription_by_id_for_user(db, sub_id, db_user.id)

    if not subscription:
        await callback.answer('Подписка не найдена', show_alert=True)
        return

    tariff_name = subscription.tariff.name if subscription.tariff else 'Подписка'

    # Traffic
    if subscription.traffic_limit_gb == 0:
        traffic = '∞ ГБ'
    else:
        used = f'{subscription.traffic_used_gb:.1f}' if subscription.traffic_used_gb else '0'
        traffic = f'{used} / {subscription.traffic_limit_gb} ГБ'

    end_date = subscription.end_date.strftime('%d.%m.%Y %H:%M') if subscription.end_date else '—'
    status = subscription.status_display

    text = (
        f'📋 <b>{tariff_name}</b>\n\n'
        f'Статус: {status}\n'
        f'📊 Трафик: {traffic}\n'
        f'📱 Устройства: {subscription.device_limit}\n'
        f'📅 До: {end_date}\n'
    )

    if subscription.subscription_url:
        text += f'\n🔗 <code>{subscription.subscription_url}</code>'

    keyboard = _build_subscription_detail_keyboard(sub_id)

    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


async def handle_subscription_link(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Delegation: sl:{sub_id} → connect subscription link handler."""
    sub_id = _extract_sub_id(callback)
    if sub_id is None:
        await callback.answer('Неверный формат', show_alert=True)
        return

    subscription = await get_subscription_by_id_for_user(db, sub_id, db_user.id)
    if not subscription:
        await callback.answer('Подписка не найдена', show_alert=True)
        return

    from .links import handle_connect_subscription

    await handle_connect_subscription(callback, db_user, db)


async def handle_subscription_extend(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Delegation: se:{sub_id} → extend/renew subscription handler."""
    sub_id = _extract_sub_id(callback)
    if sub_id is None:
        await callback.answer('Неверный формат', show_alert=True)
        return

    subscription = await get_subscription_by_id_for_user(db, sub_id, db_user.id)
    if not subscription:
        await callback.answer('Подписка не найдена', show_alert=True)
        return

    from .purchase import handle_extend_subscription

    await handle_extend_subscription(callback, db_user, db)


async def handle_subscription_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Delegation: st:{sub_id} → traffic management handler."""
    sub_id = _extract_sub_id(callback)
    if sub_id is None:
        await callback.answer('Неверный формат', show_alert=True)
        return

    subscription = await get_subscription_by_id_for_user(db, sub_id, db_user.id)
    if not subscription:
        await callback.answer('Подписка не найдена', show_alert=True)
        return

    from .traffic import handle_add_traffic

    await handle_add_traffic(callback, db_user, db)


async def handle_subscription_devices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Delegation: sd:{sub_id} → devices management handler."""
    sub_id = _extract_sub_id(callback)
    if sub_id is None:
        await callback.answer('Неверный формат', show_alert=True)
        return

    subscription = await get_subscription_by_id_for_user(db, sub_id, db_user.id)
    if not subscription:
        await callback.answer('Подписка не найдена', show_alert=True)
        return

    from .devices import handle_change_devices

    await handle_change_devices(callback, db_user, db)


def _extract_sub_id(callback: types.CallbackQuery) -> int | None:
    """Extract subscription ID from callback_data format 'prefix:sub_id'."""
    parts = (callback.data or '').split(':')
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except (ValueError, TypeError):
            return None
    return None
