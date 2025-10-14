import html
import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.states import BalanceStates
from app.database.crud.user import add_user_balance
from app.database.crud.transaction import (
    get_user_transactions, get_user_transactions_count,
    create_transaction
)
from app.database.models import User, TransactionType, PaymentMethod
from app.keyboards.inline import (
    get_balance_keyboard, get_payment_methods_keyboard,
    get_back_keyboard, get_pagination_keyboard
)
from app.localization.texts import get_texts
from app.services.payment_service import PaymentService
from app.utils.pagination import paginate_list
from app.utils.decorators import error_handler

logger = logging.getLogger(__name__)

TRANSACTIONS_PER_PAGE = 10


def get_quick_amount_buttons(language: str) -> list:
    if not settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED or settings.DISABLE_TOPUP_BUTTONS:
        return []
    
    buttons = []
    periods = settings.get_available_subscription_periods()
    
    periods = periods[:6]
    
    for period in periods:
        price_attr = f"PRICE_{period}_DAYS"
        if hasattr(settings, price_attr):
            price_kopeks = getattr(settings, price_attr)
            price_rubles = price_kopeks // 100
            
            callback_data = f"quick_amount_{price_kopeks}"
            
            buttons.append(
                types.InlineKeyboardButton(
                    text=f"{price_rubles} ₽ ({period} дней)",
                    callback_data=callback_data
                )
            )
    
    keyboard_rows = []
    for i in range(0, len(buttons), 2):
        keyboard_rows.append(buttons[i:i + 2])
    
    return keyboard_rows


@error_handler


@error_handler
async def show_balance_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    balance_text = texts.BALANCE_INFO.format(
        balance=texts.format_price(db_user.balance_kopeks)
    )
    
    await callback.message.edit_text(
        balance_text,
        reply_markup=get_balance_keyboard(db_user.language)
    )
    await callback.answer()


@error_handler
async def show_balance_history(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    page: int = 1
):
    texts = get_texts(db_user.language)
    
    offset = (page - 1) * TRANSACTIONS_PER_PAGE
    
    raw_transactions = await get_user_transactions(
        db, db_user.id, 
        limit=TRANSACTIONS_PER_PAGE * 3, 
        offset=offset
    )
    
    seen_transactions = set()
    unique_transactions = []
    
    for transaction in raw_transactions:
        rounded_time = transaction.created_at.replace(second=0, microsecond=0)
        transaction_key = (
            transaction.amount_kopeks,
            transaction.description,
            rounded_time
        )
        
        if transaction_key not in seen_transactions:
            seen_transactions.add(transaction_key)
            unique_transactions.append(transaction)
            
            if len(unique_transactions) >= TRANSACTIONS_PER_PAGE:
                break
    
    all_transactions = await get_user_transactions(db, db_user.id, limit=1000)
    seen_all = set()
    total_unique = 0
    
    for transaction in all_transactions:
        rounded_time = transaction.created_at.replace(second=0, microsecond=0)
        transaction_key = (
            transaction.amount_kopeks,
            transaction.description,
            rounded_time
        )
        if transaction_key not in seen_all:
            seen_all.add(transaction_key)
            total_unique += 1
    
    if not unique_transactions:
        await callback.message.edit_text(
            "📊 История операций пуста",
            reply_markup=get_back_keyboard(db_user.language)
        )
        await callback.answer()
        return
    
    text = "📊 <b>История операций</b>\n\n"
    
    for transaction in unique_transactions:
        emoji = "💰" if transaction.type == TransactionType.DEPOSIT.value else "💸"
        amount_text = f"+{texts.format_price(transaction.amount_kopeks)}" if transaction.type == TransactionType.DEPOSIT.value else f"-{texts.format_price(transaction.amount_kopeks)}"
        
        text += f"{emoji} {amount_text}\n"
        text += f"📝 {transaction.description}\n"
        text += f"📅 {transaction.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
    
    keyboard = []
    total_pages = (total_unique + TRANSACTIONS_PER_PAGE - 1) // TRANSACTIONS_PER_PAGE
    
    if total_pages > 1:
        pagination_row = get_pagination_keyboard(
            page, total_pages, "balance_history", db_user.language
        )
        keyboard.extend(pagination_row)
    
    keyboard.append([
        types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_balance")
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()


@error_handler
async def handle_balance_history_pagination(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    page = int(callback.data.split('_')[-1])
    await show_balance_history(callback, db_user, db, page)


@error_handler
async def show_payment_methods(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    from app.utils.payment_utils import get_payment_methods_text
    
    texts = get_texts(db_user.language)
    payment_text = get_payment_methods_text(db_user.language)
    
    await callback.message.edit_text(
        payment_text,
        reply_markup=get_payment_methods_keyboard(0, db_user.language), 
        parse_mode="HTML"
    )
    await callback.answer()


@error_handler
async def handle_payment_methods_unavailable(
    callback: types.CallbackQuery,
    db_user: User
):
    texts = get_texts(db_user.language)
    
    await callback.answer(
        texts.t(
            "PAYMENT_METHODS_UNAVAILABLE_ALERT",
            "⚠️ В данный момент автоматические способы оплаты временно недоступны. Для пополнения баланса обратитесь в техподдержку.",
        ),
        show_alert=True
    )


@error_handler
async def start_stars_payment(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    if not settings.TELEGRAM_STARS_ENABLED:
        await callback.answer("❌ Пополнение через Stars временно недоступно", show_alert=True)
        return
    
    # Формируем текст сообщения в зависимости от настройки
    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        message_text = (
            f"⭐ <b>Пополнение через Telegram Stars</b>\n\n"
            f"Выберите сумму пополнения или введите вручную:"
        )
    else:
        message_text = texts.TOP_UP_AMOUNT
    
    # Создаем клавиатуру
    keyboard = get_back_keyboard(db_user.language)
    
    # Если включен быстрый выбор суммы и не отключены кнопки, добавляем кнопки
    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        quick_amount_buttons = get_quick_amount_buttons(db_user.language)
        if quick_amount_buttons:
            # Вставляем кнопки быстрого выбора перед кнопкой "Назад"
            keyboard.inline_keyboard = quick_amount_buttons + keyboard.inline_keyboard
    
    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard
    )
    
    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="stars")
    await callback.answer()


@error_handler
async def start_yookassa_payment(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    if not settings.is_yookassa_enabled():
        await callback.answer("❌ Оплата картой через YooKassa временно недоступна", show_alert=True)
        return
    
    min_amount_rub = settings.YOOKASSA_MIN_AMOUNT_KOPEKS / 100
    max_amount_rub = settings.YOOKASSA_MAX_AMOUNT_KOPEKS / 100
    
    # Формируем текст сообщения в зависимости от настройки
    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        message_text = (
            f"💳 <b>Оплата банковской картой</b>\n\n"
            f"Выберите сумму пополнения или введите вручную сумму "
            f"от {min_amount_rub:.0f} до {max_amount_rub:,.0f} рублей:"
        )
    else:
        message_text = (
            f"💳 <b>Оплата банковской картой</b>\n\n"
            f"Введите сумму для пополнения от {min_amount_rub:.0f} до {max_amount_rub:,.0f} рублей:"
        )
    
    # Создаем клавиатуру
    keyboard = get_back_keyboard(db_user.language)
    
    # Если включен быстрый выбор суммы и не отключены кнопки, добавляем кнопки
    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        quick_amount_buttons = get_quick_amount_buttons(db_user.language)
        if quick_amount_buttons:
            # Вставляем кнопки быстрого выбора перед кнопкой "Назад"
            keyboard.inline_keyboard = quick_amount_buttons + keyboard.inline_keyboard
    
    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="yookassa")
    await callback.answer()


@error_handler
async def start_yookassa_sbp_payment(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    if not settings.is_yookassa_enabled() or not settings.YOOKASSA_SBP_ENABLED:
        await callback.answer("❌ Оплата через СБП временно недоступна", show_alert=True)
        return
    
    min_amount_rub = settings.YOOKASSA_MIN_AMOUNT_KOPEKS / 100
    max_amount_rub = settings.YOOKASSA_MAX_AMOUNT_KOPEKS / 100
    
    # Формируем текст сообщения в зависимости от настройки
    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        message_text = (
            f"🏦 <b>Оплата через СБП</b>\n\n"
            f"Выберите сумму пополнения или введите вручную сумму "
            f"от {min_amount_rub:.0f} до {max_amount_rub:,.0f} рублей:"
        )
    else:
        message_text = (
            f"🏦 <b>Оплата через СБП</b>\n\n"
            f"Введите сумму для пополнения от {min_amount_rub:.0f} до {max_amount_rub:,.0f} рублей:"
        )
    
    # Создаем клавиатуру
    keyboard = get_back_keyboard(db_user.language)
    
    # Если включен быстрый выбор суммы и не отключены кнопки, добавляем кнопки
    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        quick_amount_buttons = get_quick_amount_buttons(db_user.language)
        if quick_amount_buttons:
            # Вставляем кнопки быстрого выбора перед кнопкой "Назад"
            keyboard.inline_keyboard = quick_amount_buttons + keyboard.inline_keyboard
    
    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="yookassa_sbp")
    await callback.answer()


@error_handler
async def start_mulenpay_payment(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)

    if not settings.is_mulenpay_enabled():
        await callback.answer("❌ Оплата через Mulen Pay временно недоступна", show_alert=True)
        return

    message_text = texts.t(
        "MULENPAY_TOPUP_PROMPT",
        (
            "💳 <b>Оплата через Mulen Pay</b>\n\n"
            "Введите сумму для пополнения от 100 до 100 000 ₽.\n"
            "Оплата происходит через защищенную платформу Mulen Pay."
        ),
    )

    keyboard = get_back_keyboard(db_user.language)

    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        quick_amount_buttons = get_quick_amount_buttons(db_user.language)
        if quick_amount_buttons:
            keyboard.inline_keyboard = quick_amount_buttons + keyboard.inline_keyboard

    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="mulenpay")
    await callback.answer()


@error_handler
async def start_pal24_payment(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)

    if not settings.is_pal24_enabled():
        await callback.answer("❌ Оплата через PayPalych временно недоступна", show_alert=True)
        return

    # Формируем текст сообщения в зависимости от доступных способов оплаты
    if settings.is_pal24_sbp_button_visible() and settings.is_pal24_card_button_visible():
        payment_methods_text = "СБП и банковской картой"
    elif settings.is_pal24_sbp_button_visible():
        payment_methods_text = "СБП"
    elif settings.is_pal24_card_button_visible():
        payment_methods_text = "банковской картой"
    else:
        # Если обе кнопки отключены, используем общий текст
        payment_methods_text = "доступными способами"

    message_text = texts.t(
        "PAL24_TOPUP_PROMPT",
        (
            f"🏦 <b>Оплата через PayPalych ({payment_methods_text})</b>\n\n"
            "Введите сумму для пополнения от 100 до 1 000 000 ₽.\n"
            f"Оплата проходит через PayPalych ({payment_methods_text})."
        ),
    )

    keyboard = get_back_keyboard(db_user.language)

    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        quick_amount_buttons = get_quick_amount_buttons(db_user.language)
        if quick_amount_buttons:
            keyboard.inline_keyboard = quick_amount_buttons + keyboard.inline_keyboard

    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="pal24")
    await callback.answer()


@error_handler
async def start_tribute_payment(
    callback: types.CallbackQuery,
    db_user: User
):
    texts = get_texts(db_user.language)
    
    if not settings.TRIBUTE_ENABLED:
        await callback.answer("❌ Оплата картой временно недоступна", show_alert=True)
        return
    
    try:
        from app.services.tribute_service import TributeService
        
        tribute_service = TributeService(callback.bot)
        payment_url = await tribute_service.create_payment_link(
            user_id=db_user.telegram_id,
            amount_kopeks=0,
            description="Пополнение баланса VPN"
        )
        
        if not payment_url:
            await callback.answer("❌ Ошибка создания платежа", show_alert=True)
            return
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_url)],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")]
        ])
        
        await callback.message.edit_text(
            f"💳 <b>Пополнение банковской картой</b>\n\n"
            f"• Введите любую сумму от 100₽\n"
            f"• Безопасная оплата через Tribute\n"
            f"• Мгновенное зачисление на баланс\n"
            f"• Принимаем карты Visa, MasterCard, МИР\n\n"
            f"• 🚨 НЕ ОТПРАВЛЯТЬ ПЛАТЕЖ АНОНИМНО!\n\n"
            f"Нажмите кнопку для перехода к оплате:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка создания Tribute платежа: {e}")
        await callback.answer("❌ Ошибка создания платежа", show_alert=True)
    
    await callback.answer()
    
async def handle_successful_topup_with_cart(
    user_id: int,
    amount_kopeks: int,
    bot,
    db: AsyncSession
):
    from app.database.crud.user import get_user_by_id
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from app.bot import dp
    
    user = await get_user_by_id(db, user_id)
    if not user:
        return
    
    storage = dp.storage
    key = StorageKey(bot_id=bot.id, chat_id=user.telegram_id, user_id=user.telegram_id)
    
    try:
        state_data = await storage.get_data(key)
        current_state = await storage.get_state(key)
        
        if (current_state == "SubscriptionStates:cart_saved_for_topup" and 
            state_data.get('saved_cart')):
            
            texts = get_texts(user.language)
            total_price = state_data.get('total_price', 0)
            
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(
                    text="🛒 Вернуться к оформлению подписки", 
                    callback_data="return_to_saved_cart"
                )],
                [types.InlineKeyboardButton(
                    text="💰 Мой баланс", 
                    callback_data="menu_balance"
                )],
                [types.InlineKeyboardButton(
                    text="🏠 Главное меню", 
                    callback_data="back_to_menu"
                )]
            ])
            
            success_text = (
                f"✅ Баланс пополнен на {texts.format_price(amount_kopeks)}!\n\n"
                f"💰 Текущий баланс: {texts.format_price(user.balance_kopeks)}\n\n"
                f"🛒 У вас есть сохраненная корзина подписки\n"
                f"Стоимость: {texts.format_price(total_price)}\n\n"
                f"Хотите продолжить оформление?"
            )
            
            await bot.send_message(
                chat_id=user.telegram_id,
                text=success_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
    except Exception as e:
        logger.error(f"Ошибка обработки успешного пополнения с корзиной: {e}")

@error_handler
async def request_support_topup(
    callback: types.CallbackQuery,
    db_user: User
):
    texts = get_texts(db_user.language)
    
    support_text = f"""
🛠️ <b>Пополнение через поддержку</b>

Для пополнения баланса обратитесь в техподдержку:
{settings.get_support_contact_display_html()}

Укажите:
• ID: {db_user.telegram_id}
• Сумму пополнения
• Способ оплаты

⏰ Время обработки: 1-24 часа

<b>Доступные способы:</b>
• Криптовалюта
• Переводы между банками
• Другие платежные системы
"""
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="💬 Написать в поддержку",
            url=settings.get_support_contact_url() or "https://t.me/"
        )],
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")]
    ])
    
    await callback.message.edit_text(
        support_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@error_handler
async def process_topup_amount(
    message: types.Message,
    db_user: User,
    state: FSMContext
):
    texts = get_texts(db_user.language)

    try:
        if not message.text:
            if message.successful_payment:
                logger.info(
                    "Получено сообщение об успешном платеже без текста, "
                    "обработчик суммы пополнения завершает работу"
                )
                await state.clear()
                return

            await message.answer(
                texts.INVALID_AMOUNT,
                reply_markup=get_back_keyboard(db_user.language)
            )
            return

        amount_text = message.text.strip()
        if not amount_text:
            await message.answer(
                texts.INVALID_AMOUNT,
                reply_markup=get_back_keyboard(db_user.language)
            )
            return

        amount_rubles = float(amount_text.replace(',', '.'))

        if amount_rubles < 1:
            await message.answer("Минимальная сумма пополнения: 1 ₽")
            return
        
        if amount_rubles > 50000:
            await message.answer("Максимальная сумма пополнения: 50,000 ₽")
            return
        
        amount_kopeks = int(amount_rubles * 100)
        data = await state.get_data()
        payment_method = data.get("payment_method", "stars")
        
        if payment_method in ["yookassa", "yookassa_sbp"]:
            if amount_kopeks < settings.YOOKASSA_MIN_AMOUNT_KOPEKS:
                min_rubles = settings.YOOKASSA_MIN_AMOUNT_KOPEKS / 100
                await message.answer(f"❌ Минимальная сумма для оплаты через YooKassa: {min_rubles:.0f} ₽")
                return
            
            if amount_kopeks > settings.YOOKASSA_MAX_AMOUNT_KOPEKS:
                max_rubles = settings.YOOKASSA_MAX_AMOUNT_KOPEKS / 100
                await message.answer(f"❌ Максимальная сумма для оплаты через YooKassa: {max_rubles:,.0f} ₽".replace(',', ' '))
                return
        
        if payment_method == "stars":
            await process_stars_payment_amount(message, db_user, amount_kopeks, state)
        elif payment_method == "yookassa":
            from app.database.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await process_yookassa_payment_amount(message, db_user, db, amount_kopeks, state)
        elif payment_method == "yookassa_sbp":
            from app.database.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await process_yookassa_sbp_payment_amount(message, db_user, db, amount_kopeks, state)
        elif payment_method == "mulenpay":
            from app.database.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await process_mulenpay_payment_amount(message, db_user, db, amount_kopeks, state)
        elif payment_method == "pal24":
            from app.database.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await process_pal24_payment_amount(message, db_user, db, amount_kopeks, state)
        elif payment_method == "cryptobot":
            from app.database.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await process_cryptobot_payment_amount(message, db_user, db, amount_kopeks, state)
        else:
            await message.answer("Неизвестный способ оплаты")
        
    except ValueError:
        await message.answer(
            texts.INVALID_AMOUNT,
            reply_markup=get_back_keyboard(db_user.language)
        )

@error_handler
async def process_stars_payment_amount(
    message: types.Message,
    db_user: User,
    amount_kopeks: int,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    if not settings.TELEGRAM_STARS_ENABLED:
        await message.answer("⚠️ Оплата Stars временно недоступна")
        return
    
    try:
        from app.external.telegram_stars import TelegramStarsService
        
        amount_rubles = amount_kopeks / 100
        stars_amount = TelegramStarsService.calculate_stars_from_rubles(amount_rubles)
        stars_rate = settings.get_stars_rate() 
        
        payment_service = PaymentService(message.bot)
        invoice_link = await payment_service.create_stars_invoice(
            amount_kopeks=amount_kopeks,
            description=f"Пополнение баланса на {texts.format_price(amount_kopeks)}",
            payload=f"balance_{db_user.id}_{amount_kopeks}"
        )
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⭐ Оплатить", url=invoice_link)],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")]
        ])
        
        await message.answer(
            f"⭐ <b>Оплата через Telegram Stars</b>\n\n"
            f"💰 Сумма: {texts.format_price(amount_kopeks)}\n"
            f"⭐ К оплате: {stars_amount} звезд\n"
            f"📊 Курс: {stars_rate}₽ за звезду\n\n"
            f"Нажмите кнопку ниже для оплаты:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Ошибка создания Stars invoice: {e}")
        await message.answer("⚠️ Ошибка создания платежа")



@error_handler
async def process_yookassa_payment_amount(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    if not settings.is_yookassa_enabled():
        await message.answer("❌ Оплата через YooKassa временно недоступна")
        return
    
    if amount_kopeks < settings.YOOKASSA_MIN_AMOUNT_KOPEKS:
        min_rubles = settings.YOOKASSA_MIN_AMOUNT_KOPEKS / 100
        await message.answer(f"❌ Минимальная сумма для оплаты картой: {min_rubles:.0f} ₽")
        return
    
    if amount_kopeks > settings.YOOKASSA_MAX_AMOUNT_KOPEKS:
        max_rubles = settings.YOOKASSA_MAX_AMOUNT_KOPEKS / 100
        await message.answer(f"❌ Максимальная сумма для оплаты картой: {max_rubles:,.0f} ₽".replace(',', ' '))
        return
    
    try:
        payment_service = PaymentService(message.bot)
        
        payment_result = await payment_service.create_yookassa_payment(
            db=db,
            user_id=db_user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
            receipt_email=None,
            receipt_phone=None,
            metadata={
                "user_telegram_id": str(db_user.telegram_id),
                "user_username": db_user.username or "",
                "purpose": "balance_topup"
            }
        )
        
        if not payment_result:
            await message.answer("❌ Ошибка создания платежа. Попробуйте позже или обратитесь в поддержку.")
            await state.clear()
            return
        
        confirmation_url = payment_result.get("confirmation_url")
        if not confirmation_url:
            await message.answer("❌ Ошибка получения ссылки для оплаты. Обратитесь в поддержку.")
            await state.clear()
            return
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="💳 Оплатить картой", url=confirmation_url)],
            [types.InlineKeyboardButton(text="📊 Проверить статус", callback_data=f"check_yookassa_{payment_result['local_payment_id']}")],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")]
        ])
        
        await message.answer(
            f"💳 <b>Оплата банковской картой</b>\n\n"
            f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
            f"🆔 ID платежа: {payment_result['yookassa_payment_id'][:8]}...\n\n"
            f"📱 <b>Инструкция:</b>\n"
            f"1. Нажмите кнопку 'Оплатить картой'\n"
            f"2. Введите данные вашей карты\n"
            f"3. Подтвердите платеж\n"
            f"4. Деньги поступят на баланс автоматически\n\n"
            f"🔒 Оплата происходит через защищенную систему YooKassa\n"
            f"✅ Принимаем карты: Visa, MasterCard, МИР\n\n"
            f"❓ Если возникнут проблемы, обратитесь в {settings.get_support_contact_display_html()}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        await state.clear()
        
        logger.info(f"Создан платеж YooKassa для пользователя {db_user.telegram_id}: "
                   f"{amount_kopeks//100}₽, ID: {payment_result['yookassa_payment_id']}")
        
    except Exception as e:
        logger.error(f"Ошибка создания YooKassa платежа: {e}")
        await message.answer("❌ Ошибка создания платежа. Попробуйте позже или обратитесь в поддержку.")
        await state.clear()


@error_handler
async def process_yookassa_sbp_payment_amount(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    if not settings.is_yookassa_enabled() or not settings.YOOKASSA_SBP_ENABLED:
        await message.answer("❌ Оплата через СБП временно недоступна")
        return
    
    if amount_kopeks < settings.YOOKASSA_MIN_AMOUNT_KOPEKS:
        min_rubles = settings.YOOKASSA_MIN_AMOUNT_KOPEKS / 100
        await message.answer(f"❌ Минимальная сумма для оплаты через СБП: {min_rubles:.0f} ₽")
        return
    
    if amount_kopeks > settings.YOOKASSA_MAX_AMOUNT_KOPEKS:
        max_rubles = settings.YOOKASSA_MAX_AMOUNT_KOPEKS / 100
        await message.answer(f"❌ Максимальная сумма для оплаты через СБП: {max_rubles:,.0f} ₽".replace(',', ' '))
        return
    
    try:
        payment_service = PaymentService(message.bot)
        
        payment_result = await payment_service.create_yookassa_sbp_payment(
            db=db,
            user_id=db_user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
            receipt_email=None,
            receipt_phone=None,
            metadata={
                "user_telegram_id": str(db_user.telegram_id),
                "user_username": db_user.username or "",
                "purpose": "balance_topup_sbp"
            }
        )
        
        if not payment_result:
            await message.answer("❌ Ошибка создания платежа через СБП. Попробуйте позже или обратитесь в поддержку.")
            await state.clear()
            return
        
        confirmation_url = payment_result.get("confirmation_url")
        if not confirmation_url:
            await message.answer("❌ Ошибка получения ссылки для оплаты через СБП. Обратитесь в поддержку.")
            await state.clear()
            return
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🏦 Оплатить через СБП", url=confirmation_url)],
            [types.InlineKeyboardButton(text="📊 Проверить статус", callback_data=f"check_yookassa_{payment_result['local_payment_id']}")],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")]
        ])
        
        await message.answer(
            f"🏦 <b>Оплата через СБП</b>\n\n"
            f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
            f"🆔 ID платежа: {payment_result['yookassa_payment_id'][:8]}...\n\n"
            f"📱 <b>Инструкция:</b>\n"
            f"1. Нажмите кнопку 'Оплатить через СБП'\n"
            f"2. Вас перенаправит в приложение вашего банка\n"
            f"3. Подтвердите платеж через СБП\n"
            f"4. Деньги поступят на баланс автоматически\n\n"
            f"🔒 Оплата происходит через защищенную систему YooKassa\n"
            f"✅ Принимаем СБП от всех банков-участников\n\n"
            f"❓ Если возникнут проблемы, обратитесь в {settings.get_support_contact_display_html()}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        await state.clear()
        
        logger.info(f"Создан платеж YooKassa СБП для пользователя {db_user.telegram_id}: "
                   f"{amount_kopeks//100}₽, ID: {payment_result['yookassa_payment_id']}")
        
    except Exception as e:
        logger.error(f"Ошибка создания YooKassa СБП платежа: {e}")
        await message.answer("❌ Ошибка создания платежа через СБП. Попробуйте позже или обратитесь в поддержку.")
        await state.clear()


@error_handler
async def process_mulenpay_payment_amount(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    state: FSMContext,
):
    texts = get_texts(db_user.language)

    if not settings.is_mulenpay_enabled():
        await message.answer("❌ Оплата через Mulen Pay временно недоступна")
        return

    if amount_kopeks < settings.MULENPAY_MIN_AMOUNT_KOPEKS:
        await message.answer(
            f"Минимальная сумма пополнения: {settings.format_price(settings.MULENPAY_MIN_AMOUNT_KOPEKS)}"
        )
        return

    if amount_kopeks > settings.MULENPAY_MAX_AMOUNT_KOPEKS:
        await message.answer(
            f"Максимальная сумма пополнения: {settings.format_price(settings.MULENPAY_MAX_AMOUNT_KOPEKS)}"
        )
        return

    amount_rubles = amount_kopeks / 100

    try:
        payment_service = PaymentService(message.bot)
        payment_result = await payment_service.create_mulenpay_payment(
            db=db,
            user_id=db_user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
            language=db_user.language,
        )

        if not payment_result or not payment_result.get("payment_url"):
            await message.answer(
                texts.t(
                    "MULENPAY_PAYMENT_ERROR",
                    "❌ Ошибка создания платежа Mulen Pay. Попробуйте позже или обратитесь в поддержку.",
                )
            )
            await state.clear()
            return

        payment_url = payment_result.get("payment_url")
        mulen_payment_id = payment_result.get("mulen_payment_id")
        local_payment_id = payment_result.get("local_payment_id")

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            "MULENPAY_PAY_BUTTON",
                            "💳 Оплатить через Mulen Pay",
                        ),
                        url=payment_url,
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                        callback_data=f"check_mulenpay_{local_payment_id}",
                    )
                ],
                [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")],
            ]
        )

        payment_id_display = mulen_payment_id if mulen_payment_id is not None else local_payment_id

        message_template = texts.t(
            "MULENPAY_PAYMENT_INSTRUCTIONS",
            (
                "💳 <b>Оплата через Mulen Pay</b>\n\n"
                "💰 Сумма: {amount}\n"
                "🆔 ID платежа: {payment_id}\n\n"
                "📱 <b>Инструкция:</b>\n"
                "1. Нажмите кнопку ‘Оплатить через Mulen Pay’\n"
                "2. Следуйте подсказкам платежной системы\n"
                "3. Подтвердите перевод\n"
                "4. Средства зачислятся автоматически\n\n"
                "❓ Если возникнут проблемы, обратитесь в {support}"
            ),
        )

        message_text = message_template.format(
            amount=settings.format_price(amount_kopeks),
            payment_id=payment_id_display,
            support=settings.get_support_contact_display_html(),
        )

        await message.answer(
            message_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

        await state.clear()

        logger.info(
            "Создан MulenPay платеж для пользователя %s: %s₽, ID: %s",
            db_user.telegram_id,
            amount_rubles,
            payment_id_display,
        )

    except Exception as e:
        logger.error(f"Ошибка создания MulenPay платежа: {e}")
        await message.answer(
            texts.t(
                "MULENPAY_PAYMENT_ERROR",
                "❌ Ошибка создания платежа Mulen Pay. Попробуйте позже или обратитесь в поддержку.",
            )
        )
        await state.clear()


@error_handler
async def process_pal24_payment_amount(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    state: FSMContext,
):
    texts = get_texts(db_user.language)

    if not settings.is_pal24_enabled():
        await message.answer("❌ Оплата через PayPalych временно недоступна")
        return

    if amount_kopeks < settings.PAL24_MIN_AMOUNT_KOPEKS:
        min_rubles = settings.PAL24_MIN_AMOUNT_KOPEKS / 100
        await message.answer(f"❌ Минимальная сумма для оплаты через PayPalych: {min_rubles:.0f} ₽")
        return

    if amount_kopeks > settings.PAL24_MAX_AMOUNT_KOPEKS:
        max_rubles = settings.PAL24_MAX_AMOUNT_KOPEKS / 100
        await message.answer(f"❌ Максимальная сумма для оплаты через PayPalych: {max_rubles:,.0f} ₽".replace(',', ' '))
        return

    try:
        payment_service = PaymentService(message.bot)
        payment_result = await payment_service.create_pal24_payment(
            db=db,
            user_id=db_user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
            language=db_user.language,
        )

        if not payment_result:
            await message.answer(
                texts.t(
                    "PAL24_PAYMENT_ERROR",
                    "❌ Ошибка создания платежа PayPalych. Попробуйте позже или обратитесь в поддержку.",
                )
            )
            await state.clear()
            return

        sbp_url = (
            payment_result.get("sbp_url")
            or payment_result.get("transfer_url")
        )
        card_url = payment_result.get("card_url")
        fallback_url = (
            payment_result.get("link_page_url")
            or payment_result.get("link_url")
        )

        if not (sbp_url or card_url or fallback_url):
            await message.answer(
                texts.t(
                    "PAL24_PAYMENT_ERROR",
                    "❌ Ошибка создания платежа PayPalych. Попробуйте позже или обратитесь в поддержку.",
                )
            )
            await state.clear()
            return

        if not sbp_url:
            sbp_url = fallback_url

        bill_id = payment_result.get("bill_id")
        local_payment_id = payment_result.get("local_payment_id")

        pay_buttons: list[list[types.InlineKeyboardButton]] = []
        steps: list[str] = []
        step_counter = 1

        default_sbp_text = texts.t(
            "PAL24_SBP_PAY_BUTTON",
            "🏦 Оплатить через PayPalych (СБП)",
        )
        sbp_button_text = settings.get_pal24_sbp_button_text(default_sbp_text)

        if sbp_url and settings.is_pal24_sbp_button_visible():
            pay_buttons.append(
                [
                    types.InlineKeyboardButton(
                        text=sbp_button_text,
                        url=sbp_url,
                    )
                ]
            )
            steps.append(
                texts.t(
                    "PAL24_INSTRUCTION_BUTTON",
                    "{step}. Нажмите кнопку «{button}»",
                ).format(step=step_counter, button=html.escape(sbp_button_text))
            )
            step_counter += 1

        default_card_text = texts.t(
            "PAL24_CARD_PAY_BUTTON",
            "💳 Оплатить банковской картой (PayPalych)",
        )
        card_button_text = settings.get_pal24_card_button_text(default_card_text)

        if card_url and card_url != sbp_url and settings.is_pal24_card_button_visible():
            pay_buttons.append(
                [
                    types.InlineKeyboardButton(
                        text=card_button_text,
                        url=card_url,
                    )
                ]
            )
            steps.append(
                texts.t(
                    "PAL24_INSTRUCTION_BUTTON",
                    "{step}. Нажмите кнопку «{button}»",
                ).format(step=step_counter, button=html.escape(card_button_text))
            )
            step_counter += 1

        if not pay_buttons and fallback_url and settings.is_pal24_sbp_button_visible():
            pay_buttons.append(
                [
                    types.InlineKeyboardButton(
                        text=sbp_button_text,
                        url=fallback_url,
                    )
                ]
            )
            steps.append(
                texts.t(
                    "PAL24_INSTRUCTION_BUTTON",
                    "{step}. Нажмите кнопку «{button}»",
                ).format(step=step_counter, button=html.escape(sbp_button_text))
            )
            step_counter += 1

        follow_template = texts.t(
            "PAL24_INSTRUCTION_FOLLOW",
            "{step}. Следуйте подсказкам платёжной системы",
        )
        steps.append(follow_template.format(step=step_counter))
        step_counter += 1

        confirm_template = texts.t(
            "PAL24_INSTRUCTION_CONFIRM",
            "{step}. Подтвердите перевод",
        )
        steps.append(confirm_template.format(step=step_counter))
        step_counter += 1

        success_template = texts.t(
            "PAL24_INSTRUCTION_COMPLETE",
            "{step}. Средства зачислятся автоматически",
        )
        steps.append(success_template.format(step=step_counter))

        message_template = texts.t(
            "PAL24_PAYMENT_INSTRUCTIONS",
            (
                "🏦 <b>Оплата через PayPalych</b>\n\n"
                "💰 Сумма: {amount}\n"
                "🆔 ID счета: {bill_id}\n\n"
                "📱 <b>Инструкция:</b>\n{steps}\n\n"
                "❓ Если возникнут проблемы, обратитесь в {support}"
            ),
        )

        keyboard_rows = pay_buttons + [
            [
                types.InlineKeyboardButton(
                    text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                    callback_data=f"check_pal24_{local_payment_id}",
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")],
        ]

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        message_text = message_template.format(
            amount=settings.format_price(amount_kopeks),
            bill_id=bill_id,
            steps="\n".join(steps),
            support=settings.get_support_contact_display_html(),
        )

        await message.answer(
            message_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

        await state.clear()

        logger.info(
            "Создан PayPalych счет для пользователя %s: %s₽, ID: %s",
            db_user.telegram_id,
            amount_kopeks / 100,
            bill_id,
        )

    except Exception as e:
        logger.error(f"Ошибка создания PayPalych платежа: {e}")
        await message.answer(
            texts.t(
                "PAL24_PAYMENT_ERROR",
                "❌ Ошибка создания платежа PayPalych. Попробуйте позже или обратитесь в поддержку.",
            )
        )
        await state.clear()


@error_handler
async def check_yookassa_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession
):
    try:
        local_payment_id = int(callback.data.split('_')[-1])
        
        from app.database.crud.yookassa import get_yookassa_payment_by_local_id
        payment = await get_yookassa_payment_by_local_id(db, local_payment_id)
        
        if not payment:
            await callback.answer("❌ Платеж не найден", show_alert=True)
            return
        
        status_emoji = {
            "pending": "⏳",
            "waiting_for_capture": "⌛",
            "succeeded": "✅",
            "canceled": "❌",
            "failed": "❌"
        }
        
        status_text = {
            "pending": "Ожидает оплаты",
            "waiting_for_capture": "Ожидает подтверждения",
            "succeeded": "Оплачен",
            "canceled": "Отменен",
            "failed": "Ошибка"
        }
        
        emoji = status_emoji.get(payment.status, "❓")
        status = status_text.get(payment.status, "Неизвестно")
        
        message_text = (f"💳 Статус платежа:\n\n"
                       f"🆔 ID: {payment.yookassa_payment_id[:8]}...\n"
                       f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}\n"
                       f"📊 Статус: {emoji} {status}\n"
                       f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M')}\n")
        
        if payment.is_succeeded:
            message_text += "\n✅ Платеж успешно завершен!\n\nСредства зачислены на баланс."
        elif payment.is_pending:
            message_text += "\n⏳ Платеж ожидает оплаты. Нажмите кнопку 'Оплатить' выше."
        elif payment.is_failed:
            message_text += (
                f"\n❌ Платеж не прошел. Обратитесь в {settings.get_support_contact_display()}"
            )
        
        await callback.answer(message_text, show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка проверки статуса платежа: {e}")
        await callback.answer("❌ Ошибка проверки статуса", show_alert=True)


@error_handler
async def check_mulenpay_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession
):
    try:
        local_payment_id = int(callback.data.split('_')[-1])
        payment_service = PaymentService(callback.bot)
        status_info = await payment_service.get_mulenpay_payment_status(db, local_payment_id)

        if not status_info:
            await callback.answer("❌ Платеж не найден", show_alert=True)
            return

        payment = status_info["payment"]

        status_labels = {
            "created": ("⏳", "Ожидает оплаты"),
            "processing": ("⌛", "Обрабатывается"),
            "success": ("✅", "Оплачен"),
            "canceled": ("❌", "Отменен"),
            "error": ("⚠️", "Ошибка"),
            "hold": ("🔒", "Холд"),
            "unknown": ("❓", "Неизвестно"),
        }

        emoji, status_text = status_labels.get(payment.status, ("❓", "Неизвестно"))

        message_lines = [
            "💳 Статус платежа Mulen Pay:\n\n",
            f"🆔 ID: {payment.mulen_payment_id or payment.id}\n",
            f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}\n",
            f"📊 Статус: {emoji} {status_text}\n",
            f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M')}\n",
        ]

        if payment.is_paid:
            message_lines.append("\n✅ Платеж успешно завершен! Средства уже на балансе.")
        elif payment.status in {"created", "processing"}:
            message_lines.append(
                "\n⏳ Платеж еще не завершен. Завершите оплату по ссылке и проверьте статус позже."
            )
            if payment.payment_url:
                message_lines.append(f"\n🔗 Ссылка на оплату: {payment.payment_url}")
        elif payment.status in {"canceled", "error"}:
            message_lines.append(
                f"\n❌ Платеж не был завершен. Попробуйте создать новый платеж или обратитесь в {settings.get_support_contact_display()}"
            )

        message_text = "".join(message_lines)

        if len(message_text) > 190:
            await callback.message.answer(message_text)
            await callback.answer("ℹ️ Статус платежа отправлен в чат", show_alert=True)
        else:
            await callback.answer(message_text, show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка проверки статуса MulenPay: {e}")
        await callback.answer("❌ Ошибка проверки статуса", show_alert=True)


@error_handler
async def check_pal24_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.split('_')[-1])
        payment_service = PaymentService(callback.bot)
        status_info = await payment_service.get_pal24_payment_status(db, local_payment_id)

        if not status_info:
            await callback.answer("❌ Платеж не найден", show_alert=True)
            return

        payment = status_info["payment"]

        status_labels = {
            "NEW": ("⏳", "Ожидает оплаты"),
            "PROCESS": ("⌛", "Обрабатывается"),
            "SUCCESS": ("✅", "Оплачен"),
            "FAIL": ("❌", "Отменен"),
            "UNDERPAID": ("⚠️", "Недоплата"),
            "OVERPAID": ("⚠️", "Переплата"),
        }

        emoji, status_text = status_labels.get(payment.status, ("❓", "Неизвестно"))

        metadata = payment.metadata_json or {}
        links_meta = metadata.get("links") if isinstance(metadata, dict) else None
        if not isinstance(links_meta, dict):
            links_meta = {}

        sbp_link = links_meta.get("sbp") or payment.link_url
        card_link = links_meta.get("card")

        if not card_link and payment.link_page_url and payment.link_page_url != sbp_link:
            card_link = payment.link_page_url

        message_lines = [
            "🏦 Статус платежа PayPalych:",
            "",
            f"🆔 ID счета: {payment.bill_id}",
            f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}",
            f"📊 Статус: {emoji} {status_text}",
            f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M')}",
        ]

        if payment.is_paid:
            message_lines.append("")
            message_lines.append("✅ Платеж успешно завершен! Средства уже на балансе.")
        elif payment.status in {"NEW", "PROCESS"}:
            message_lines.append("")
            message_lines.append("⏳ Платеж еще не завершен. Оплатите счет и проверьте статус позже.")
            if sbp_link:
                message_lines.append("")
                message_lines.append(f"🏦 СБП: {sbp_link}")
            if card_link and card_link != sbp_link:
                message_lines.append(f"💳 Банковская карта: {card_link}")
        elif payment.status in {"FAIL", "UNDERPAID", "OVERPAID"}:
            message_lines.append("")
            message_lines.append(
                f"❌ Платеж не завершен корректно. Обратитесь в {settings.get_support_contact_display()}"
            )

        from app.localization.texts import get_texts
        db_user = getattr(callback, 'db_user', None)
        texts = get_texts(db_user.language if db_user else 'ru') if db_user else get_texts('ru')

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"), callback_data=f"check_pal24_{local_payment_id}")],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")]
        ])
        
        await callback.answer()
        await callback.message.edit_text(
            "\n".join(message_lines),
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.error(f"Ошибка проверки статуса PayPalych: {e}")
        await callback.answer("❌ Ошибка проверки статуса", show_alert=True)


@error_handler
async def start_cryptobot_payment(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    if not settings.is_cryptobot_enabled():
        await callback.answer("❌ Оплата криптовалютой временно недоступна", show_alert=True)
        return
    
    from app.utils.currency_converter import currency_converter
    try:
        current_rate = await currency_converter.get_usd_to_rub_rate()
        rate_text = f"💱 Текущий курс: 1 USD = {current_rate:.2f} ₽"
    except Exception as e:
        logger.warning(f"Не удалось получить курс валют: {e}")
        current_rate = 95.0
        rate_text = f"💱 Курс: 1 USD ≈ {current_rate:.0f} ₽"
    
    available_assets = settings.get_cryptobot_assets()
    assets_text = ", ".join(available_assets)
    
    # Формируем текст сообщения в зависимости от настройки
    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        message_text = (
            f"🪙 <b>Пополнение криптовалютой</b>\n\n"
            f"Выберите сумму пополнения или введите вручную сумму "
            f"от 100 до 100,000 ₽:\n\n"
            f"💰 Доступные активы: {assets_text}\n"
            f"⚡ Мгновенное зачисление на баланс\n"
            f"🔒 Безопасная оплата через CryptoBot\n\n"
            f"{rate_text}\n"
            f"Сумма будет автоматически конвертирована в USD для оплаты."
        )
    else:
        message_text = (
            f"🪙 <b>Пополнение криптовалютой</b>\n\n"
            f"Введите сумму для пополнения от 100 до 100,000 ₽:\n\n"
            f"💰 Доступные активы: {assets_text}\n"
            f"⚡ Мгновенное зачисление на баланс\n"
            f"🔒 Безопасная оплата через CryptoBot\n\n"
            f"{rate_text}\n"
            f"Сумма будет автоматически конвертирована в USD для оплаты."
        )
    
    # Создаем клавиатуру
    keyboard = get_back_keyboard(db_user.language)
    
    # Если включен быстрый выбор суммы и не отключены кнопки, добавляем кнопки
    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        quick_amount_buttons = get_quick_amount_buttons(db_user.language)
        if quick_amount_buttons:
            # Вставляем кнопки быстрого выбора перед кнопкой "Назад"
            keyboard.inline_keyboard = quick_amount_buttons + keyboard.inline_keyboard
    
    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="cryptobot", current_rate=current_rate)
    await callback.answer()

@error_handler
async def process_cryptobot_payment_amount(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    if not settings.is_cryptobot_enabled():
        await message.answer("❌ Оплата криптовалютой временно недоступна")
        return
    
    amount_rubles = amount_kopeks / 100
    
    if amount_rubles < 100:
        await message.answer("Минимальная сумма пополнения: 100 ₽")
        return
    
    if amount_rubles > 100000:
        await message.answer("Максимальная сумма пополнения: 100,000 ₽")
        return
    
    try:
        data = await state.get_data()
        current_rate = data.get('current_rate')
        
        if not current_rate:
            from app.utils.currency_converter import currency_converter
            current_rate = await currency_converter.get_usd_to_rub_rate()
        
        amount_usd = amount_rubles / current_rate
        
        amount_usd = round(amount_usd, 2)
        
        if amount_usd < 1:
            await message.answer("❌ Минимальная сумма для оплаты в USD: 1.00 USD")
            return
        
        if amount_usd > 1000:
            await message.answer("❌ Максимальная сумма для оплаты в USD: 1,000 USD")
            return
        
        payment_service = PaymentService(message.bot)
        
        payment_result = await payment_service.create_cryptobot_payment(
            db=db,
            user_id=db_user.id,
            amount_usd=amount_usd,
            asset=settings.CRYPTOBOT_DEFAULT_ASSET,
            description=f"Пополнение баланса на {amount_rubles:.0f} ₽ ({amount_usd:.2f} USD)",
            payload=f"balance_{db_user.id}_{amount_kopeks}"
        )
        
        if not payment_result:
            await message.answer("❌ Ошибка создания платежа. Попробуйте позже или обратитесь в поддержку.")
            await state.clear()
            return
        
        bot_invoice_url = payment_result.get("bot_invoice_url")
        mini_app_invoice_url = payment_result.get("mini_app_invoice_url")
        
        payment_url = bot_invoice_url or mini_app_invoice_url
        
        if not payment_url:
            await message.answer("❌ Ошибка получения ссылки для оплаты. Обратитесь в поддержку.")
            await state.clear()
            return
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🪙 Оплатить", url=payment_url)],
            [types.InlineKeyboardButton(text="📊 Проверить статус", callback_data=f"check_cryptobot_{payment_result['local_payment_id']}")],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")]
        ])
        
        await message.answer(
            f"🪙 <b>Оплата криптовалютой</b>\n\n"
            f"💰 Сумма к зачислению: {amount_rubles:.0f} ₽\n"
            f"💵 К оплате: {amount_usd:.2f} USD\n"
            f"🪙 Актив: {payment_result['asset']}\n"
            f"💱 Курс: 1 USD = {current_rate:.2f} ₽\n"
            f"🆔 ID платежа: {payment_result['invoice_id'][:8]}...\n\n"
            f"📱 <b>Инструкция:</b>\n"
            f"1. Нажмите кнопку 'Оплатить'\n"
            f"2. Выберите удобный актив\n"
            f"3. Переведите указанную сумму\n"
            f"4. Деньги поступят на баланс автоматически\n\n"
            f"🔒 Оплата проходит через защищенную систему CryptoBot\n"
            f"⚡ Поддерживаемые активы: USDT, TON, BTC, ETH\n\n"
            f"❓ Если возникнут проблемы, обратитесь в {settings.get_support_contact_display_html()}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        await state.clear()
        
        logger.info(f"Создан CryptoBot платеж для пользователя {db_user.telegram_id}: "
                   f"{amount_rubles:.0f} ₽ ({amount_usd:.2f} USD), ID: {payment_result['invoice_id']}")
        
    except Exception as e:
        logger.error(f"Ошибка создания CryptoBot платежа: {e}")
        await message.answer("❌ Ошибка создания платежа. Попробуйте позже или обратитесь в поддержку.")
        await state.clear()

@error_handler
async def check_cryptobot_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession
):
    try:
        local_payment_id = int(callback.data.split('_')[-1])
        
        from app.database.crud.cryptobot import get_cryptobot_payment_by_id
        payment = await get_cryptobot_payment_by_id(db, local_payment_id)
        
        if not payment:
            await callback.answer("❌ Платеж не найден", show_alert=True)
            return
        
        status_emoji = {
            "active": "⏳",
            "paid": "✅",
            "expired": "❌"
        }
        
        status_text = {
            "active": "Ожидает оплаты",
            "paid": "Оплачен",
            "expired": "Истек"
        }
        
        emoji = status_emoji.get(payment.status, "❓")
        status = status_text.get(payment.status, "Неизвестно")
        
        message_text = (f"🪙 Статус платежа:\n\n"
                       f"🆔 ID: {payment.invoice_id[:8]}...\n"
                       f"💰 Сумма: {payment.amount} {payment.asset}\n"
                       f"📊 Статус: {emoji} {status}\n"
                       f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M')}\n")
        
        if payment.is_paid:
            message_text += "\n✅ Платеж успешно завершен!\n\nСредства зачислены на баланс."
        elif payment.is_pending:
            message_text += "\n⏳ Платеж ожидает оплаты. Нажмите кнопку 'Оплатить' выше."
        elif payment.is_expired:
            message_text += (
                f"\n❌ Платеж истек. Обратитесь в {settings.get_support_contact_display()}"
            )
        
        await callback.answer(message_text, show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка проверки статуса CryptoBot платежа: {e}")
        await callback.answer("❌ Ошибка проверки статуса", show_alert=True)



@error_handler
async def handle_sbp_payment(
    callback: types.CallbackQuery,
    db: AsyncSession
):
    try:
        local_payment_id = int(callback.data.split('_')[-1])
        
        from app.database.crud.yookassa import get_yookassa_payment_by_local_id
        payment = await get_yookassa_payment_by_local_id(db, local_payment_id)
        
        if not payment:
            await callback.answer("❌ Платеж не найден", show_alert=True)
            return
        
        import json
        metadata = json.loads(payment.metadata_json) if payment.metadata_json else {}
        confirmation_token = metadata.get("confirmation_token")
        
        if not confirmation_token:
            await callback.answer("❌ Токен подтверждения не найден", show_alert=True)
            return
        
        await callback.message.answer(
            f"Для оплаты через СБП откройте приложение вашего банка и подтвердите платеж.\\n\\n"
            f"Если у вас не открылось банковское приложение автоматически, вы можете:\\n"
            f"1. Скопировать этот токен: <code>{confirmation_token}</code>\\n"
            f"2. Открыть приложение вашего банка\\n"
            f"3. Найти функцию оплаты по токену\\n"
            f"4. Вставить токен и подтвердить платеж",
            parse_mode="HTML"
        )
        
        await callback.answer("Информация об оплате отправлена", show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка обработки embedded платежа СБП: {e}")
        await callback.answer("❌ Ошибка обработки платежа", show_alert=True)



@error_handler
async def handle_quick_amount_selection(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    """
    Обработчик выбора суммы через кнопки быстрого выбора
    """
    # Извлекаем сумму из callback_data
    try:
        amount_kopeks = int(callback.data.split('_')[-1])
        amount_rubles = amount_kopeks / 100
        
        # Получаем метод оплаты из состояния
        data = await state.get_data()
        payment_method = data.get("payment_method", "yookassa")
        
        # Проверяем, какой метод оплаты был выбран и вызываем соответствующий обработчик
        if payment_method == "yookassa":
            from app.database.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await process_yookassa_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif payment_method == "yookassa_sbp":
            from app.database.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await process_yookassa_sbp_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif payment_method == "mulenpay":
            from app.database.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await process_mulenpay_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif payment_method == "pal24":
            from app.database.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await process_pal24_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        else:
            await callback.answer("❌ Неизвестный способ оплаты", show_alert=True)
            return
            
    except ValueError:
        await callback.answer("❌ Ошибка обработки суммы", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка обработки быстрого выбора суммы: {e}")
        await callback.answer("❌ Ошибка обработки запроса", show_alert=True)


@error_handler
async def handle_topup_amount_callback(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    try:
        _, method, amount_str = callback.data.split("|", 2)
        amount_kopeks = int(amount_str)
    except ValueError:
        await callback.answer("❌ Некорректный запрос", show_alert=True)
        return

    if amount_kopeks <= 0:
        await callback.answer("❌ Некорректная сумма", show_alert=True)
        return

    try:
        if method == "yookassa":
            from app.database.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await process_yookassa_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif method == "yookassa_sbp":
            from app.database.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await process_yookassa_sbp_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif method == "mulenpay":
            from app.database.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await process_mulenpay_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif method == "pal24":
            from app.database.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await process_pal24_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif method == "cryptobot":
            from app.database.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await process_cryptobot_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif method == "stars":
            await process_stars_payment_amount(
                callback.message, db_user, amount_kopeks, state
            )
        elif method == "tribute":
            await start_tribute_payment(callback, db_user)
            return
        else:
            await callback.answer("❌ Неизвестный способ оплаты", show_alert=True)
            return

        await callback.answer()

    except Exception as error:
        logger.error(f"Ошибка быстрого пополнения: {error}")
        await callback.answer("❌ Ошибка обработки запроса", show_alert=True)


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_balance_menu,
        F.data == "menu_balance"
    )
    
    dp.callback_query.register(
        show_balance_history,
        F.data == "balance_history"
    )
    
    dp.callback_query.register(
        handle_balance_history_pagination,
        F.data.startswith("balance_history_page_")
    )
    
    dp.callback_query.register(
        show_payment_methods,
        F.data == "balance_topup"
    )
    
    dp.callback_query.register(
        start_stars_payment,
        F.data == "topup_stars"
    )
    
    dp.callback_query.register(
        start_yookassa_payment,
        F.data == "topup_yookassa"
    )
    
    dp.callback_query.register(
        start_yookassa_sbp_payment,
        F.data == "topup_yookassa_sbp"
    )

    dp.callback_query.register(
        start_mulenpay_payment,
        F.data == "topup_mulenpay"
    )

    dp.callback_query.register(
        start_pal24_payment,
        F.data == "topup_pal24"
    )

    dp.callback_query.register(
        check_yookassa_payment_status,
        F.data.startswith("check_yookassa_")
    )

    dp.callback_query.register(
        start_tribute_payment,
        F.data == "topup_tribute"
    )
    
    dp.callback_query.register(
        request_support_topup,
        F.data == "topup_support"
    )
    
    dp.callback_query.register(
        check_yookassa_payment_status,
        F.data.startswith("check_yookassa_")
    )
    
    dp.message.register(
        process_topup_amount,
        BalanceStates.waiting_for_amount
    )

    dp.callback_query.register(
        start_cryptobot_payment,
        F.data == "topup_cryptobot"
    )
    
    dp.callback_query.register(
        check_cryptobot_payment_status,
        F.data.startswith("check_cryptobot_")
    )

    dp.callback_query.register(
        check_mulenpay_payment_status,
        F.data.startswith("check_mulenpay_")
    )

    dp.callback_query.register(
        check_pal24_payment_status,
        F.data.startswith("check_pal24_")
    )

    dp.callback_query.register(
        handle_payment_methods_unavailable,
        F.data == "payment_methods_unavailable"
    )
    
    # Регистрируем обработчик для кнопок быстрого выбора суммы
    dp.callback_query.register(
        handle_quick_amount_selection,
        F.data.startswith("quick_amount_")
    )

    dp.callback_query.register(
        handle_topup_amount_callback,
        F.data.startswith("topup_amount|")
    )
