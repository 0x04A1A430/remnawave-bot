"""Handler for inline gift subscription deep-links and activation.

Flow:
1. Admin sends inline message via inline query.
2. Recipient clicks "Активировать" → lands in the bot with /start <gift_code>
3. Bot verifies recipient, shows what will change.
4. On activation: extends existing subscription OR creates new one.
   Updates inline message button to ✓ Активировано (callback → popup).
"""

from datetime import UTC, datetime

import structlog
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import InaccessibleMessage
from sqlalchemy import select

from app.config import settings
from app.database.crud.subscription import (
    create_paid_subscription,
    extend_subscription,
    get_subscription_by_user_id,
)
from app.database.crud.user import get_user_by_telegram_id
from app.database.database import AsyncSessionLocal
from app.database.models import InlineGiftSubscription
from app.localization.loader import DEFAULT_LANGUAGE
from app.localization.texts import get_texts
from app.services.subscription_service import SubscriptionService


logger = structlog.get_logger(__name__)


def _days_label(days: int, texts) -> str:
    if days % 10 == 1 and days % 100 != 11:
        return texts.t('INLINE_GIFT_DAYS_ONE', '{n} день').format(n=days)
    if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        return texts.t('INLINE_GIFT_DAYS_FEW', '{n} дня').format(n=days)
    return texts.t('INLINE_GIFT_DAYS_MANY', '{n} дней').format(n=days)


def _fmt_traffic(gb: int, texts) -> str:
    if gb == 0:
        return texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит')
    return texts.t('INLINE_GIFT_TRAFFIC_GB', '{gb} ГБ').format(gb=gb)


def _build_info_text(
    gift_days: int,
    gift_traffic_gb: int,
    gift_devices: int,
    texts,
    existing_sub=None,
) -> str:
    """Build blockquote info. Only shows params that actually change (non-zero gift values).
    gift_days/traffic/devices == 0 means "no change for this param".
    """
    lines = []

    if existing_sub is None:
        # New user — show gift params that are non-zero
        if gift_days:
            lines.append(
                f'{texts.t("INLINE_GIFT_EMOJI_CALENDAR", "<tg-emoji emoji-id=\'5967412305338568701\'>📅</tg-emoji>")} '
                f'{texts.t("INLINE_GIFT_LABEL_DURATION", "Срок")}: '
                f'<b>{_days_label(gift_days, texts)}</b>'
            )
        if gift_traffic_gb:
            lines.append(
                f'{texts.t("INLINE_GIFT_EMOJI_TRAFFIC", "<tg-emoji emoji-id=\'5931472654660800739\'>📊</tg-emoji>")} '
                f'{texts.t("INLINE_GIFT_LABEL_TRAFFIC", "Трафик")}: '
                f'<b>{_fmt_traffic(gift_traffic_gb, texts)}</b>'
            )
        if gift_devices:
            lines.append(
                f'{texts.t("INLINE_GIFT_EMOJI_DEVICES", "<tg-emoji emoji-id=\'5877318502947229960\'>💻</tg-emoji>")} '
                f'{texts.t("INLINE_GIFT_LABEL_DEVICES", "Устройств")}: '
                f'<b>{gift_devices}</b>'
            )
    else:
        cur_days = max(0, existing_sub.days_left) if hasattr(existing_sub, 'days_left') else 0
        cur_traffic = existing_sub.traffic_limit_gb or 0
        cur_devices = existing_sub.device_limit or 1

        if gift_days:
            new_days = cur_days + gift_days
            lines.append(
                f'{texts.t("INLINE_GIFT_EMOJI_CALENDAR", "<tg-emoji emoji-id=\'5967412305338568701\'>📅</tg-emoji>")} '
                f'{texts.t("INLINE_GIFT_LABEL_DURATION", "Срок")}: '
                f'<b>{_days_label(cur_days, texts)}</b> +{gift_days}'
                f'{texts.t("INLINE_GIFT_DAYS_SUFFIX", " дн.")} → '
                f'<b>{_days_label(new_days, texts)}</b>'
            )

        if gift_traffic_gb:
            if cur_traffic == 0:
                # currently unlimited, no meaningful change to show
                pass
            else:
                t_delta = gift_traffic_gb - cur_traffic
                sign = '+' if t_delta >= 0 else ''
                lines.append(
                    f'{texts.t("INLINE_GIFT_EMOJI_TRAFFIC", "<tg-emoji emoji-id=\'5931472654660800739\'>📊</tg-emoji>")} '
                    f'{texts.t("INLINE_GIFT_LABEL_TRAFFIC", "Трафик")}: '
                    f'<b>{_fmt_traffic(cur_traffic, texts)}</b> {sign}{t_delta} → '
                    f'<b>{_fmt_traffic(gift_traffic_gb, texts)}</b>'
                )

        if gift_devices:
            new_devices = max(cur_devices, gift_devices)
            if new_devices != cur_devices:
                d_delta = new_devices - cur_devices
                lines.append(
                    f'{texts.t("INLINE_GIFT_EMOJI_DEVICES", "<tg-emoji emoji-id=\'5877318502947229960\'>💻</tg-emoji>")} '
                    f'{texts.t("INLINE_GIFT_LABEL_DEVICES", "Устройств")}: '
                    f'<b>{cur_devices}</b> +{d_delta} → <b>{new_devices}</b>'
                )
            else:
                lines.append(
                    f'{texts.t("INLINE_GIFT_EMOJI_DEVICES", "<tg-emoji emoji-id=\'5877318502947229960\'>💻</tg-emoji>")} '
                    f'{texts.t("INLINE_GIFT_LABEL_DEVICES", "Устройств")}: '
                    f'<b>{cur_devices}</b>'
                )

    body = '\n'.join(lines) if lines else '—'
    return (
        f'{texts.t("INLINE_GIFT_EMOJI_GIFT", "<tg-emoji emoji-id=\'6032937473162614352\'>🎁</tg-emoji>")} '
        f'<b>{texts.t("INLINE_GIFT_TITLE", "Подарочная подписка")}</b>\n\n'
        f'<blockquote>{body}</blockquote>\n\n'
        f'{texts.t("INLINE_GIFT_CONFIRM_PROMPT", "Хотите активировать?")}'
    )


async def handle_gift_deeplink(message: types.Message, gift_code: str) -> bool:
    """Called from start.py with the bare gift code. Returns True if consumed."""
    telegram_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InlineGiftSubscription).where(
                InlineGiftSubscription.gift_code == gift_code
            )
        )
        gift = result.scalars().first()

        if not gift:
            return False

        user = await get_user_by_telegram_id(session, telegram_id)
        language = user.language if user else DEFAULT_LANGUAGE
        texts = get_texts(language)

        if gift.is_activated:
            await message.answer(texts.t('INLINE_GIFT_ALREADY_ACTIVATED', 'Этот подарок уже был активирован.'))
            return True

        raw_stored = gift.inline_message_id or ''
        intended_username = raw_stored[2:] if raw_stored.startswith('u:') else ''

        if gift.recipient_telegram_id and gift.recipient_telegram_id != 0:
            if telegram_id != gift.recipient_telegram_id:
                await message.answer(texts.t('INLINE_GIFT_WRONG_RECIPIENT', '🚫 Этот подарок предназначен другому пользователю.'))
                return True
        else:
            from_username = (message.from_user.username or '').lower()
            if not intended_username or intended_username.lower() != from_username:
                await message.answer(texts.t('INLINE_GIFT_WRONG_RECIPIENT', '🚫 Этот подарок предназначен другому пользователю.'), parse_mode='HTML')
                return True
            gift.recipient_telegram_id = telegram_id
            await session.commit()
            await session.refresh(gift)

        days = gift.days
        traffic_gb = gift.traffic_limit_gb
        devices = gift.device_limit

        existing_sub = await get_subscription_by_user_id(session, user.id) if user else None

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[
            types.InlineKeyboardButton(
                text=texts.t('INLINE_GIFT_ACTIVATE_BUTTON', 'Активировать'),
                callback_data=f'igift_activate:{gift_code}',
            ),
            types.InlineKeyboardButton(
                text=texts.t('INLINE_GIFT_CANCEL_BUTTON', 'Отменить'),
                callback_data=f'igift_cancel:{gift_code}',
            ),
        ]]
    )

    text_out = _build_info_text(days, traffic_gb, devices, texts, existing_sub)
    await message.answer(text_out, reply_markup=keyboard, parse_mode='HTML')
    return True


async def handle_activate_callback(callback: types.CallbackQuery) -> None:
    if isinstance(callback.message, InaccessibleMessage):
        texts = get_texts(DEFAULT_LANGUAGE)
        await callback.answer(texts.t('INLINE_GIFT_EXPIRED_MESSAGE', 'Сообщение устарело.'), show_alert=True)
        return

    gift_code = callback.data.split(':', 1)[1]
    telegram_id = callback.from_user.id

    await callback.answer()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(InlineGiftSubscription).where(
                InlineGiftSubscription.gift_code == gift_code
            )
        )
        gift = result.scalars().first()

        user = await get_user_by_telegram_id(db, telegram_id)
        language = user.language if user else DEFAULT_LANGUAGE
        texts = get_texts(language)

        if not gift:
            await callback.message.edit_text(texts.t('INLINE_GIFT_NOT_FOUND', 'Подарок не найден.'))
            return

        if gift.is_activated:
            await callback.message.edit_text(texts.t('INLINE_GIFT_ALREADY_ACTIVATED', 'Этот подарок уже был активирован.'))
            return

        if gift.recipient_telegram_id and gift.recipient_telegram_id != 0:
            if telegram_id != gift.recipient_telegram_id:
                await callback.answer()
                await callback.message.edit_text(
                    texts.t('INLINE_GIFT_WRONG_RECIPIENT', '🚫 Этот подарок предназначен другому пользователю.'),
                    parse_mode='HTML',
                )
                return
        else:
            # recipient_telegram_id == 0: проверяем по username из inline_message_id
            raw_stored = gift.inline_message_id or ''
            intended_username = raw_stored[2:] if raw_stored.startswith('u:') else ''
            from_username = (callback.from_user.username or '').lower()
            if not intended_username or intended_username.lower() != from_username:
                await callback.answer()
                await callback.message.edit_text(
                    texts.t('INLINE_GIFT_WRONG_RECIPIENT', '🚫 Этот подарок предназначен другому пользователю.'),
                    parse_mode='HTML',
                )
                return
            # Сохраняем telegram_id чтобы больше не проверять по username
            gift.recipient_telegram_id = telegram_id
            await db.commit()
            await db.refresh(gift)

        if not user:
            await callback.message.edit_text(texts.t('INLINE_GIFT_NOT_REGISTERED', 'Вы не зарегистрированы в боте. Напишите /start для регистрации.'))
            return

        await callback.message.edit_text(texts.t('INLINE_GIFT_ACTIVATING', '⏳ Активируем подарок...'))

        try:
            squads: list[str] = []
            from app.database.crud.server_squad import get_available_server_squads

            available = await get_available_server_squads(db)
            if available:
                squads = [available[0].squad_uuid]

            existing_sub = await get_subscription_by_user_id(db, user.id)
            subscription_service = SubscriptionService()

            if existing_sub:
                new_traffic = gift.traffic_limit_gb or None  # 0 = don't change
                new_devices_val = gift.device_limit or 0
                cur_devices = existing_sub.device_limit or 1
                new_devices = max(cur_devices, new_devices_val) if new_devices_val else None

                subscription = await extend_subscription(
                    db=db,
                    subscription=existing_sub,
                    days=gift.days,
                    traffic_limit_gb=new_traffic if new_traffic and new_traffic != existing_sub.traffic_limit_gb else None,
                    device_limit=new_devices if new_devices and new_devices != existing_sub.device_limit else None,
                    connected_squads=squads if squads else None,
                    commit=False,
                )
                await subscription_service.update_remnawave_user(db, subscription)
            else:
                subscription = await create_paid_subscription(
                    db=db,
                    user_id=user.id,
                    duration_days=gift.days or 0,
                    traffic_limit_gb=gift.traffic_limit_gb or settings.DEFAULT_TRAFFIC_LIMIT_GB,
                    device_limit=gift.device_limit or settings.DEFAULT_DEVICE_LIMIT,
                    connected_squads=squads,
                    update_server_counters=True,
                )
                await subscription_service.create_remnawave_user(db, subscription)

            await db.refresh(subscription)

            gift.is_activated = True
            gift.activated_at = datetime.now(UTC)
            gift.activated_by_user_id = user.id
            gift.subscription_id = subscription.id
            inline_msg_id = gift.inline_message_id
            await db.commit()

        except Exception as exc:
            logger.exception('Failed to activate inline gift', gift_code=gift_code, error=str(exc))
            await callback.message.edit_text(texts.t('INLINE_GIFT_ACTIVATION_ERROR', 'Произошла ошибка при активации подарка. Попробуйте позже.'))
            return

        days_str = _days_label(gift.days, texts) if gift.days else ''
        traffic_str = _fmt_traffic(gift.traffic_limit_gb, texts) if gift.traffic_limit_gb else ''
        devices_val = gift.device_limit or 0

        parts = []
        if days_str:
            parts.append(texts.t('INLINE_GIFT_SUCCESS_PART_DAYS', '+{days_str}').format(days_str=days_str))
        if traffic_str:
            parts.append(texts.t('INLINE_GIFT_SUCCESS_PART_TRAFFIC', '{traffic_str}').format(traffic_str=traffic_str))
        if devices_val:
            parts.append(texts.t('INLINE_GIFT_SUCCESS_PART_DEVICES', '{n} уст.').format(n=devices_val))

        if parts:
            changes = ', '.join(parts)
            success_text = texts.t(
                'INLINE_GIFT_SUCCESS_CHANGES',
                '✅ <b>Подарок активирован!</b>\n\n<blockquote>{changes}</blockquote>',
            ).format(changes=changes)
        else:
            success_text = texts.t(
                'INLINE_GIFT_SUCCESS',
                '✅ <b>Подарок активирован!</b>\n\nПодписка обновлена на вашем аккаунте.',
            )

        await callback.message.edit_text(success_text, parse_mode='HTML')

        # Update the inline message: replace URL button with noop callback button
        await _update_inline_button(
            callback.bot, inline_msg_id,
            texts.t('INLINE_GIFT_ACTIVATED_BUTTON', '✓ Активировано'),
        )


async def _update_inline_button(bot: Bot, inline_msg_id: str | None, text: str) -> None:
    """Replace the URL button in the shared inline message with a noop callback button."""
    if not inline_msg_id or inline_msg_id.startswith('u:'):
        return
    try:
        await bot.edit_message_reply_markup(
            inline_message_id=inline_msg_id,
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[
                    types.InlineKeyboardButton(text=text, callback_data='igift_noop')
                ]]
            ),
        )
    except Exception as e:
        logger.warning('Could not update inline message button', error=str(e))


async def handle_cancel_callback(callback: types.CallbackQuery) -> None:
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return
    await callback.answer()
    texts = get_texts(DEFAULT_LANGUAGE)
    gift_code = callback.data.split(':', 1)[1]

    inline_msg_id = None
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(InlineGiftSubscription).where(InlineGiftSubscription.gift_code == gift_code)
        )
        gift = result.scalars().first()
        if gift:
            inline_msg_id = gift.inline_message_id

    await callback.message.edit_text(texts.t('INLINE_GIFT_CANCELLED', 'Активация отменена.'))
    await _update_inline_button(
        callback.bot, inline_msg_id,
        texts.t('INLINE_GIFT_CANCELLED_BUTTON', '✗ Отменено'),
    )
    texts = get_texts(DEFAULT_LANGUAGE)
    await callback.answer(texts.t('INLINE_GIFT_ALREADY_ACTIVATED_ALERT', 'Подарок уже активирован.'), show_alert=True)


async def handle_noop_callback(callback: types.CallbackQuery) -> None:
    texts = get_texts(DEFAULT_LANGUAGE)
    await callback.answer(texts.t('INLINE_GIFT_ALREADY_ACTIVATED_ALERT', 'Подарок уже активирован.'), show_alert=True)


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(handle_activate_callback, F.data.startswith('igift_activate:'))
    dp.callback_query.register(handle_cancel_callback, F.data.startswith('igift_cancel:'))
    dp.callback_query.register(handle_noop_callback, F.data == 'igift_noop')
