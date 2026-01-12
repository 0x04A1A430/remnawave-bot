"""Покупка подписки по тарифам."""
import logging
from datetime import timedelta
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


def _format_price_kopeks(kopeks: int, compact: bool = False) -> str:
    """Форматирует цену из копеек в рубли."""
    rubles = kopeks / 100
    if compact:
        # Компактный формат - округляем до рублей
        return f"{int(round(rubles))}₽"
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


def _get_user_period_discount(db_user: User, period_days: int) -> int:
    """Получает скидку пользователя на период из промогруппы."""
    promo_group = getattr(db_user, 'promo_group', None)

    if promo_group:
        discount = promo_group.get_discount_percent("period", period_days)
        if discount > 0:
            return discount

    personal_discount = get_user_active_promo_discount_percent(db_user)
    return personal_discount


def format_tariffs_list_text(
    tariffs: List[Tariff],
    db_user: Optional[User] = None,
    has_period_discounts: bool = False,
) -> str:
    """Форматирует текст со списком тарифов для отображения."""
    lines = ["📦 <b>Выберите тариф</b>"]

    if has_period_discounts:
        lines.append("🎁 <i>Скидки по периодам</i>")

    lines.append("")

    for tariff in tariffs:
        # Трафик компактно
        traffic_gb = tariff.traffic_limit_gb
        traffic = "∞" if traffic_gb == 0 else f"{traffic_gb}ГБ"

        # Цена
        is_daily = getattr(tariff, 'is_daily', False)
        price_text = ""
        discount_icon = ""

        if is_daily:
            # Для суточных тарифов показываем цену за день
            daily_price = getattr(tariff, 'daily_price_kopeks', 0)
            price_text = f"🔄 {_format_price_kopeks(daily_price, compact=True)}/день"
        else:
            # Для периодных тарифов показываем минимальную цену
            prices = tariff.period_prices or {}
            if prices:
                min_period = min(prices.keys(), key=int)
                min_price = prices[min_period]
                discount_percent = 0
                if db_user:
                    discount_percent = _get_user_period_discount(db_user, int(min_period))
                if discount_percent > 0:
                    min_price = _apply_promo_discount(min_price, discount_percent)
                    discount_icon = "🔥"
                price_text = f"от {_format_price_kopeks(min_price, compact=True)}{discount_icon}"

        # Компактный формат: Название — 250ГБ/10📱 от 179₽🔥
        lines.append(f"<b>{tariff.name}</b> — {traffic}/{tariff.device_limit}📱 {price_text}")

        # Описание тарифа если есть
        if tariff.description:
            lines.append(f"<i>{tariff.description}</i>")

        lines.append("")

    return "\n".join(lines)


def get_tariffs_keyboard(
    tariffs: List[Tariff],
    language: str,
) -> InlineKeyboardMarkup:
    """Создает компактную клавиатуру выбора тарифов (только названия)."""
    texts = get_texts(language)
    buttons = []

    for tariff in tariffs:
        buttons.append([
            InlineKeyboardButton(
                text=f"📦 {tariff.name}",
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
    db_user: Optional[User] = None,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру выбора периода для тарифа с учетом скидок по периодам."""
    texts = get_texts(language)
    buttons = []

    prices = tariff.period_prices or {}
    for period_str in sorted(prices.keys(), key=int):
        period = int(period_str)
        price = prices[period_str]

        # Получаем скидку для конкретного периода
        discount_percent = 0
        if db_user:
            discount_percent = _get_user_period_discount(db_user, period)

        if discount_percent > 0:
            original_price = price
            price = _apply_promo_discount(price, discount_percent)
            price_text = f"{_format_price_kopeks(price)} 🔥−{discount_percent}%"
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

    # Для суточных тарифов не показываем выбор периода
    is_daily = getattr(tariff, 'is_daily', False)
    if not is_daily:
        text += "\nВыберите период подписки:"

    return text


def get_daily_tariff_confirm_keyboard(
    tariff_id: int,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения покупки суточного тарифа."""
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить покупку",
                callback_data=f"daily_tariff_confirm:{tariff_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.BACK,
                callback_data="tariff_list"
            )
        ]
    ])


def get_daily_tariff_insufficient_balance_keyboard(
    tariff_id: int,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру при недостаточном балансе для суточного тарифа."""
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
                callback_data="tariff_list"
            )
        ]
    ])


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

    # Проверяем есть ли у пользователя скидки по периодам
    promo_group = getattr(db_user, 'promo_group', None)
    has_period_discounts = False
    if promo_group:
        period_discounts = getattr(promo_group, 'period_discounts', None)
        if period_discounts and isinstance(period_discounts, dict) and len(period_discounts) > 0:
            has_period_discounts = True

    # Формируем текст со списком тарифов и их характеристиками
    tariffs_text = format_tariffs_list_text(tariffs, db_user, has_period_discounts)

    await callback.message.edit_text(
        tariffs_text,
        reply_markup=get_tariffs_keyboard(tariffs, db_user.language),
        parse_mode="HTML"
    )

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

    # Проверяем, суточный ли это тариф
    is_daily = getattr(tariff, 'is_daily', False)

    if is_daily:
        # Для суточного тарифа показываем подтверждение без выбора периода
        daily_price = getattr(tariff, 'daily_price_kopeks', 0)
        user_balance = db_user.balance_kopeks or 0
        traffic = _format_traffic(tariff.traffic_limit_gb)

        if user_balance >= daily_price:
            await callback.message.edit_text(
                f"✅ <b>Подтверждение покупки</b>\n\n"
                f"📦 Тариф: <b>{tariff.name}</b>\n"
                f"📊 Трафик: {traffic}\n"
                f"📱 Устройств: {tariff.device_limit}\n"
                f"🔄 Тип: <b>Суточный</b>\n\n"
                f"💰 <b>Цена: {_format_price_kopeks(daily_price)}/день</b>\n\n"
                f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n\n"
                f"ℹ️ Средства будут списываться автоматически раз в сутки.\n"
                f"Вы можете приостановить подписку в любой момент.",
                reply_markup=get_daily_tariff_confirm_keyboard(tariff_id, db_user.language),
                parse_mode="HTML"
            )
        else:
            missing = daily_price - user_balance
            await callback.message.edit_text(
                f"❌ <b>Недостаточно средств</b>\n\n"
                f"📦 Тариф: <b>{tariff.name}</b>\n"
                f"🔄 Тип: Суточный\n"
                f"💰 Цена: {_format_price_kopeks(daily_price)}/день\n\n"
                f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
                f"⚠️ Не хватает: <b>{_format_price_kopeks(missing)}</b>",
                reply_markup=get_daily_tariff_insufficient_balance_keyboard(tariff_id, db_user.language),
                parse_mode="HTML"
            )
    else:
        # Для обычного тарифа показываем выбор периода
        await callback.message.edit_text(
            format_tariff_info_for_user(tariff, db_user.language),
            reply_markup=get_tariff_periods_keyboard(tariff, db_user.language, db_user=db_user),
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

    # Получаем скидку для выбранного периода
    discount_percent = _get_user_period_discount(db_user, period)

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
        tariff_discount_percent=discount_percent,
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

    # Получаем скидку для выбранного периода
    discount_percent = _get_user_period_discount(db_user, period)

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
                None,  # Транзакция отсутствует, оплата с баланса
                period,
                was_trial_conversion=False,
                amount_kopeks=final_price,
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


# ==================== Покупка суточного тарифа ====================

@error_handler
async def confirm_daily_tariff_purchase(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Подтверждает покупку суточного тарифа."""
    from datetime import datetime

    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    is_daily = getattr(tariff, 'is_daily', False)
    if not is_daily:
        await callback.answer("Это не суточный тариф", show_alert=True)
        return

    daily_price = getattr(tariff, 'daily_price_kopeks', 0)
    if daily_price <= 0:
        await callback.answer("Некорректная цена тарифа", show_alert=True)
        return

    # Проверяем баланс
    user_balance = db_user.balance_kopeks or 0
    if user_balance < daily_price:
        await callback.answer("Недостаточно средств на балансе", show_alert=True)
        return

    texts = get_texts(db_user.language)

    try:
        # Списываем первый день сразу
        success = await subtract_user_balance(
            db, db_user, daily_price,
            f"Покупка суточного тарифа {tariff.name} (первый день)"
        )
        if not success:
            await callback.answer("Ошибка списания баланса", show_alert=True)
            return

        # Получаем список серверов из тарифа
        squads = tariff.allowed_squads or []

        # Проверяем есть ли уже подписка
        existing_subscription = await get_subscription_by_user_id(db, db_user.id)

        if existing_subscription:
            # Обновляем существующую подписку на суточный тариф
            existing_subscription.tariff_id = tariff.id
            existing_subscription.traffic_limit_gb = tariff.traffic_limit_gb
            existing_subscription.device_limit = tariff.device_limit
            existing_subscription.connected_squads = squads
            existing_subscription.status = "active"
            existing_subscription.is_trial = False  # Сбрасываем триальный статус
            existing_subscription.is_daily_paused = False
            existing_subscription.last_daily_charge_at = datetime.utcnow()
            # Для суточного тарифа ставим срок на 1 день
            existing_subscription.end_date = datetime.utcnow() + timedelta(days=1)

            await db.commit()
            await db.refresh(existing_subscription)
            subscription = existing_subscription
        else:
            # Создаем новую подписку на 1 день
            subscription = await create_paid_subscription(
                db=db,
                user_id=db_user.id,
                duration_days=1,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
                connected_squads=squads,
                tariff_id=tariff.id,
            )
            # Устанавливаем время последнего списания
            subscription.last_daily_charge_at = datetime.utcnow()
            subscription.is_daily_paused = False
            await db.commit()
            await db.refresh(subscription)

        # Обновляем пользователя в Remnawave
        try:
            subscription_service = SubscriptionService()
            await subscription_service.create_remnawave_user(
                db,
                subscription,
                reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
                reset_reason="покупка суточного тарифа",
            )
        except Exception as e:
            logger.error(f"Ошибка обновления Remnawave: {e}")

        # Создаем транзакцию
        await create_transaction(
            db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=-daily_price,
            description=f"Покупка суточного тарифа {tariff.name} (первый день)",
        )

        # Отправляем уведомление админу
        try:
            admin_notification_service = AdminNotificationService(callback.bot)
            await admin_notification_service.send_subscription_purchase_notification(
                db,
                db_user,
                subscription,
                None,
                1,  # 1 день
                was_trial_conversion=False,
                amount_kopeks=daily_price,
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу: {e}")

        # Очищаем корзину после успешной покупки
        try:
            await user_cart_service.delete_user_cart(db_user.id)
            logger.info(f"Корзина очищена после покупки суточного тарифа для пользователя {db_user.telegram_id}")
        except Exception as e:
            logger.error(f"Ошибка очистки корзины: {e}")

        await state.clear()

        traffic = _format_traffic(tariff.traffic_limit_gb)

        await callback.message.edit_text(
            f"🎉 <b>Суточная подписка оформлена!</b>\n\n"
            f"📦 Тариф: <b>{tariff.name}</b>\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройств: {tariff.device_limit}\n"
            f"🔄 Тип: Суточный\n"
            f"💰 Списано: {_format_price_kopeks(daily_price)}\n\n"
            f"ℹ️ Следующее списание через 24 часа.\n"
            f"Перейдите в раздел «Подписка» для подключения.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")],
                [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer("Подписка оформлена!", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка при покупке суточного тарифа: {e}", exc_info=True)
        await callback.answer("Произошла ошибка при оформлении подписки", show_alert=True)


# ==================== Продление по тарифу ====================

def get_tariff_extend_keyboard(
    tariff: Tariff,
    language: str,
    db_user: Optional[User] = None,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру выбора периода для продления по тарифу с учетом скидок по периодам."""
    texts = get_texts(language)
    buttons = []

    prices = tariff.period_prices or {}
    for period_str in sorted(prices.keys(), key=int):
        period = int(period_str)
        price = prices[period_str]

        # Получаем скидку для конкретного периода
        discount_percent = 0
        if db_user:
            discount_percent = _get_user_period_discount(db_user, period)

        if discount_percent > 0:
            original_price = price
            price = _apply_promo_discount(price, discount_percent)
            price_text = f"{_format_price_kopeks(price)} 🔥−{discount_percent}%"
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

    traffic = _format_traffic(tariff.traffic_limit_gb)

    # Проверяем есть ли у пользователя скидки по периодам
    promo_group = getattr(db_user, 'promo_group', None)
    has_period_discounts = False
    if promo_group:
        period_discounts = getattr(promo_group, 'period_discounts', None)
        if period_discounts and isinstance(period_discounts, dict) and len(period_discounts) > 0:
            has_period_discounts = True

    discount_hint = ""
    if has_period_discounts:
        discount_hint = "\n🎁 <i>Скидки зависят от выбранного периода</i>"

    await callback.message.edit_text(
        f"🔄 <b>Продление подписки</b>{discount_hint}\n\n"
        f"📦 Тариф: <b>{tariff.name}</b>\n"
        f"📊 Трафик: {traffic}\n"
        f"📱 Устройств: {tariff.device_limit}\n\n"
        "Выберите период продления:",
        reply_markup=get_tariff_extend_keyboard(tariff, db_user.language, db_user=db_user),
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
    texts = get_texts(db_user.language)
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    period = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    # Получаем скидку для выбранного периода
    discount_percent = _get_user_period_discount(db_user, period)

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
                None,  # Транзакция отсутствует, оплата с баланса
                period,
                was_trial_conversion=False,
                amount_kopeks=final_price,
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

def format_tariff_switch_list_text(
    tariffs: List[Tariff],
    current_tariff_id: Optional[int],
    current_tariff_name: str,
    db_user: Optional[User] = None,
    has_period_discounts: bool = False,
) -> str:
    """Форматирует текст со списком тарифов для переключения."""
    lines = [
        "📦 <b>Смена тарифа</b>",
        f"📌 Текущий: <b>{current_tariff_name}</b>",
    ]

    if has_period_discounts:
        lines.append("🎁 <i>Скидки по периодам</i>")

    lines.append("")
    lines.append("⚠️ Оплачивается полная стоимость.")
    lines.append("")

    for tariff in tariffs:
        if tariff.id == current_tariff_id:
            continue

        traffic_gb = tariff.traffic_limit_gb
        traffic = "∞" if traffic_gb == 0 else f"{traffic_gb}ГБ"

        # Проверяем суточный ли тариф
        is_daily = getattr(tariff, 'is_daily', False)
        price_text = ""
        discount_icon = ""

        if is_daily:
            # Для суточных тарифов показываем цену за день
            daily_price = getattr(tariff, 'daily_price_kopeks', 0)
            price_text = f"🔄 {_format_price_kopeks(daily_price, compact=True)}/день"
        else:
            prices = tariff.period_prices or {}
            if prices:
                min_period = min(prices.keys(), key=int)
                min_price = prices[min_period]
                discount_percent = 0
                if db_user:
                    discount_percent = _get_user_period_discount(db_user, int(min_period))
                if discount_percent > 0:
                    min_price = _apply_promo_discount(min_price, discount_percent)
                    discount_icon = "🔥"
                price_text = f"от {_format_price_kopeks(min_price, compact=True)}{discount_icon}"

        lines.append(f"<b>{tariff.name}</b> — {traffic}/{tariff.device_limit}📱 {price_text}")

        if tariff.description:
            lines.append(f"<i>{tariff.description}</i>")

        lines.append("")

    return "\n".join(lines)


def get_tariff_switch_keyboard(
    tariffs: List[Tariff],
    current_tariff_id: Optional[int],
    language: str,
) -> InlineKeyboardMarkup:
    """Создает компактную клавиатуру выбора тарифа для переключения."""
    texts = get_texts(language)
    buttons = []

    for tariff in tariffs:
        if tariff.id == current_tariff_id:
            continue

        buttons.append([
            InlineKeyboardButton(
                text=f"📦 {tariff.name}",
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
    db_user: Optional[User] = None,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру выбора периода для переключения тарифа с учетом скидок по периодам."""
    texts = get_texts(language)
    buttons = []

    prices = tariff.period_prices or {}
    for period_str in sorted(prices.keys(), key=int):
        period = int(period_str)
        price = prices[period_str]

        # Получаем скидку для конкретного периода
        discount_percent = 0
        if db_user:
            discount_percent = _get_user_period_discount(db_user, period)

        if discount_percent > 0:
            original_price = price
            price = _apply_promo_discount(price, discount_percent)
            price_text = f"{_format_price_kopeks(price)} 🔥−{discount_percent}%"
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

    # Проверяем есть ли у пользователя скидки по периодам
    promo_group = getattr(db_user, 'promo_group', None)
    has_period_discounts = False
    if promo_group:
        period_discounts = getattr(promo_group, 'period_discounts', None)
        if period_discounts and isinstance(period_discounts, dict) and len(period_discounts) > 0:
            has_period_discounts = True

    # Формируем текст со списком тарифов
    switch_text = format_tariff_switch_list_text(
        tariffs, current_tariff_id, current_tariff_name, db_user, has_period_discounts
    )

    await callback.message.edit_text(
        switch_text,
        reply_markup=get_tariff_switch_keyboard(tariffs, current_tariff_id, db_user.language),
        parse_mode="HTML"
    )

    await state.update_data(
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

    traffic = _format_traffic(tariff.traffic_limit_gb)

    # Проверяем, суточный ли это тариф
    is_daily = getattr(tariff, 'is_daily', False)

    if is_daily:
        # Для суточного тарифа показываем подтверждение без выбора периода
        daily_price = getattr(tariff, 'daily_price_kopeks', 0)
        user_balance = db_user.balance_kopeks or 0

        if user_balance >= daily_price:
            await callback.message.edit_text(
                f"✅ <b>Подтверждение смены тарифа</b>\n\n"
                f"📦 Новый тариф: <b>{tariff.name}</b>\n"
                f"📊 Трафик: {traffic}\n"
                f"📱 Устройств: {tariff.device_limit}\n"
                f"🔄 Тип: <b>Суточный</b>\n\n"
                f"💰 <b>Цена: {_format_price_kopeks(daily_price)}/день</b>\n\n"
                f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n\n"
                f"ℹ️ Средства будут списываться автоматически раз в сутки.\n"
                f"Вы можете приостановить подписку в любой момент.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="✅ Подтвердить смену",
                        callback_data=f"daily_tariff_switch_confirm:{tariff_id}"
                    )],
                    [InlineKeyboardButton(
                        text=get_texts(db_user.language).BACK,
                        callback_data="tariff_switch"
                    )]
                ]),
                parse_mode="HTML"
            )
        else:
            missing = daily_price - user_balance
            await callback.message.edit_text(
                f"❌ <b>Недостаточно средств</b>\n\n"
                f"📦 Тариф: <b>{tariff.name}</b>\n"
                f"🔄 Тип: Суточный\n"
                f"💰 Цена: {_format_price_kopeks(daily_price)}/день\n\n"
                f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
                f"⚠️ Не хватает: <b>{_format_price_kopeks(missing)}</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="💳 Пополнить баланс",
                        callback_data="balance_topup"
                    )],
                    [InlineKeyboardButton(
                        text=get_texts(db_user.language).BACK,
                        callback_data="tariff_switch"
                    )]
                ]),
                parse_mode="HTML"
            )
    else:
        # Для обычного тарифа показываем выбор периода
        info_text = f"""📦 <b>{tariff.name}</b>

<b>Параметры нового тарифа:</b>
• Трафик: {traffic}
• Устройств: {tariff.device_limit}
"""

        if tariff.description:
            info_text += f"\n📝 {tariff.description}\n"

        info_text += "\n⚠️ Оплачивается полная стоимость тарифа.\nВыберите период:"

        await callback.message.edit_text(
            info_text,
            reply_markup=get_tariff_switch_periods_keyboard(tariff, db_user.language, db_user=db_user),
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
    from datetime import datetime

    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    period = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    data = await state.get_data()
    current_tariff_id = data.get('current_tariff_id')

    # Получаем скидку для выбранного периода
    discount_percent = _get_user_period_discount(db_user, period)

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

    # Получаем текущую подписку для расчёта оставшегося времени
    subscription = await get_subscription_by_user_id(db, db_user.id)
    remaining_days = 0
    if subscription and subscription.end_date:
        remaining_days = max(0, (subscription.end_date - datetime.utcnow()).days)

    # При смене тарифа устанавливается ровно оплаченный период
    time_info = f"⏰ Будет установлено: {period} дней"

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
            f"{time_info}\n"
            f"{discount_text}\n"
            f"💰 <b>К оплате: {_format_price_kopeks(final_price)}</b>\n\n"
            f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
            f"После оплаты: {_format_price_kopeks(user_balance - final_price)}",
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

    # Получаем скидку для выбранного периода
    discount_percent = _get_user_period_discount(db_user, period)

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

        # При смене тарифа пользователь получает ровно тот период, за который заплатил
        # Старые дни не сохраняются - это смена тарифа, а не продление
        days_for_new_tariff = period

        # Обновляем подписку с новыми параметрами тарифа
        subscription = await extend_subscription(
            db,
            subscription,
            days=days_for_new_tariff,  # Даем ровно оплаченный период
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
            description=f"Смена тарифа на {tariff.name}",
        )

        # Отправляем уведомление админу
        try:
            admin_notification_service = AdminNotificationService(callback.bot)
            await admin_notification_service.send_subscription_purchase_notification(
                db,
                db_user,
                subscription,
                None,  # Транзакция отсутствует, оплата с баланса
                days_for_new_tariff,  # Итоговый срок подписки
                was_trial_conversion=False,
                amount_kopeks=final_price,
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

        # При смене тарифа устанавливается оплаченный период
        time_info = f"📅 Период: {days_for_new_tariff} дней"

        await callback.message.edit_text(
            f"🎉 <b>Тариф успешно изменён!</b>\n\n"
            f"📦 Новый тариф: <b>{tariff.name}</b>\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройств: {tariff.device_limit}\n"
            f"💰 Списано: {_format_price_kopeks(final_price)}\n"
            f"{time_info}\n\n"
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


# ==================== Смена на суточный тариф ====================

@error_handler
async def confirm_daily_tariff_switch(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Подтверждает смену на суточный тариф."""
    from datetime import datetime

    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    is_daily = getattr(tariff, 'is_daily', False)
    if not is_daily:
        await callback.answer("Это не суточный тариф", show_alert=True)
        return

    daily_price = getattr(tariff, 'daily_price_kopeks', 0)
    if daily_price <= 0:
        await callback.answer("Некорректная цена тарифа", show_alert=True)
        return

    # Проверяем баланс
    user_balance = db_user.balance_kopeks or 0
    if user_balance < daily_price:
        await callback.answer("Недостаточно средств на балансе", show_alert=True)
        return

    # Проверяем наличие подписки
    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not subscription:
        await callback.answer("У вас нет активной подписки", show_alert=True)
        return

    texts = get_texts(db_user.language)

    try:
        # Списываем первый день сразу
        success = await subtract_user_balance(
            db, db_user, daily_price,
            f"Смена на суточный тариф {tariff.name} (первый день)"
        )
        if not success:
            await callback.answer("Ошибка списания баланса", show_alert=True)
            return

        # Получаем список серверов из тарифа
        squads = tariff.allowed_squads or []

        # Обновляем подписку на суточный тариф
        subscription.tariff_id = tariff.id
        subscription.traffic_limit_gb = tariff.traffic_limit_gb
        subscription.device_limit = tariff.device_limit
        subscription.connected_squads = squads
        subscription.status = "active"
        subscription.is_trial = False  # Сбрасываем триальный статус
        subscription.is_daily_paused = False
        subscription.last_daily_charge_at = datetime.utcnow()
        # Для суточного тарифа ставим срок на 1 день
        subscription.end_date = datetime.utcnow() + timedelta(days=1)

        await db.commit()
        await db.refresh(subscription)

        # Обновляем пользователя в Remnawave
        try:
            subscription_service = SubscriptionService()
            await subscription_service.create_remnawave_user(
                db,
                subscription,
                reset_traffic=True,
                reset_reason="смена на суточный тариф",
            )
        except Exception as e:
            logger.error(f"Ошибка обновления Remnawave: {e}")

        # Создаем транзакцию
        await create_transaction(
            db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=-daily_price,
            description=f"Смена на суточный тариф {tariff.name} (первый день)",
        )

        # Отправляем уведомление админу
        try:
            admin_notification_service = AdminNotificationService(callback.bot)
            await admin_notification_service.send_subscription_purchase_notification(
                db,
                db_user,
                subscription,
                None,
                1,  # 1 день
                was_trial_conversion=False,
                amount_kopeks=daily_price,
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу: {e}")

        await state.clear()

        traffic = _format_traffic(tariff.traffic_limit_gb)

        await callback.message.edit_text(
            f"🎉 <b>Тариф успешно изменён!</b>\n\n"
            f"📦 Новый тариф: <b>{tariff.name}</b>\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройств: {tariff.device_limit}\n"
            f"🔄 Тип: Суточный\n"
            f"💰 Списано: {_format_price_kopeks(daily_price)}\n\n"
            f"ℹ️ Следующее списание через 24 часа.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")],
                [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer("Тариф изменён!", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка при смене на суточный тариф: {e}", exc_info=True)
        await callback.answer("Произошла ошибка при смене тарифа", show_alert=True)


# ==================== Мгновенное переключение тарифов (без выбора периода) ====================

def _get_tariff_monthly_price(tariff: Tariff) -> int:
    """Получает месячную цену тарифа (30 дней) с fallback на пропорциональный расчёт."""
    price = tariff.get_price_for_period(30)
    if price is not None:
        return price

    # Fallback: пропорционально пересчитываем из первого доступного периода
    periods = tariff.get_available_periods()
    if periods:
        first_period = periods[0]
        first_price = tariff.get_price_for_period(first_period)
        if first_price:
            return int(first_price * 30 / first_period)

    return 0


def _calculate_instant_switch_cost(
    current_tariff: Tariff,
    new_tariff: Tariff,
    remaining_days: int,
    db_user: Optional[User] = None,
) -> tuple[int, bool]:
    """
    Рассчитывает стоимость мгновенного переключения тарифа.

    Если новый тариф дороже - доплата пропорционально оставшимся дням.
    Если дешевле или равен - бесплатно.

    Формула: (new_monthly - current_monthly) * remaining_days / 30
    Скидка применяется к обоим тарифам одинаково.

    Returns:
        (upgrade_cost_kopeks, is_upgrade)
    """
    current_monthly = _get_tariff_monthly_price(current_tariff)
    new_monthly = _get_tariff_monthly_price(new_tariff)

    discount_percent = 0
    if db_user:
        discount_percent = _get_user_period_discount(db_user, 30)

    if discount_percent > 0:
        current_monthly = _apply_promo_discount(current_monthly, discount_percent)
        new_monthly = _apply_promo_discount(new_monthly, discount_percent)

    price_diff = new_monthly - current_monthly

    if price_diff <= 0:
        return 0, False

    upgrade_cost = int(price_diff * remaining_days / 30)
    return upgrade_cost, True


def format_instant_switch_list_text(
    tariffs: List[Tariff],
    current_tariff: Tariff,
    remaining_days: int,
    db_user: Optional[User] = None,
) -> str:
    """Форматирует текст со списком тарифов для мгновенного переключения."""
    lines = [
        "📦 <b>Мгновенная смена тарифа</b>",
        f"📌 Текущий: <b>{current_tariff.name}</b>",
        f"⏰ Осталось: <b>{remaining_days} дн.</b>",
        "",
        "💡 При переключении остаток дней сохраняется.",
        "⬆️ Повышение тарифа = доплата за разницу",
        "⬇️ Понижение = бесплатно",
        "",
    ]

    for tariff in tariffs:
        if tariff.id == current_tariff.id:
            continue

        traffic_gb = tariff.traffic_limit_gb
        traffic = "∞" if traffic_gb == 0 else f"{traffic_gb}ГБ"

        # Рассчитываем стоимость переключения
        cost, is_upgrade = _calculate_instant_switch_cost(
            current_tariff, tariff, remaining_days, db_user
        )

        if is_upgrade:
            cost_text = f"⬆️ +{_format_price_kopeks(cost, compact=True)}"
        else:
            cost_text = "⬇️ Бесплатно"

        lines.append(f"<b>{tariff.name}</b> — {traffic}/{tariff.device_limit}📱 {cost_text}")

        if tariff.description:
            lines.append(f"<i>{tariff.description}</i>")

        lines.append("")

    return "\n".join(lines)


def get_instant_switch_keyboard(
    tariffs: List[Tariff],
    current_tariff: Tariff,
    remaining_days: int,
    language: str,
    db_user: Optional[User] = None,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру для мгновенного переключения тарифа."""
    texts = get_texts(language)
    buttons = []

    for tariff in tariffs:
        if tariff.id == current_tariff.id:
            continue

        # Рассчитываем стоимость
        cost, is_upgrade = _calculate_instant_switch_cost(
            current_tariff, tariff, remaining_days, db_user
        )

        if is_upgrade:
            btn_text = f"📦 {tariff.name} (+{_format_price_kopeks(cost, compact=True)})"
        else:
            btn_text = f"📦 {tariff.name} (бесплатно)"

        buttons.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"instant_sw_preview:{tariff.id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_instant_switch_confirm_keyboard(
    tariff_id: int,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения мгновенного переключения."""
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить переключение",
                callback_data=f"instant_sw_confirm:{tariff_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.BACK,
                callback_data="instant_switch"
            )
        ]
    ])


def get_instant_switch_insufficient_balance_keyboard(
    tariff_id: int,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру при недостаточном балансе для мгновенного переключения."""
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
                callback_data="instant_switch"
            )
        ]
    ])


@error_handler
async def show_instant_switch_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает список тарифов для мгновенного переключения."""
    from datetime import datetime

    texts = get_texts(db_user.language)
    await state.clear()

    # Проверяем наличие активной подписки
    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not subscription:
        await callback.answer("У вас нет активной подписки", show_alert=True)
        return

    if not subscription.tariff_id:
        await callback.answer("У вашей подписки нет тарифа", show_alert=True)
        return

    # Получаем текущий тариф
    current_tariff = await get_tariff_by_id(db, subscription.tariff_id)
    if not current_tariff:
        await callback.answer("Текущий тариф не найден", show_alert=True)
        return

    # Рассчитываем оставшиеся дни
    remaining_days = 0
    if subscription.end_date:
        remaining_days = max(0, (subscription.end_date - datetime.utcnow()).days)

    if remaining_days == 0:
        await callback.message.edit_text(
            "❌ <b>Переключение недоступно</b>\n\n"
            "У вашей подписки не осталось активных дней.\n"
            "Используйте продление или покупку нового тарифа.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    # Получаем доступные тарифы
    promo_group_id = getattr(db_user, 'promo_group_id', None)
    tariffs = await get_tariffs_for_user(db, promo_group_id)

    # Фильтруем текущий тариф
    available_tariffs = [t for t in tariffs if t.id != current_tariff.id]

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

    # Формируем текст со списком тарифов
    switch_text = format_instant_switch_list_text(
        tariffs, current_tariff, remaining_days, db_user
    )

    await callback.message.edit_text(
        switch_text,
        reply_markup=get_instant_switch_keyboard(
            tariffs, current_tariff, remaining_days, db_user.language, db_user
        ),
        parse_mode="HTML"
    )

    await state.update_data(
        current_tariff_id=current_tariff.id,
        remaining_days=remaining_days,
    )
    await callback.answer()


@error_handler
async def preview_instant_switch(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает превью мгновенного переключения тарифа."""
    from datetime import datetime

    tariff_id = int(callback.data.split(":")[1])
    new_tariff = await get_tariff_by_id(db, tariff_id)

    if not new_tariff or not new_tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    # Получаем данные из состояния
    data = await state.get_data()
    current_tariff_id = data.get('current_tariff_id')
    remaining_days = data.get('remaining_days', 0)

    # Если данных нет в state, получаем заново
    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not subscription or not subscription.tariff_id:
        await callback.answer("Подписка не найдена", show_alert=True)
        return

    current_tariff_id = current_tariff_id or subscription.tariff_id
    current_tariff = await get_tariff_by_id(db, current_tariff_id)
    if not current_tariff:
        await callback.answer("Текущий тариф не найден", show_alert=True)
        return

    if not remaining_days and subscription.end_date:
        remaining_days = max(0, (subscription.end_date - datetime.utcnow()).days)

    # Рассчитываем стоимость переключения
    upgrade_cost, is_upgrade = _calculate_instant_switch_cost(
        current_tariff, new_tariff, remaining_days, db_user
    )

    # Проверяем баланс
    user_balance = db_user.balance_kopeks or 0

    traffic = _format_traffic(new_tariff.traffic_limit_gb)
    current_traffic = _format_traffic(current_tariff.traffic_limit_gb)

    texts = get_texts(db_user.language)

    if is_upgrade:
        # Upgrade - нужна доплата
        if user_balance >= upgrade_cost:
            await callback.message.edit_text(
                f"⬆️ <b>Повышение тарифа</b>\n\n"
                f"📌 Текущий: <b>{current_tariff.name}</b>\n"
                f"   • Трафик: {current_traffic}\n"
                f"   • Устройств: {current_tariff.device_limit}\n\n"
                f"📦 Новый: <b>{new_tariff.name}</b>\n"
                f"   • Трафик: {traffic}\n"
                f"   • Устройств: {new_tariff.device_limit}\n\n"
                f"⏰ Осталось дней: <b>{remaining_days}</b>\n"
                f"💰 <b>Доплата: {_format_price_kopeks(upgrade_cost)}</b>\n\n"
                f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
                f"После оплаты: {_format_price_kopeks(user_balance - upgrade_cost)}",
                reply_markup=get_instant_switch_confirm_keyboard(tariff_id, db_user.language),
                parse_mode="HTML"
            )
        else:
            missing = upgrade_cost - user_balance
            await callback.message.edit_text(
                f"❌ <b>Недостаточно средств</b>\n\n"
                f"📦 Новый тариф: <b>{new_tariff.name}</b>\n"
                f"💰 Требуется доплата: {_format_price_kopeks(upgrade_cost)}\n\n"
                f"💳 Ваш баланс: {_format_price_kopeks(user_balance)}\n"
                f"⚠️ Не хватает: <b>{_format_price_kopeks(missing)}</b>",
                reply_markup=get_instant_switch_insufficient_balance_keyboard(tariff_id, db_user.language),
                parse_mode="HTML"
            )
    else:
        # Downgrade или тот же уровень - бесплатно
        await callback.message.edit_text(
            f"⬇️ <b>Переключение тарифа</b>\n\n"
            f"📌 Текущий: <b>{current_tariff.name}</b>\n"
            f"   • Трафик: {current_traffic}\n"
            f"   • Устройств: {current_tariff.device_limit}\n\n"
            f"📦 Новый: <b>{new_tariff.name}</b>\n"
            f"   • Трафик: {traffic}\n"
            f"   • Устройств: {new_tariff.device_limit}\n\n"
            f"⏰ Осталось дней: <b>{remaining_days}</b>\n"
            f"💰 <b>Бесплатно</b> (понижение/равный тариф)",
            reply_markup=get_instant_switch_confirm_keyboard(tariff_id, db_user.language),
            parse_mode="HTML"
        )

    await state.update_data(
        switch_tariff_id=tariff_id,
        upgrade_cost=upgrade_cost,
        is_upgrade=is_upgrade,
        current_tariff_id=current_tariff_id,
        remaining_days=remaining_days,
    )
    await callback.answer()


@error_handler
async def confirm_instant_switch(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Подтверждает мгновенное переключение тарифа."""
    from datetime import datetime, timedelta

    tariff_id = int(callback.data.split(":")[1])
    new_tariff = await get_tariff_by_id(db, tariff_id)

    if not new_tariff or not new_tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    # Получаем данные из состояния
    data = await state.get_data()
    upgrade_cost = data.get('upgrade_cost', 0)
    is_upgrade = data.get('is_upgrade', False)
    remaining_days = data.get('remaining_days', 0)

    # Проверяем подписку
    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not subscription:
        await callback.answer("Подписка не найдена", show_alert=True)
        return

    # Проверяем баланс если это upgrade
    user_balance = db_user.balance_kopeks or 0
    if is_upgrade and user_balance < upgrade_cost:
        await callback.answer("Недостаточно средств на балансе", show_alert=True)
        return

    texts = get_texts(db_user.language)

    try:
        # Списываем баланс если это upgrade
        if is_upgrade and upgrade_cost > 0:
            success = await subtract_user_balance(
                db, db_user, upgrade_cost,
                f"Переключение на тариф {new_tariff.name}"
            )
            if not success:
                await callback.answer("Ошибка списания баланса", show_alert=True)
                return

        # Получаем список серверов из нового тарифа
        squads = new_tariff.allowed_squads or []

        # Обновляем подписку с новыми параметрами тарифа
        # НЕ меняем end_date - только параметры тарифа
        subscription.tariff_id = new_tariff.id
        subscription.traffic_limit_gb = new_tariff.traffic_limit_gb
        subscription.device_limit = new_tariff.device_limit
        subscription.connected_squads = squads

        await db.commit()
        await db.refresh(subscription)

        # Обновляем пользователя в Remnawave
        try:
            subscription_service = SubscriptionService()
            await subscription_service.create_remnawave_user(
                db,
                subscription,
                reset_traffic=False,  # Не сбрасываем трафик при переключении
                reset_reason="мгновенное переключение тарифа",
            )
        except Exception as e:
            logger.error(f"Ошибка обновления Remnawave при мгновенном переключении: {e}")

        # Создаем транзакцию если была оплата
        if is_upgrade and upgrade_cost > 0:
            await create_transaction(
                db,
                user_id=db_user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=-upgrade_cost,
                description=f"Переключение на тариф {new_tariff.name}",
            )

            # Отправляем уведомление админу
            try:
                admin_notification_service = AdminNotificationService(callback.bot)
                await admin_notification_service.send_subscription_purchase_notification(
                    db,
                    db_user,
                    subscription,
                    None,
                    remaining_days,
                    was_trial_conversion=False,
                    amount_kopeks=upgrade_cost,
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления админу: {e}")

        await state.clear()

        traffic = _format_traffic(new_tariff.traffic_limit_gb)

        if is_upgrade:
            cost_text = f"💰 Списано: {_format_price_kopeks(upgrade_cost)}"
        else:
            cost_text = "💰 Бесплатно"

        await callback.message.edit_text(
            f"🎉 <b>Тариф успешно изменён!</b>\n\n"
            f"📦 Новый тариф: <b>{new_tariff.name}</b>\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройств: {new_tariff.device_limit}\n"
            f"⏰ Осталось дней: {remaining_days}\n"
            f"{cost_text}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")],
                [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer("Тариф изменён!", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка при мгновенном переключении тарифа: {e}", exc_info=True)
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

    # Подтверждение покупки суточного тарифа
    dp.callback_query.register(confirm_daily_tariff_purchase, F.data.startswith("daily_tariff_confirm:"))

    # Продление по тарифу
    dp.callback_query.register(select_tariff_extend_period, F.data.startswith("tariff_extend:"))
    dp.callback_query.register(confirm_tariff_extend, F.data.startswith("tariff_ext_confirm:"))

    # Переключение тарифов (с выбором периода)
    dp.callback_query.register(show_tariff_switch_list, F.data == "tariff_switch")
    dp.callback_query.register(select_tariff_switch, F.data.startswith("tariff_sw_select:"))
    dp.callback_query.register(select_tariff_switch_period, F.data.startswith("tariff_sw_period:"))
    dp.callback_query.register(confirm_tariff_switch, F.data.startswith("tariff_sw_confirm:"))

    # Смена на суточный тариф
    dp.callback_query.register(confirm_daily_tariff_switch, F.data.startswith("daily_tariff_switch_confirm:"))

    # Мгновенное переключение тарифов (без выбора периода)
    dp.callback_query.register(show_instant_switch_list, F.data == "instant_switch")
    dp.callback_query.register(preview_instant_switch, F.data.startswith("instant_sw_preview:"))
    dp.callback_query.register(confirm_instant_switch, F.data.startswith("instant_sw_confirm:"))
