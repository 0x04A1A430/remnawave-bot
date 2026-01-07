"""Покупка подписки по тарифам."""
import logging
from typing import List, Optional

from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.tariff import get_tariffs_for_user, get_tariff_by_id
from app.database.crud.subscription import create_paid_subscription, get_subscription_by_user_id, extend_subscription
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.database.models import User, Tariff, TransactionType
from app.localization.texts import get_texts
from app.states import SubscriptionStates
from app.utils.decorators import error_handler
from app.services.subscription_service import SubscriptionService
from app.services.admin_notification_service import AdminNotificationService
from app.services.user_cart_service import user_cart_service
from app.utils.promo_offer import get_user_active_promo_discount_percent


logger = logging.getLogger(__name__)


def _format_traffic(gb: int) -> str:
    """Форматирует трафик."""
    if gb == 0:
        return "Безлимит"
    return f"{gb} ГБ"


def _format_price_kopeks(kopeks: int) -> str:
    """Форматирует цену из копеек в рубли."""
    rubles = kopeks / 100
    if rubles == int(rubles):
        return f"{int(rubles)} ₽"
    return f"{rubles:.2f} ₽"


def _format_period(days: int) -> str:
    """Форматирует период."""
    if days == 1:
        return "1 день"
    elif days < 5:
        return f"{days} дня"
    elif days < 21 or days % 10 >= 5 or days % 10 == 0:
        return f"{days} дней"
    elif days % 10 == 1:
        return f"{days} день"
    else:
        return f"{days} дня"


def _apply_promo_discount(price: int, discount_percent: int) -> int:
    """Применяет скидку промогруппы к цене."""
    if discount_percent <= 0:
        return price
    discount = int(price * discount_percent / 100)
    return max(0, price - discount)


def get_tariffs_keyboard(
    tariffs: List[Tariff],
    language: str,
    discount_percent: int = 0,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру выбора тарифов."""
    texts = get_texts(language)
    buttons = []

    for tariff in tariffs:
        # Берем минимальную цену для отображения
        prices = tariff.period_prices or {}
        if prices:
            min_period = min(prices.keys(), key=int)
            min_price = prices[min_period]
            if discount_percent > 0:
                min_price = _apply_promo_discount(min_price, discount_percent)
            price_text = f"от {_format_price_kopeks(min_price)}"
        else:
            price_text = ""

        traffic = _format_traffic(tariff.traffic_limit_gb)

        button_text = f"📦 {tariff.name} • {traffic} • {tariff.device_limit} уст. {price_text}"
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"tariff_select:{tariff.id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariff_periods_keyboard(
    tariff: Tariff,
    language: str,
    discount_percent: int = 0,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру выбора периода для тарифа."""
    texts = get_texts(language)
    buttons = []

    prices = tariff.period_prices or {}
    for period_str in sorted(prices.keys(), key=int):
        period = int(period_str)
        price = prices[period_str]

        if discount_percent > 0:
            original_price = price
            price = _apply_promo_discount(price, discount_percent)
            price_text = f"{_format_price_kopeks(price)} (было {_format_price_kopeks(original_price)})"
        else:
            price_text = _format_price_kopeks(price)

        button_text = f"{_format_period(period)} — {price_text}"
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"tariff_period:{tariff.id}:{period}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="tariff_list")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariff_confirm_keyboard(
    tariff_id: int,
    period: int,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения покупки тарифа."""
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить покупку",
                callback_data=f"tariff_confirm:{tariff_id}:{period}"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.BACK,
                callback_data=f"tariff_select:{tariff_id}"
            )
        ]
    ])


def get_tariff_insufficient_balance_keyboard(
    tariff_id: int,
    period: int,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру при недостаточном балансе."""
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💳 Пополнить баланс",
                callback_data="balance_topup"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.BACK,
                callback_data=f"tariff_select:{tariff_id}"
            )
        ]
    ])


def format_tariff_info_for_user(
    tariff: Tariff,
    language: str,
    discount_percent: int = 0,
) -> str:
    """Форматирует информацию о тарифе для пользователя."""
    texts = get_texts(language)

    traffic = _format_traffic(tariff.traffic_limit_gb)

    text = f"""📦 <b>{tariff.name}</b>

<b>Параметры:</b>
• Трафик: {traffic}
• Устройств: {tariff.device_limit}
"""

    if tariff.description:
        text += f"\n📝 {tariff.description}\n"

    if discount_percent > 0:
        text += f"\n🎁 <b>Ваша скидка: {discount_percent}%</b>\n"

    text += "\nВыберите период подписки:"

    return text


@error_handler
async def show_tariffs_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает список тарифов для покупки."""
    texts = get_texts(db_user.language)
    await state.clear()

    # Получаем скидку пользователя
    discount_percent = 0
    promo_group = getattr(db_user, 'promo_group', None)
    if promo_group:
        # Используем скидку на серверы как общую скидку на тарифы
        discount_percent = getattr(promo_group, 'server_discount_percent', 0)

    # Также проверяем персональную скидку
    personal_discount = get_user_active_promo_discount_percent(db_user)
    if personal_discount > discount_percent:
        discount_percent = personal_discount

    # Получаем доступные тарифы
    promo_group_id = getattr(db_user, 'promo_group_id', None)
    tariffs = await get_tariffs_for_user(db, promo_group_id)

    if not tariffs:
        await callback.message.edit_text(
            "😔 <b>Нет доступных тарифов</b>\n\n"
            "К сожалению, сейчас нет тарифов для покупки.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    discount_text = ""
    if discount_percent > 0:
        discount_text = f"\n\n🎁 <b>Ваша скидка: {discount_percent}%</b>"

    await callback.message.edit_text(
        f"📦 <b>Выберите тариф</b>{discount_text}\n\n"
        "Выберите подходящий тариф из списка:",
        reply_markup=get_tariffs_keyboard(tariffs, db_user.language, discount_percent),
        parse_mode="HTML"
    )

    await state.update_data(tariff_discount_percent=discount_percent)
    await callback.answer()


@error_handler
async def select_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает выбор тарифа."""
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    data = await state.get_data()
    discount_percent = data.get('tariff_discount_percent', 0)

    await callback.message.edit_text(
        format_tariff_info_for_user(tariff, db_user.language, discount_percent),
        reply_markup=get_tariff_periods_keyboard(tariff, db_user.language, discount_percent),
        parse_mode="HTML"
    )

    await state.update_data(selected_tariff_id=tariff_id)
    await callback.answer()


@error_handler
async def select_tariff_period(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает выбор периода для тарифа."""
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    period = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    data = await state.get_data()
    discount_percent = data.get('tariff_discount_percent', 0)

    # Получаем цену
    prices = tariff.period_prices or {}
    base_price = prices.get(str(period), 0)
    final_price = _apply_promo_discount(base_price, discount_percent)

    # Проверяем баланс
    user_balance = db_user.balance_kopeks or 0

    traffic = _format_traffic(tariff.traffic_limit_gb)

    if user_balance >= final_price:
        # Показываем подтверждение
        discount_text = ""
        if discount_percent > 0:
            discount_text = f"\n🎁 Скидка: {discount_percent}% (-{_format_price_kopeks(base_price - final_price)})"

        await callback.message.edit_text(
            f"✅ <b>Подтверждение покупки</b>\n\n"
            f"📦 Тариф: <b>{tariff.name}</b>\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройств: {tariff.device_limit}\n"
            f"📅 Период: {_format_period(period)}\n"
            f"{discount_text}\n"
            f"💰 <b>Итого: {_format_price_kopeks(final_price)}</b>\n\n"
            f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
            f"После оплаты: {_format_price_kopeks(user_balance - final_price)}",
            reply_markup=get_tariff_confirm_keyboard(tariff_id, period, db_user.language),
            parse_mode="HTML"
        )
    else:
        # Недостаточно средств
        missing = final_price - user_balance
        await callback.message.edit_text(
            f"❌ <b>Недостаточно средств</b>\n\n"
            f"📦 Тариф: <b>{tariff.name}</b>\n"
            f"📅 Период: {_format_period(period)}\n"
            f"💰 Стоимость: {_format_price_kopeks(final_price)}\n\n"
            f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
            f"⚠️ Не хватает: <b>{_format_price_kopeks(missing)}</b>",
            reply_markup=get_tariff_insufficient_balance_keyboard(tariff_id, period, db_user.language),
            parse_mode="HTML"
        )

    await state.update_data(
        selected_tariff_id=tariff_id,
        selected_period=period,
        final_price=final_price,
    )
    await callback.answer()


@error_handler
async def confirm_tariff_purchase(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Подтверждает покупку тарифа и создает подписку."""
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    period = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    data = await state.get_data()
    discount_percent = data.get('tariff_discount_percent', 0)

    # Получаем цену
    prices = tariff.period_prices or {}
    base_price = prices.get(str(period), 0)
    final_price = _apply_promo_discount(base_price, discount_percent)

    # Проверяем баланс
    user_balance = db_user.balance_kopeks or 0
    if user_balance < final_price:
        await callback.answer("Недостаточно средств на балансе", show_alert=True)
        return

    texts = get_texts(db_user.language)

    try:
        # Списываем баланс
        success = await subtract_user_balance(
            db, db_user, final_price,
            f"Покупка тарифа {tariff.name} на {period} дней"
        )
        if not success:
            await callback.answer("Ошибка списания баланса", show_alert=True)
            return

        # Получаем список серверов из тарифа
        squads = tariff.allowed_squads or []

        # Проверяем есть ли уже подписка
        existing_subscription = await get_subscription_by_user_id(db, db_user.id)

        if existing_subscription:
            # Продлеваем существующую подписку и обновляем параметры тарифа
            subscription = await extend_subscription(
                db,
                existing_subscription,
                days=period,
                tariff_id=tariff.id,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
                connected_squads=squads,
            )
        else:
            # Создаем новую подписку
            subscription = await create_paid_subscription(
                db=db,
                user_id=db_user.id,
                duration_days=period,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
                connected_squads=squads,
                tariff_id=tariff.id,
            )

        # Обновляем пользователя в Remnawave
        try:
            subscription_service = SubscriptionService()
            await subscription_service.create_remnawave_user(
                db,
                subscription,
                reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
                reset_reason="покупка тарифа",
            )
        except Exception as e:
            logger.error(f"Ошибка обновления Remnawave: {e}")

        # Создаем транзакцию
        await create_transaction(
            db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=-final_price,
            description=f"Покупка тарифа {tariff.name} на {period} дней",
        )

        # Отправляем уведомление админу
        try:
            admin_notification_service = AdminNotificationService(callback.bot)
            await admin_notification_service.send_subscription_purchase_notification(
                db,
                db_user,
                subscription,
                final_price,
                period,
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу: {e}")

        # Очищаем корзину после успешной покупки
        try:
            await user_cart_service.delete_user_cart(db_user.id)
            logger.info(f"Корзина очищена после покупки тарифа для пользователя {db_user.telegram_id}")
        except Exception as e:
            logger.error(f"Ошибка очистки корзины: {e}")

        await state.clear()

        traffic = _format_traffic(tariff.traffic_limit_gb)

        await callback.message.edit_text(
            f"🎉 <b>Подписка успешно оформлена!</b>\n\n"
            f"📦 Тариф: <b>{tariff.name}</b>\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройств: {tariff.device_limit}\n"
            f"📅 Период: {_format_period(period)}\n"
            f"💰 Списано: {_format_price_kopeks(final_price)}\n\n"
            f"Перейдите в раздел «Подписка» для подключения.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")],
                [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer("Подписка оформлена!", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка при покупке тарифа: {e}", exc_info=True)
        await callback.answer("Произошла ошибка при оформлении подписки", show_alert=True)


# ==================== Продление по тарифу ====================

def get_tariff_extend_keyboard(
    tariff: Tariff,
    language: str,
    discount_percent: int = 0,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру выбора периода для продления по тарифу."""
    texts = get_texts(language)
    buttons = []

    prices = tariff.period_prices or {}
    for period_str in sorted(prices.keys(), key=int):
        period = int(period_str)
        price = prices[period_str]

        if discount_percent > 0:
            original_price = price
            price = _apply_promo_discount(price, discount_percent)
            price_text = f"{_format_price_kopeks(price)} (было {_format_price_kopeks(original_price)})"
        else:
            price_text = _format_price_kopeks(price)

        button_text = f"{_format_period(period)} — {price_text}"
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"tariff_extend:{tariff.id}:{period}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariff_extend_confirm_keyboard(
    tariff_id: int,
    period: int,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения продления по тарифу."""
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить продление",
                callback_data=f"tariff_ext_confirm:{tariff_id}:{period}"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.BACK,
                callback_data="subscription_extend"
            )
        ]
    ])


async def show_tariff_extend(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Показывает экран продления по текущему тарифу."""
    texts = get_texts(db_user.language)

    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not subscription or not subscription.tariff_id:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    tariff = await get_tariff_by_id(db, subscription.tariff_id)
    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    # Получаем скидку пользователя
    discount_percent = 0
    promo_group = getattr(db_user, 'promo_group', None)
    if promo_group:
        discount_percent = getattr(promo_group, 'server_discount_percent', 0)

    personal_discount = get_user_active_promo_discount_percent(db_user)
    if personal_discount > discount_percent:
        discount_percent = personal_discount

    traffic = _format_traffic(tariff.traffic_limit_gb)

    discount_text = ""
    if discount_percent > 0:
        discount_text = f"\n🎁 <b>Ваша скидка: {discount_percent}%</b>"

    await callback.message.edit_text(
        f"🔄 <b>Продление подписки</b>{discount_text}\n\n"
        f"📦 Тариф: <b>{tariff.name}</b>\n"
        f"📊 Трафик: {traffic}\n"
        f"📱 Устройств: {tariff.device_limit}\n\n"
        "Выберите период продления:",
        reply_markup=get_tariff_extend_keyboard(tariff, db_user.language, discount_percent),
        parse_mode="HTML"
    )
    await callback.answer()


@error_handler
async def select_tariff_extend_period(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает выбор периода для продления."""
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    period = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    # Получаем скидку пользователя
    discount_percent = 0
    promo_group = getattr(db_user, 'promo_group', None)
    if promo_group:
        discount_percent = getattr(promo_group, 'server_discount_percent', 0)

    personal_discount = get_user_active_promo_discount_percent(db_user)
    if personal_discount > discount_percent:
        discount_percent = personal_discount

    # Получаем цену
    prices = tariff.period_prices or {}
    base_price = prices.get(str(period), 0)
    final_price = _apply_promo_discount(base_price, discount_percent)

    # Проверяем баланс
    user_balance = db_user.balance_kopeks or 0

    traffic = _format_traffic(tariff.traffic_limit_gb)

    if user_balance >= final_price:
        discount_text = ""
        if discount_percent > 0:
            discount_text = f"\n🎁 Скидка: {discount_percent}% (-{_format_price_kopeks(base_price - final_price)})"

        await callback.message.edit_text(
            f"✅ <b>Подтверждение продления</b>\n\n"
            f"📦 Тариф: <b>{tariff.name}</b>\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройств: {tariff.device_limit}\n"
            f"📅 Период: {_format_period(period)}\n"
            f"{discount_text}\n"
            f"💰 <b>К оплате: {_format_price_kopeks(final_price)}</b>\n\n"
            f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
            f"После оплаты: {_format_price_kopeks(user_balance - final_price)}",
            reply_markup=get_tariff_extend_confirm_keyboard(tariff_id, period, db_user.language),
            parse_mode="HTML"
        )
    else:
        missing = final_price - user_balance
        await callback.message.edit_text(
            f"❌ <b>Недостаточно средств</b>\n\n"
            f"📦 Тариф: <b>{tariff.name}</b>\n"
            f"📅 Период: {_format_period(period)}\n"
            f"💰 К оплате: {_format_price_kopeks(final_price)}\n\n"
            f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
            f"⚠️ Не хватает: <b>{_format_price_kopeks(missing)}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="balance_topup")],
                [InlineKeyboardButton(text=texts.BACK, callback_data="subscription_extend")]
            ]),
            parse_mode="HTML"
        )

    await state.update_data(
        extend_tariff_id=tariff_id,
        extend_period=period,
        extend_discount_percent=discount_percent,
    )
    await callback.answer()


@error_handler
async def confirm_tariff_extend(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Подтверждает продление по тарифу."""
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    period = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not subscription:
        await callback.answer("Подписка не найдена", show_alert=True)
        return

    data = await state.get_data()
    discount_percent = data.get('extend_discount_percent', 0)

    # Получаем цену
    prices = tariff.period_prices or {}
    base_price = prices.get(str(period), 0)
    final_price = _apply_promo_discount(base_price, discount_percent)

    # Проверяем баланс
    user_balance = db_user.balance_kopeks or 0
    if user_balance < final_price:
        await callback.answer("Недостаточно средств на балансе", show_alert=True)
        return

    texts = get_texts(db_user.language)

    try:
        # Списываем баланс
        success = await subtract_user_balance(
            db, db_user, final_price,
            f"Продление тарифа {tariff.name} на {period} дней"
        )
        if not success:
            await callback.answer("Ошибка списания баланса", show_alert=True)
            return

        # Продлеваем подписку (параметры тарифа не меняются, только добавляется время)
        subscription = await extend_subscription(
            db,
            subscription,
            days=period,
        )

        # Обновляем пользователя в Remnawave
        try:
            subscription_service = SubscriptionService()
            await subscription_service.create_remnawave_user(
                db,
                subscription,
                reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
                reset_reason="продление тарифа",
            )
        except Exception as e:
            logger.error(f"Ошибка обновления Remnawave: {e}")

        # Создаем транзакцию
        await create_transaction(
            db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=-final_price,
            description=f"Продление тарифа {tariff.name} на {period} дней",
        )

        # Отправляем уведомление админу
        try:
            admin_notification_service = AdminNotificationService(callback.bot)
            await admin_notification_service.send_subscription_purchase_notification(
                db,
                db_user,
                subscription,
                final_price,
                period,
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу: {e}")

        # Очищаем корзину после успешной покупки
        try:
            await user_cart_service.delete_user_cart(db_user.id)
            logger.info(f"Корзина очищена после продления тарифа для пользователя {db_user.telegram_id}")
        except Exception as e:
            logger.error(f"Ошибка очистки корзины: {e}")

        await state.clear()

        traffic = _format_traffic(tariff.traffic_limit_gb)

        await callback.message.edit_text(
            f"🎉 <b>Подписка успешно продлена!</b>\n\n"
            f"📦 Тариф: <b>{tariff.name}</b>\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройств: {tariff.device_limit}\n"
            f"📅 Добавлено: {_format_period(period)}\n"
            f"💰 Списано: {_format_price_kopeks(final_price)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")],
                [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer("Подписка продлена!", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка при продлении тарифа: {e}", exc_info=True)
        await callback.answer("Произошла ошибка при продлении подписки", show_alert=True)


# ==================== Переключение тарифов ====================

def get_tariff_switch_keyboard(
    tariffs: List[Tariff],
    current_tariff_id: Optional[int],
    language: str,
    discount_percent: int = 0,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру выбора тарифа для переключения."""
    texts = get_texts(language)
    buttons = []

    for tariff in tariffs:
        # Пропускаем текущий тариф
        if tariff.id == current_tariff_id:
            continue

        prices = tariff.period_prices or {}
        if prices:
            min_period = min(prices.keys(), key=int)
            min_price = prices[min_period]
            if discount_percent > 0:
                min_price = _apply_promo_discount(min_price, discount_percent)
            price_text = f"от {_format_price_kopeks(min_price)}"
        else:
            price_text = ""

        traffic = _format_traffic(tariff.traffic_limit_gb)

        button_text = f"📦 {tariff.name} • {traffic} • {tariff.device_limit} уст. {price_text}"
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"tariff_sw_select:{tariff.id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariff_switch_periods_keyboard(
    tariff: Tariff,
    language: str,
    discount_percent: int = 0,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру выбора периода для переключения тарифа."""
    texts = get_texts(language)
    buttons = []

    prices = tariff.period_prices or {}
    for period_str in sorted(prices.keys(), key=int):
        period = int(period_str)
        price = prices[period_str]

        if discount_percent > 0:
            original_price = price
            price = _apply_promo_discount(price, discount_percent)
            price_text = f"{_format_price_kopeks(price)} (было {_format_price_kopeks(original_price)})"
        else:
            price_text = _format_price_kopeks(price)

        button_text = f"{_format_period(period)} — {price_text}"
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"tariff_sw_period:{tariff.id}:{period}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="tariff_switch")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariff_switch_confirm_keyboard(
    tariff_id: int,
    period: int,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения переключения тарифа."""
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить переключение",
                callback_data=f"tariff_sw_confirm:{tariff_id}:{period}"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.BACK,
                callback_data=f"tariff_sw_select:{tariff_id}"
            )
        ]
    ])


def get_tariff_switch_insufficient_balance_keyboard(
    tariff_id: int,
    period: int,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру при недостаточном балансе для переключения."""
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💳 Пополнить баланс",
                callback_data="balance_topup"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.BACK,
                callback_data=f"tariff_sw_select:{tariff_id}"
            )
        ]
    ])


@error_handler
async def show_tariff_switch_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает список тарифов для переключения."""
    texts = get_texts(db_user.language)
    await state.clear()

    # Проверяем наличие активной подписки
    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not subscription:
        await callback.answer("У вас нет активной подписки", show_alert=True)
        return

    current_tariff_id = subscription.tariff_id

    # Получаем скидку пользователя
    discount_percent = 0
    promo_group = getattr(db_user, 'promo_group', None)
    if promo_group:
        discount_percent = getattr(promo_group, 'server_discount_percent', 0)

    personal_discount = get_user_active_promo_discount_percent(db_user)
    if personal_discount > discount_percent:
        discount_percent = personal_discount

    # Получаем доступные тарифы
    promo_group_id = getattr(db_user, 'promo_group_id', None)
    tariffs = await get_tariffs_for_user(db, promo_group_id)

    # Фильтруем текущий тариф
    available_tariffs = [t for t in tariffs if t.id != current_tariff_id]

    if not available_tariffs:
        await callback.message.edit_text(
            "😔 <b>Нет доступных тарифов для переключения</b>\n\n"
            "Вы уже используете единственный доступный тариф.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    # Получаем текущий тариф для отображения
    current_tariff_name = "Неизвестно"
    if current_tariff_id:
        current_tariff = await get_tariff_by_id(db, current_tariff_id)
        if current_tariff:
            current_tariff_name = current_tariff.name

    discount_text = ""
    if discount_percent > 0:
        discount_text = f"\n\n🎁 <b>Ваша скидка: {discount_percent}%</b>"

    await callback.message.edit_text(
        f"📦 <b>Смена тарифа</b>{discount_text}\n\n"
        f"📌 Ваш текущий тариф: <b>{current_tariff_name}</b>\n\n"
        "⚠️ При смене тарифа оплачивается полная стоимость нового тарифа.\n"
        "Остаток времени текущей подписки будет сохранён.\n\n"
        "Выберите новый тариф:",
        reply_markup=get_tariff_switch_keyboard(tariffs, current_tariff_id, db_user.language, discount_percent),
        parse_mode="HTML"
    )

    await state.update_data(
        tariff_switch_discount_percent=discount_percent,
        current_tariff_id=current_tariff_id,
    )
    await callback.answer()


@error_handler
async def select_tariff_switch(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает выбор тарифа для переключения."""
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    data = await state.get_data()
    discount_percent = data.get('tariff_switch_discount_percent', 0)

    traffic = _format_traffic(tariff.traffic_limit_gb)

    info_text = f"""📦 <b>{tariff.name}</b>

<b>Параметры нового тарифа:</b>
• Трафик: {traffic}
• Устройств: {tariff.device_limit}
"""

    if tariff.description:
        info_text += f"\n📝 {tariff.description}\n"

    if discount_percent > 0:
        info_text += f"\n🎁 <b>Ваша скидка: {discount_percent}%</b>\n"

    info_text += "\n⚠️ Оплачивается полная стоимость тарифа.\nВыберите период:"

    await callback.message.edit_text(
        info_text,
        reply_markup=get_tariff_switch_periods_keyboard(tariff, db_user.language, discount_percent),
        parse_mode="HTML"
    )

    await state.update_data(switch_tariff_id=tariff_id)
    await callback.answer()


@error_handler
async def select_tariff_switch_period(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает выбор периода для переключения тарифа."""
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    period = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    data = await state.get_data()
    discount_percent = data.get('tariff_switch_discount_percent', 0)
    current_tariff_id = data.get('current_tariff_id')

    # Получаем цену
    prices = tariff.period_prices or {}
    base_price = prices.get(str(period), 0)
    final_price = _apply_promo_discount(base_price, discount_percent)

    # Проверяем баланс
    user_balance = db_user.balance_kopeks or 0

    traffic = _format_traffic(tariff.traffic_limit_gb)

    # Получаем текущий тариф для отображения
    current_tariff_name = "Неизвестно"
    if current_tariff_id:
        current_tariff = await get_tariff_by_id(db, current_tariff_id)
        if current_tariff:
            current_tariff_name = current_tariff.name

    if user_balance >= final_price:
        discount_text = ""
        if discount_percent > 0:
            discount_text = f"\n🎁 Скидка: {discount_percent}% (-{_format_price_kopeks(base_price - final_price)})"

        await callback.message.edit_text(
            f"✅ <b>Подтверждение переключения тарифа</b>\n\n"
            f"📌 Текущий тариф: <b>{current_tariff_name}</b>\n"
            f"📦 Новый тариф: <b>{tariff.name}</b>\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройств: {tariff.device_limit}\n"
            f"📅 Добавляется период: {_format_period(period)}\n"
            f"{discount_text}\n"
            f"💰 <b>К оплате: {_format_price_kopeks(final_price)}</b>\n\n"
            f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
            f"После оплаты: {_format_price_kopeks(user_balance - final_price)}\n\n"
            f"⚠️ Остаток времени текущей подписки будет сохранён.",
            reply_markup=get_tariff_switch_confirm_keyboard(tariff_id, period, db_user.language),
            parse_mode="HTML"
        )
    else:
        missing = final_price - user_balance
        await callback.message.edit_text(
            f"❌ <b>Недостаточно средств</b>\n\n"
            f"📦 Тариф: <b>{tariff.name}</b>\n"
            f"📅 Период: {_format_period(period)}\n"
            f"💰 К оплате: {_format_price_kopeks(final_price)}\n\n"
            f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
            f"⚠️ Не хватает: <b>{_format_price_kopeks(missing)}</b>",
            reply_markup=get_tariff_switch_insufficient_balance_keyboard(tariff_id, period, db_user.language),
            parse_mode="HTML"
        )

    await state.update_data(
        switch_tariff_id=tariff_id,
        switch_period=period,
        switch_final_price=final_price,
    )
    await callback.answer()


@error_handler
async def confirm_tariff_switch(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Подтверждает переключение тарифа."""
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    period = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    data = await state.get_data()
    discount_percent = data.get('tariff_switch_discount_percent', 0)

    # Получаем цену
    prices = tariff.period_prices or {}
    base_price = prices.get(str(period), 0)
    final_price = _apply_promo_discount(base_price, discount_percent)

    # Проверяем баланс
    user_balance = db_user.balance_kopeks or 0
    if user_balance < final_price:
        await callback.answer("Недостаточно средств на балансе", show_alert=True)
        return

    # Проверяем наличие подписки
    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not subscription:
        await callback.answer("У вас нет активной подписки", show_alert=True)
        return

    texts = get_texts(db_user.language)

    try:
        # Списываем баланс
        success = await subtract_user_balance(
            db, db_user, final_price,
            f"Смена тарифа на {tariff.name} ({period} дней)"
        )
        if not success:
            await callback.answer("Ошибка списания баланса", show_alert=True)
            return

        # Получаем список серверов из тарифа
        squads = tariff.allowed_squads or []

        # Обновляем подписку с новыми параметрами тарифа и добавляем период
        subscription = await extend_subscription(
            db,
            subscription,
            days=period,
            tariff_id=tariff.id,
            traffic_limit_gb=tariff.traffic_limit_gb,
            device_limit=tariff.device_limit,
            connected_squads=squads,
        )

        # Обновляем пользователя в Remnawave
        try:
            subscription_service = SubscriptionService()
            await subscription_service.create_remnawave_user(
                db,
                subscription,
                reset_traffic=True,
                reset_reason="переключение тарифа",
            )
        except Exception as e:
            logger.error(f"Ошибка обновления Remnawave при переключении тарифа: {e}")

        # Создаем транзакцию
        await create_transaction(
            db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=-final_price,
            description=f"Переключение на тариф {tariff.name} на {period} дней",
        )

        # Отправляем уведомление админу
        try:
            admin_notification_service = AdminNotificationService(callback.bot)
            await admin_notification_service.send_subscription_purchase_notification(
                db,
                db_user,
                subscription,
                final_price,
                period,
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу: {e}")

        # Очищаем корзину после успешной покупки
        try:
            await user_cart_service.delete_user_cart(db_user.id)
            logger.info(f"Корзина очищена после смены тарифа для пользователя {db_user.telegram_id}")
        except Exception as e:
            logger.error(f"Ошибка очистки корзины: {e}")

        await state.clear()

        traffic = _format_traffic(tariff.traffic_limit_gb)

        await callback.message.edit_text(
            f"🎉 <b>Тариф успешно изменён!</b>\n\n"
            f"📦 Новый тариф: <b>{tariff.name}</b>\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройств: {tariff.device_limit}\n"
            f"📅 Добавлен период: {_format_period(period)}\n"
            f"💰 Списано: {_format_price_kopeks(final_price)}\n\n"
            f"Перейдите в раздел «Подписка» для просмотра деталей.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")],
                [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer("Тариф изменён!", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка при переключении тарифа: {e}", exc_info=True)
        await callback.answer("Произошла ошибка при переключении тарифа", show_alert=True)


def register_tariff_purchase_handlers(dp: Dispatcher):
    """Регистрирует обработчики покупки по тарифам."""
    # Список тарифов (для режима tariffs)
    dp.callback_query.register(show_tariffs_list, F.data == "tariff_list")
    dp.callback_query.register(show_tariffs_list, F.data == "buy_subscription_tariffs")

    # Выбор тарифа
    dp.callback_query.register(select_tariff, F.data.startswith("tariff_select:"))

    # Выбор периода
    dp.callback_query.register(select_tariff_period, F.data.startswith("tariff_period:"))

    # Подтверждение покупки
    dp.callback_query.register(confirm_tariff_purchase, F.data.startswith("tariff_confirm:"))

    # Продление по тарифу
    dp.callback_query.register(select_tariff_extend_period, F.data.startswith("tariff_extend:"))
    dp.callback_query.register(confirm_tariff_extend, F.data.startswith("tariff_ext_confirm:"))

    # Переключение тарифов
    dp.callback_query.register(show_tariff_switch_list, F.data == "tariff_switch")
    dp.callback_query.register(select_tariff_switch, F.data.startswith("tariff_sw_select:"))
    dp.callback_query.register(select_tariff_switch_period, F.data.startswith("tariff_sw_period:"))
    dp.callback_query.register(confirm_tariff_switch, F.data.startswith("tariff_sw_confirm:"))
