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
    
    transactions = await get_user_transactions(
        db, db_user.id, 
        limit=TRANSACTIONS_PER_PAGE,
        offset=offset
    )
    
    total_count = await get_user_transactions_count(db, db_user.id)
    
    if not transactions:
        await callback.message.edit_text(
            "📊 История операций пуста",
            reply_markup=get_back_keyboard(db_user.language)
        )
        await callback.answer()
        return
    
    text = "📊 <b>История операций</b>\n\n"
    
    for transaction in transactions:
        emoji = "💰" if transaction.type == TransactionType.DEPOSIT.value else "💸"
        amount_text = f"+{texts.format_price(transaction.amount_kopeks)}" if transaction.type == TransactionType.DEPOSIT.value else f"-{texts.format_price(transaction.amount_kopeks)}"
        
        text += f"{emoji} {amount_text}\n"
        text += f"📝 {transaction.description}\n"
        text += f"📅 {transaction.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
    
    keyboard = []
    total_pages = (total_count + TRANSACTIONS_PER_PAGE - 1) // TRANSACTIONS_PER_PAGE
    
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
    texts = get_texts(db_user.language)
    
    payment_text = """
💳 <b>Способы пополнения баланса</b>

Выберите удобный для вас способ оплаты:

⭐ <b>Telegram Stars</b> - быстро и удобно
💎 <b>Банковская карта</b> - через Tribute
🛠️ <b>Через поддержку</b> - другие способы

Выберите способ пополнения:
"""
    
    await callback.message.edit_text(
        payment_text,
        reply_markup=get_payment_methods_keyboard(0, db_user.language), 
        parse_mode="HTML"
    )
    await callback.answer()


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
    
    await callback.message.edit_text(
        texts.TOP_UP_AMOUNT,
        reply_markup=get_back_keyboard(db_user.language)
    )
    
    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="stars")
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
        
        tribute_service = TributeService()
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
            f"• Введите любую сумму от 50 ₽\n"
            f"• Безопасная оплата через Tribute\n"
            f"• Мгновенное зачисление на баланс\n"
            f"• Принимаем карты Visa, MasterCard, МИР\n\n"
            f"Нажмите кнопку для перехода к оплате:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка создания Tribute платежа: {e}")
        await callback.answer("❌ Ошибка создания платежа", show_alert=True)
    
    await callback.answer()


@error_handler
async def request_support_topup(
    callback: types.CallbackQuery,
    db_user: User
):
    texts = get_texts(db_user.language)
    
    support_text = f"""
🛠️ <b>Пополнение через поддержку</b>

Для пополнения баланса обратитесь в техподдержку:
{settings.SUPPORT_USERNAME}

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
            url=f"https://t.me/{settings.SUPPORT_USERNAME.lstrip('@')}"
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
        amount_rubles = float(message.text.replace(',', '.'))
        
        if amount_rubles < 1:
            await message.answer("❌ Минимальная сумма пополнения: 1 ₽")
            return
        
        if amount_rubles > 50000:
            await message.answer("❌ Максимальная сумма пополнения: 50,000 ₽")
            return
        
        amount_kopeks = int(amount_rubles * 100)
        data = await state.get_data()
        payment_method = data.get("payment_method", "stars")
        
        if payment_method == "stars":
            await process_stars_payment_amount(message, db_user, amount_kopeks, state)
        else:
            await message.answer("❌ Неизвестный способ оплаты")
        
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
        await message.answer("❌ Оплата Stars временно недоступна")
        return
    
    try:
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
            f"Сумма: {texts.format_price(amount_kopeks)}\n\n"
            f"Нажмите кнопку ниже для оплаты:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Ошибка создания Stars invoice: {e}")
        await message.answer("❌ Ошибка создания платежа")


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
        start_tribute_payment,
        F.data == "topup_tribute"
    )
    
    dp.callback_query.register(
        request_support_topup,
        F.data == "topup_support"
    )
    
    dp.message.register(
        process_topup_amount,
        BalanceStates.waiting_for_amount
    )
