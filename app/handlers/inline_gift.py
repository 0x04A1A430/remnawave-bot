"""Handler for inline gift subscription deep-links and activation.

Flow:
1. Admin sends inline message with a deep-link button.
2. Recipient clicks "Активировать" → lands in the bot with /start <code>
3. Bot verifies recipient is the intended user (by telegram_id or @username).
4. Shows gift info with [Активировать] / [Отменить] buttons.
5. On activation: creates subscription in DB + Remnawave, updates inline button to ✓ Активировано.
"""

from datetime import UTC, datetime

import structlog
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import InaccessibleMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.subscription import create_paid_subscription
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


async def handle_gift_deeplink(message: types.Message, gift_code: str) -> bool:
    """Called from start.py with the bare gift code (no prefix).

    Returns True if handled (consumed), False if no gift found with this code.
    """
    telegram_id = message.from_user.id
    language = DEFAULT_LANGUAGE

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InlineGiftSubscription).where(
                InlineGiftSubscription.gift_code == gift_code
            )
        )
        gift = result.scalars().first()

        if not gift:
            return False

        texts = get_texts(language)

        if gift.is_activated:
            await message.answer(texts.t('INLINE_GIFT_ALREADY_ACTIVATED', 'Этот подарок уже был активирован.'))
            return True

        # inline_message_id stores "u:<username>" until chosen_inline_result overwrites it.
        raw_stored = gift.inline_message_id or ''
        intended_username = raw_stored[2:] if raw_stored.startswith('u:') else ''

        # Verify by telegram_id (most reliable)
        if gift.recipient_telegram_id and gift.recipient_telegram_id != 0:
            if telegram_id != gift.recipient_telegram_id:
                await message.answer(texts.t('INLINE_GIFT_WRONG_RECIPIENT', '🚫 Этот подарок предназначен другому пользователю.'))
                return True
        else:
            # recipient_telegram_id unknown — fall back to @username check
            from_username = (message.from_user.username or '').lower()
            if intended_username and intended_username.lower() != from_username:
                await message.answer(texts.t('INLINE_GIFT_WRONG_RECIPIENT', '🚫 Этот подарок предназначен другому пользователю.'))
                return True
            gift.recipient_telegram_id = telegram_id
            await session.commit()
            await session.refresh(gift)

        days = gift.days
        traffic_limit_gb = gift.traffic_limit_gb
        device_limit = gift.device_limit

    texts = get_texts(language)
    days_str = _days_label(days, texts)
    traffic = (
        texts.t('INLINE_GIFT_TRAFFIC_GB', '{gb} ГБ').format(gb=traffic_limit_gb)
        if traffic_limit_gb
        else texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит')
    )
    devices = device_limit or 1

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('INLINE_GIFT_ACTIVATE_BUTTON', 'Активировать'),
                    callback_data=f'igift_activate:{gift_code}',
                ),
                types.InlineKeyboardButton(
                    text=texts.t('INLINE_GIFT_CANCEL_BUTTON', 'Отменить'),
                    callback_data=f'igift_cancel:{gift_code}',
                ),
            ]
        ]
    )

    text_out = texts.t(
        'INLINE_GIFT_INFO_MESSAGE',
        '🎁 <b>Подарочная подписка</b>\n\n'
        '<blockquote>🗓 Срок: <b>{days_str}</b>\n'
        '📶 Трафик: <b>{traffic}</b>\n'
        '📱 Устройств: <b>{devices}</b></blockquote>\n\n'
        'Хотите активировать?',
    ).format(days_str=days_str, traffic=traffic, devices=devices)

    await message.answer(text_out, reply_markup=keyboard, parse_mode='HTML')
    return True


async def handle_activate_callback(callback: types.CallbackQuery) -> None:
    """Handle igift_activate:<gift_code> callback."""
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
                await callback.answer(
                    texts.t('INLINE_GIFT_WRONG_RECIPIENT', '🚫 Этот подарок предназначен другому пользователю.'),
                    show_alert=True,
                )
                return

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

            subscription = await create_paid_subscription(
                db=db,
                user_id=user.id,
                duration_days=gift.days,
                traffic_limit_gb=gift.traffic_limit_gb or settings.DEFAULT_TRAFFIC_LIMIT_GB,
                device_limit=gift.device_limit or settings.DEFAULT_DEVICE_LIMIT,
                connected_squads=squads,
                update_server_counters=True,
            )

            subscription_service = SubscriptionService()
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

        days_str = _days_label(gift.days, texts)
        await callback.message.edit_text(
            texts.t('INLINE_GIFT_SUCCESS', '✅ <b>Подарок активирован!</b>\n\nПодписка на <b>{days_str}</b> добавлена на ваш аккаунт.').format(days_str=days_str),
            parse_mode='HTML',
        )

        # Update the inline message button to ✓ Активировано
        if inline_msg_id and not inline_msg_id.startswith('u:'):
            try:
                bot: Bot = callback.bot
                await bot.edit_message_reply_markup(
                    inline_message_id=inline_msg_id,
                    reply_markup=types.InlineKeyboardMarkup(
                        inline_keyboard=[
                            [types.InlineKeyboardButton(
                                text=texts.t('INLINE_GIFT_ACTIVATED_BUTTON', '✓ Активировано'),
                                callback_data='igift_noop',
                            )]
                        ]
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
    await callback.message.edit_text(texts.t('INLINE_GIFT_CANCELLED', 'Активация отменена.'))


async def handle_noop_callback(callback: types.CallbackQuery) -> None:
    texts = get_texts(DEFAULT_LANGUAGE)
    await callback.answer(texts.t('INLINE_GIFT_ALREADY_ACTIVATED_ALERT', 'Подарок уже активирован.'), show_alert=True)


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(handle_activate_callback, F.data.startswith('igift_activate:'))
    dp.callback_query.register(handle_cancel_callback, F.data.startswith('igift_cancel:'))
    dp.callback_query.register(handle_noop_callback, F.data == 'igift_noop')
