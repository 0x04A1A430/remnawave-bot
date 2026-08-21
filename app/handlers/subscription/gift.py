"""Native Telegram handlers for subscription gifting catalog, period selection, and navigation."""

from __future__ import annotations

import html
import uuid

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InaccessibleMessage, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.handlers.subscription.purchase import show_subscription_info
from app.localization.texts import get_texts
from app.services.gift_notification_service import (
    build_gift_result_presentation,
    resolve_gift_claim_channel,
)
from app.services.gift_purchase_service import (
    GiftError,
    GiftFeatureDisabledError,
    GiftInsufficientBalanceError,
    GiftPeriodUnavailableError,
    GiftPriceChangedError,
    GiftPurchaseRestrictedError,
    GiftQuote,
    GiftTariffOffer,
    GiftTariffUnavailableError,
    is_gift_enabled,
    list_gift_offers,
    purchase_gift_from_balance,
    quote_gift_purchase,
)
from app.states import GiftPurchaseStates


logger = structlog.get_logger(__name__)


# ── Render Helpers ──────────────────────────────────────────────────────────


def _render_tariff_catalog(db_user: User, offers: list[GiftTariffOffer]) -> tuple[str, InlineKeyboardMarkup]:
    """Render gift tariff catalog message and keyboard."""
    texts = get_texts(db_user.language)
    text = texts.t(
        'GIFT_CATALOG_TITLE',
        '🎁 <b>Подарить подписку</b>\n\nВыберите тариф для подарка:',
    )

    buttons: list[list[InlineKeyboardButton]] = []
    for offer in offers:
        tariff_label = texts.t('GIFT_TARIFF_CHOICE_BUTTON', '{tariff_name}').format(tariff_name=offer.tariff_name)
        buttons.append([InlineKeyboardButton(text=tariff_label, callback_data=f'gift_tariff:{offer.tariff_id}')])

    buttons.append([InlineKeyboardButton(text=texts.t('GIFT_CANCEL_BUTTON', '❌ Отмена'), callback_data='gift_cancel')])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def _render_period_selection(db_user: User, offer: GiftTariffOffer) -> tuple[str, InlineKeyboardMarkup]:
    """Render gift period selection message and keyboard with HTML escaping."""
    texts = get_texts(db_user.language)
    escaped_name = html.escape(offer.tariff_name)
    escaped_desc = html.escape(offer.tariff_description) + '\n\n' if offer.tariff_description else ''
    traffic_str = (
        texts.format_traffic(offer.traffic_limit_gb)
        if offer.traffic_limit_gb is not None
        else texts.t('GIFT_TRAFFIC_UNLIMITED', '∞ (безлимит)')
    )
    devices_str = texts.format_device_limit(offer.device_limit)

    text = texts.t(
        'GIFT_SELECT_PERIOD_TITLE',
        '🎁 <b>Подарок: {tariff_name}</b>\n\n{description}📊 Трафик: <b>{traffic}</b>\n📱 Устройства: <b>{devices}</b>\n\nВыберите период:',
    ).format(
        tariff_name=escaped_name,
        description=escaped_desc,
        traffic=traffic_str,
        devices=devices_str,
    )

    buttons: list[list[InlineKeyboardButton]] = []
    for quote in offer.quotes:
        price_str = texts.format_price(quote.final_price_kopeks)
        if quote.discount_percent > 0:
            btn_text = texts.t('GIFT_PERIOD_DISCOUNT_BUTTON', '{days} дн. — {price} (-{discount}%)').format(
                days=quote.period_days, price=price_str, discount=quote.discount_percent
            )
        else:
            btn_text = texts.t('GIFT_PERIOD_CHOICE_BUTTON', '{days} дн. — {price}').format(
                days=quote.period_days, price=price_str
            )

        buttons.append(
            [InlineKeyboardButton(text=btn_text, callback_data=f'gift_period:{offer.tariff_id}:{quote.period_days}')]
        )

    nav_row = [
        InlineKeyboardButton(
            text=texts.t('GIFT_BACK_TO_TARIFFS_BUTTON', '◀️ К тарифам'),
            callback_data='gift_back_tariffs',
        ),
        InlineKeyboardButton(
            text=texts.t('GIFT_CANCEL_BUTTON', '❌ Отмена'),
            callback_data='gift_cancel',
        ),
    ]
    buttons.append(nav_row)

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def _render_confirmation_summary(db_user: User, quote: GiftQuote) -> tuple[str, InlineKeyboardMarkup]:
    """Render gift purchase confirmation summary message and keyboard with HTML escaping."""
    texts = get_texts(db_user.language)
    escaped_name = html.escape(quote.tariff_name)
    traffic_str = (
        texts.format_traffic(quote.traffic_limit_gb)
        if quote.traffic_limit_gb is not None
        else texts.t('GIFT_TRAFFIC_UNLIMITED', '∞ (безлимит)')
    )
    devices_str = texts.format_device_limit(quote.device_limit)
    balance_str = texts.format_price(db_user.balance_kopeks)
    final_price_str = texts.format_price(quote.final_price_kopeks)

    price_details = ''
    if quote.total_discount_kopeks > 0:
        orig_price_str = texts.format_price(quote.original_price_kopeks)
        disc_str = texts.format_price(quote.total_discount_kopeks)
        price_details += texts.t('GIFT_PRICE_ORIGINAL_LINE', '💵 Исходная цена: <s>{original_price}</s>\n').format(
            original_price=orig_price_str
        )
        price_details += texts.t('GIFT_PRICE_DISCOUNT_LINE', '🏷 Скидка: <b>-{discount}</b>\n').format(discount=disc_str)
    price_details += texts.t('GIFT_PRICE_FINAL_LINE', '💰 Итого к оплате: <b>{final_price}</b>\n').format(
        final_price=final_price_str
    )

    text = texts.t(
        'GIFT_SUMMARY_TITLE',
        '🎁 <b>Подтверждение подарка</b>\n\n📦 Тариф: <b>{tariff_name}</b>\n📅 Период: <b>{period_days} дн.</b>\n📊 Трафик: <b>{traffic}</b>\n📱 Устройства: <b>{devices}</b>\n\n{price_details}💳 Ваш баланс: <b>{balance}</b>\n\nПосле подтверждения с вашего баланса будет списана указанная сумма и создана ссылка на подарок.',
    ).format(
        tariff_name=escaped_name,
        period_days=quote.period_days,
        traffic=traffic_str,
        devices=devices_str,
        price_details=price_details,
        balance=balance_str,
    )

    buttons = [
        [
            InlineKeyboardButton(
                text=texts.t('GIFT_CONFIRM_PURCHASE_BUTTON', '✅ Подтвердить покупку'),
                callback_data='gift_confirm',
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t('GIFT_BACK_TO_PERIODS_BUTTON', '◀️ К периодам'),
                callback_data='gift_back_periods',
            ),
            InlineKeyboardButton(
                text=texts.t('GIFT_CANCEL_BUTTON', '❌ Отмена'),
                callback_data='gift_cancel',
            ),
        ],
    ]

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Callback Handlers ───────────────────────────────────────────────────────


async def handle_gift_catalog(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Entry point for native gift catalog."""
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    texts = get_texts(db_user.language)
    if not await is_gift_enabled(db):
        await callback.answer(
            texts.t('GIFT_FEATURE_DISABLED', 'Покупка подарков временно недоступна.'),
            show_alert=True,
        )
        return

    offers = await list_gift_offers(db, buyer=db_user)
    if not offers:
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t('GIFT_CANCEL_BUTTON', '❌ Отмена'),
                        callback_data='gift_cancel',
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            texts.t('GIFT_NO_TARIFFS_AVAILABLE', 'В данный момент нет доступных тарифов для подарка.'),
            reply_markup=back_kb,
        )
        await callback.answer()
        return

    data = await state.get_data()
    checkout_id = data.get('gift_checkout_id') or uuid.uuid4().hex
    origin = data.get('gift_origin_callback') or (
        callback.data if callback.data != 'subscription_gift' else 'menu_subscription'
    )

    await state.set_state(GiftPurchaseStates.selecting_tariff)
    await state.update_data(
        gift_checkout_id=checkout_id,
        gift_origin_callback=origin,
    )

    text, keyboard = _render_tariff_catalog(db_user, offers)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


async def handle_gift_tariff_select(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle tariff selection in gift catalog."""
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    texts = get_texts(db_user.language)
    if not callback.data or not callback.data.startswith('gift_tariff:'):
        await callback.answer(texts.t('GIFT_INVALID_SELECTION', 'Некорректные параметры выбора.'), show_alert=True)
        return

    parts = callback.data.split(':', 1)
    if len(parts) != 2:
        await callback.answer(texts.t('GIFT_INVALID_SELECTION', 'Некорректные параметры выбора.'), show_alert=True)
        return

    try:
        tariff_id = int(parts[1])
    except ValueError:
        await callback.answer(texts.t('GIFT_INVALID_SELECTION', 'Некорректные параметры выбора.'), show_alert=True)
        return

    if not await is_gift_enabled(db):
        await callback.answer(
            texts.t('GIFT_FEATURE_DISABLED', 'Покупка подарков временно недоступна.'),
            show_alert=True,
        )
        return

    offers = await list_gift_offers(db, buyer=db_user)
    offer = next((o for o in offers if o.tariff_id == tariff_id), None)
    if offer is None or not offer.quotes:
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t('GIFT_BACK_TO_TARIFFS_BUTTON', '◀️ К тарифам'),
                        callback_data='gift_back_tariffs',
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            texts.t('GIFT_TARIFF_UNAVAILABLE', 'Выбранный тариф недоступен для подарка.'),
            reply_markup=back_kb,
        )
        await callback.answer()
        return

    data = await state.get_data()
    checkout_id = data.get('gift_checkout_id') or uuid.uuid4().hex
    await state.set_state(GiftPurchaseStates.selecting_period)
    await state.update_data(
        gift_checkout_id=checkout_id,
        gift_tariff_id=tariff_id,
    )

    text, keyboard = _render_period_selection(db_user, offer)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


async def handle_gift_period_select(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle period selection in gift flow and render confirmation summary."""
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    texts = get_texts(db_user.language)
    if not callback.data or not callback.data.startswith('gift_period:'):
        await callback.answer(texts.t('GIFT_INVALID_SELECTION', 'Некорректные параметры выбора.'), show_alert=True)
        return

    parts = callback.data.split(':')
    if len(parts) != 3:
        await callback.answer(texts.t('GIFT_INVALID_SELECTION', 'Некорректные параметры выбора.'), show_alert=True)
        return

    try:
        tariff_id = int(parts[1])
        period_days = int(parts[2])
    except ValueError:
        await callback.answer(texts.t('GIFT_INVALID_SELECTION', 'Некорректные параметры выбора.'), show_alert=True)
        return

    try:
        quote = await quote_gift_purchase(db, buyer=db_user, tariff_id=tariff_id, period_days=period_days)
    except GiftFeatureDisabledError:
        await callback.answer(
            texts.t('GIFT_FEATURE_DISABLED', 'Покупка подарков временно недоступна.'), show_alert=True
        )
        return
    except GiftTariffUnavailableError:
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t('GIFT_BACK_TO_TARIFFS_BUTTON', '◀️ К тарифам'),
                        callback_data='gift_back_tariffs',
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            texts.t('GIFT_TARIFF_UNAVAILABLE', 'Выбранный тариф недоступен для подарка.'),
            reply_markup=back_kb,
        )
        await callback.answer()
        return
    except GiftPeriodUnavailableError:
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t('GIFT_BACK_TO_PERIODS_BUTTON', '◀️ К периодам'),
                        callback_data='gift_back_periods',
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            texts.t('GIFT_PERIOD_UNAVAILABLE', 'Выбранный период недоступен.'),
            reply_markup=back_kb,
        )
        await callback.answer()
        return
    except GiftError as err:
        logger.warning('Gift quote calculation failed', error=str(err), tariff_id=tariff_id, period_days=period_days)
        await callback.answer(
            texts.t('GIFT_GENERIC_ERROR', 'Произошла ошибка при оформлении подарка. Попробуйте позже.'),
            show_alert=True,
        )
        return

    data = await state.get_data()
    checkout_id = data.get('gift_checkout_id') or uuid.uuid4().hex

    await state.set_state(GiftPurchaseStates.confirming_purchase)
    await state.update_data(
        gift_checkout_id=checkout_id,
        gift_tariff_id=tariff_id,
        gift_period_days=period_days,
        gift_expected_price_kopeks=quote.final_price_kopeks,
    )

    text, keyboard = _render_confirmation_summary(db_user, quote)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


async def handle_gift_back_tariffs(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Navigate back to tariff catalog."""
    await handle_gift_catalog(callback, db_user, db, state)


async def handle_gift_back_periods(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Navigate back to period selection for current tariff."""
    data = await state.get_data()
    tariff_id = data.get('gift_tariff_id')
    if tariff_id is None:
        await handle_gift_catalog(callback, db_user, db, state)
        return

    callback.data = f'gift_tariff:{tariff_id}'
    await handle_gift_tariff_select(callback, db_user, db, state)


async def handle_gift_cancel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Cancel gift checkout and return to origin subscription view."""
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    data = await state.get_data()
    origin = data.get('gift_origin_callback', 'menu_subscription')
    await state.clear()

    if origin == 'my_subscriptions' and settings.is_multi_tariff_enabled():
        from app.handlers.subscription.my_subscriptions import show_my_subscriptions

        await show_my_subscriptions(callback, db_user, db, state)
    else:
        await show_subscription_info(callback, db_user, db)


async def handle_gift_confirm(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Confirmation handler: validates selection, preflights channels, purchases from balance, and renders result."""
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    texts = get_texts(db_user.language)
    data = await state.get_data()
    tariff_id = data.get('gift_tariff_id')
    period_days = data.get('gift_period_days')
    expected_price_kopeks = data.get('gift_expected_price_kopeks')

    if not tariff_id or not period_days or expected_price_kopeks is None:
        await callback.answer(
            texts.t('GIFT_INVALID_SELECTION', 'Некорректные параметры выбора.'),
            show_alert=True,
        )
        return

    checkout_id = data.get('gift_checkout_id')
    if not checkout_id:
        checkout_id = uuid.uuid4().hex
        await state.update_data(gift_checkout_id=checkout_id)

    # Preflight claim channels before debiting
    bot_username, cabinet_url = await resolve_gift_claim_channel(bot=callback.bot)
    if not bot_username and not cabinet_url:
        await callback.answer(
            texts.t(
                'GIFT_NO_CLAIM_CHANNEL_ERROR',
                '❌ Сервис подарков временно недоступен: не настроен канал выдачи ссылки.',
            ),
            show_alert=True,
        )
        return

    try:
        result = await purchase_gift_from_balance(
            db=db,
            buyer_id=db_user.id,
            tariff_id=tariff_id,
            period_days=period_days,
            expected_price_kopeks=expected_price_kopeks,
            idempotency_key=checkout_id,
            source='bot',
        )
    except GiftPriceChangedError as err:
        # Update FSM with fresh price, retain checkout for re-confirmation
        await state.update_data(
            gift_expected_price_kopeks=err.fresh_quote.final_price_kopeks,
        )
        text, keyboard = _render_confirmation_summary(db_user, err.fresh_quote)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
        await callback.answer(
            texts.t(
                'GIFT_PRICE_CHANGED_ERROR',
                '⚠️ Цена на выбранный тариф изменилась. Пожалуйста, подтвердите покупку заново.',
            ),
            show_alert=True,
        )
        return
    except GiftInsufficientBalanceError as err:
        # Retain checkout intact for top-up flow (Task 6)
        req_str = texts.format_price(err.required_kopeks)
        avail_str = texts.format_price(err.available_kopeks)
        msg = texts.t(
            'GIFT_INSUFFICIENT_BALANCE_ERROR',
            '❌ Недостаточно средств на балансе. Требуется: {required}, доступно: {available}.',
        ).format(required=req_str, available=avail_str)
        await callback.answer(msg, show_alert=True)
        return
    except GiftPurchaseRestrictedError:
        await state.clear()
        await callback.answer(
            texts.t('GIFT_PURCHASE_RESTRICTED_ERROR', '❌ Покупка подписок недоступна для вашего аккаунта.'),
            show_alert=True,
        )
        return
    except GiftFeatureDisabledError:
        await state.clear()
        await callback.answer(
            texts.t('GIFT_FEATURE_DISABLED', 'Покупка подарков временно недоступна.'),
            show_alert=True,
        )
        return
    except GiftTariffUnavailableError:
        await state.clear()
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t('GIFT_CANCEL_BUTTON', '❌ Отмена'),
                        callback_data='gift_cancel',
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            texts.t('GIFT_TARIFF_UNAVAILABLE', 'Выбранный тариф недоступен для подарка.'),
            reply_markup=back_kb,
        )
        await callback.answer()
        return
    except GiftPeriodUnavailableError:
        await state.clear()
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t('GIFT_BACK_TO_TARIFFS_BUTTON', '◀️ К тарифам'),
                        callback_data='gift_back_tariffs',
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            texts.t('GIFT_PERIOD_UNAVAILABLE', 'Выбранный период недоступен.'),
            reply_markup=back_kb,
        )
        await callback.answer()
        return
    except GiftError as err:
        logger.error('Unexpected gift domain failure', buyer_id=db_user.id, error=str(err))
        await callback.answer(
            texts.t('GIFT_GENERIC_ERROR', 'Произошла ошибка при оформлении подарка. Попробуйте позже.'),
            show_alert=True,
        )
        return
    except Exception as err:
        logger.error('Unhandled error in gift confirmation', buyer_id=db_user.id, error=str(err), exc_info=True)
        await callback.answer(
            texts.t('GIFT_GENERIC_ERROR', 'Произошла ошибка при оформлении подарка. Попробуйте позже.'),
            show_alert=True,
        )
        return

    text, keyboard = build_gift_result_presentation(
        language=db_user.language,
        purchase_result=result,
        bot_username=bot_username,
        cabinet_url=cabinet_url,
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()
    await state.clear()


async def handle_return_to_gift_cart(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    """Resume gift cart after balance top-up (Task 6)."""
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return
    await callback.answer()


# ── Handler Registration ───────────────────────────────────────────────────


def register_gift_handlers(dp: Dispatcher) -> None:
    """Register all gift purchase and navigation callback handlers."""
    dp.callback_query.register(handle_gift_catalog, F.data == 'subscription_gift')
    dp.callback_query.register(handle_gift_tariff_select, F.data.startswith('gift_tariff:'))
    dp.callback_query.register(handle_gift_period_select, F.data.startswith('gift_period:'))
    dp.callback_query.register(handle_gift_back_tariffs, F.data == 'gift_back_tariffs')
    dp.callback_query.register(handle_gift_back_periods, F.data == 'gift_back_periods')
    dp.callback_query.register(handle_gift_cancel, F.data == 'gift_cancel')
    dp.callback_query.register(handle_gift_confirm, F.data == 'gift_confirm')
    dp.callback_query.register(handle_return_to_gift_cart, F.data == 'return_to_gift_cart')
