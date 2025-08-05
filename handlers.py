from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
import logging
import secrets
from typing import Optional, Dict, Any

from database import Database, User
from remnawave_api import RemnaWaveAPI
from keyboards import *
from translations import t
from utils import *
from config import Config

logger = logging.getLogger(__name__)

# FSM States
class BotStates(StatesGroup):
    waiting_language = State()
    waiting_amount = State()
    waiting_promocode = State()
    
    # Admin states
    admin_create_sub_name = State()
    admin_create_sub_desc = State()
    admin_create_sub_price = State()
    admin_create_sub_days = State()
    admin_create_sub_traffic = State()
    admin_create_sub_squad = State()
    
    admin_add_balance_user = State()
    admin_add_balance_amount = State()
    admin_payment_history_page = State()
    
    admin_create_promo_code = State()
    admin_create_promo_discount = State()
    admin_create_promo_limit = State()
    admin_edit_sub_value = State()
    admin_send_message_user = State()
    admin_send_message_text = State()
    admin_broadcast_text = State()
    admin_create_sub_squad_select = State()
    admin_test_monitor_user = State()

router = Router()

# Start command
@router.message(Command("start"))
async def start_command(message: Message, state: FSMContext, db: Database, **kwargs):
    """Handle /start command"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    # If user is None, try to create a minimal response
    if not user:
        logger.error(f"User is None for telegram_id {message.from_user.id}")
        await message.answer("❌ Ошибка инициализации пользователя. Попробуйте позже.")
        return
    
    # Clear any existing state
    await state.clear()
    
    if not user.language or user.language == 'ru':  # Default handling
        await message.answer(
            t('select_language'),
            reply_markup=language_keyboard()
        )
        await state.set_state(BotStates.waiting_language)
    else:
        await show_main_menu(message, user.language, user.is_admin, user.telegram_id, db, config)

# Language selection
@router.callback_query(F.data.startswith("lang_"))
async def language_callback(callback: CallbackQuery, state: FSMContext, db: Database, **kwargs):
    """Handle language selection"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    lang = callback.data.split("_")[1]
    
    # Update user language
    try:
        user.language = lang
        await db.update_user(user)
        
        # Check if this is initial language selection or language change
        current_state = await state.get_state()
        is_initial_setup = current_state == BotStates.waiting_language.state
        
        if is_initial_setup:
            await callback.message.edit_text(
                t('language_selected', lang),
                reply_markup=None
            )
            await show_main_menu(callback.message, lang, user.is_admin, user.telegram_id, db, config)
            await state.clear()
        else:
            # This is a language change from main menu
            # Проверяем, доступна ли тестовая подписка
            show_trial = False
            if config and config.TRIAL_ENABLED and db:
                try:
                    has_used = await db.has_used_trial(user.telegram_id)
                    show_trial = not has_used
                except Exception as e:
                    logger.error(f"Error checking trial availability: {e}")
            
            await callback.message.edit_text(
                t('language_changed', lang),
                reply_markup=main_menu_keyboard(lang, user.is_admin, show_trial)
            )
        
    except Exception as e:
        logger.error(f"Error updating user language: {e}")
        await callback.answer("❌ Ошибка обновления языка")

async def show_main_menu(message: Message, lang: str, is_admin: bool = False, user_id: int = None, db: Database = None, config: Config = None):
    """Show main menu"""
    try:
        show_trial = False
        
        # Проверяем, доступна ли тестовая подписка
        if config and config.TRIAL_ENABLED and user_id and db:
            has_used = await db.has_used_trial(user_id)
            show_trial = not has_used
        
        await message.answer(
            t('main_menu', lang),
            reply_markup=main_menu_keyboard(lang, is_admin, show_trial)
        )
    except Exception as e:
        logger.error(f"Error showing main menu: {e}")
        await message.answer("❌ Ошибка отображения меню")

# Main menu handlers
@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery, **kwargs):
    """Return to main menu"""
    user = kwargs.get('user')
    db = kwargs.get('db')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    show_trial = False
    
    # Проверяем, доступна ли тестовая подписка
    if config and config.TRIAL_ENABLED and db:
        try:
            has_used = await db.has_used_trial(user.telegram_id)
            show_trial = not has_used
        except Exception as e:
            logger.error(f"Error checking trial availability: {e}")
    
    await callback.message.edit_text(
        t('main_menu', user.language),
        reply_markup=main_menu_keyboard(user.language, user.is_admin, show_trial)
    )

@router.callback_query(F.data == "trial_subscription")
async def trial_subscription_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Show trial subscription info"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    if not config or not config.TRIAL_ENABLED:
        await callback.answer(t('trial_not_available', user.language))
        return
    
    try:
        # Проверяем, не использовал ли пользователь уже тестовую подписку
        has_used = await db.has_used_trial(user.telegram_id)
        if has_used:
            await callback.answer(t('trial_already_used', user.language))
            return
        
        text = t('trial_info', user.language,
            days=config.TRIAL_DURATION_DAYS,
            traffic=config.TRIAL_TRAFFIC_GB
        )
        
        await callback.message.edit_text(
            text,
            reply_markup=trial_subscription_keyboard(user.language)
        )
    except Exception as e:
        logger.error(f"Error showing trial info: {e}")
        await callback.answer(t('error_occurred', user.language))

@router.callback_query(F.data == "confirm_trial")
async def confirm_trial_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Confirm and create trial subscription"""
    user = kwargs.get('user')
    api = kwargs.get('api')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    if not config or not config.TRIAL_ENABLED:
        await callback.answer(t('trial_not_available', user.language))
        return
    
    try:
        # Проверяем еще раз, не использовал ли пользователь тестовую подписку
        has_used = await db.has_used_trial(user.telegram_id)
        if has_used:
            await callback.answer(t('trial_already_used', user.language))
            return
        
        if not api:
            logger.error("API not available in kwargs")
            await callback.message.edit_text(
                t('trial_error', user.language),
                reply_markup=main_menu_keyboard(user.language, user.is_admin)
            )
            return

        # Создаем пользователя в RemnaWave для тестовой подписки
        username = generate_username()
        password = generate_password()
        
        logger.info(f"Creating trial subscription for user {user.telegram_id}")
        
        remna_user = await api.create_user(
            username=username,
            password=password,
            traffic_limit=config.TRIAL_TRAFFIC_GB * 1024 * 1024 * 1024,
            expiry_time=calculate_expiry_date(config.TRIAL_DURATION_DAYS),
            telegram_id=user.telegram_id,
            activeInternalSquads=[config.TRIAL_SQUAD_UUID]
        )

        # Обрабатываем ответ API
        if remna_user:
            if 'data' in remna_user and 'uuid' in remna_user['data']:
                user_uuid = remna_user['data']['uuid']
                short_uuid = remna_user['data'].get('shortUuid')
            elif 'response' in remna_user and 'uuid' in remna_user['response']:
                user_uuid = remna_user['response']['uuid']
                short_uuid = remna_user['response'].get('shortUuid')
            else:
                logger.error(f"Invalid API response structure: {remna_user}")
                await callback.message.edit_text(
                    t('trial_error', user.language),
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                return

            if user_uuid:
                # Если shortUuid не получен, запрашиваем его отдельно
                if not short_uuid:
                    user_details = await api.get_user_by_uuid(user_uuid)
                    if user_details and 'shortUuid' in user_details:
                        short_uuid = user_details['shortUuid']
                
                if not short_uuid:
                    logger.error(f"Failed to get shortUuid for trial user")
                    await callback.message.edit_text(
                        t('trial_error', user.language),
                        reply_markup=main_menu_keyboard(user.language, user.is_admin)
                    )
                    return
                    
                logger.info(f"Created trial user with UUID: {user_uuid}, shortUuid: {short_uuid}")
            else:
                logger.error("Failed to create trial user in RemnaWave")
                await callback.message.edit_text(
                    t('trial_error', user.language),
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                return
        else:
            logger.error("Failed to create trial user in RemnaWave API")
            await callback.message.edit_text(
                t('trial_error', user.language),
                reply_markup=main_menu_keyboard(user.language, user.is_admin)
            )
            return

        # Создаем временную тестовую подписку, которая НЕ будет отображаться в админке
        trial_subscription = await db.create_subscription(
            name=f"Trial_{user.telegram_id}_{int(datetime.utcnow().timestamp())}",  # Уникальное имя
            description="Автоматически созданная тестовая подписка",
            price=0,
            duration_days=config.TRIAL_DURATION_DAYS,
            traffic_limit_gb=config.TRIAL_TRAFFIC_GB,
            squad_uuid=config.TRIAL_SQUAD_UUID
        )
        
        # Помечаем подписку как тестовую И неактивную для админки
        trial_subscription.is_trial = True
        trial_subscription.is_active = False  # Скрываем от обычных запросов
        await db.update_subscription(trial_subscription)

        # Создаем пользовательскую подписку
        expires_at = datetime.utcnow() + timedelta(days=config.TRIAL_DURATION_DAYS)
        
        await db.create_user_subscription(
            user_id=user.telegram_id,
            subscription_id=trial_subscription.id,
            short_uuid=short_uuid,
            expires_at=expires_at
        )
        
        # Помечаем, что пользователь использовал тестовую подписку
        await db.mark_trial_used(user.telegram_id)
        
        # Создаем запись о платеже (бесплатном)
        await db.create_payment(
            user_id=user.telegram_id,
            amount=0,
            payment_type='trial',
            description='Активация тестовой подписки',
            status='completed'
        )
        
        await callback.message.edit_text(
            t('trial_success', user.language),
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )
        
        log_user_action(user.telegram_id, "trial_subscription_activated", "Free trial")
        
    except Exception as e:
        logger.error(f"Error creating trial subscription: {e}")
        await callback.message.edit_text(
            t('trial_error', user.language),
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )

@router.callback_query(F.data == "change_language")
async def change_language_callback(callback: CallbackQuery, **kwargs):
    """Show language selection for changing language"""
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    await callback.message.edit_text(
        t('select_language'),
        reply_markup=language_keyboard()
    )

@router.callback_query(F.data == "balance")
async def balance_callback(callback: CallbackQuery, **kwargs):
    """Show balance menu"""
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    text = t('your_balance', user.language, balance=user.balance)
    await callback.message.edit_text(
        text,
        reply_markup=balance_keyboard(user.language)
    )

@router.callback_query(F.data == "topup_balance")
async def topup_balance_callback(callback: CallbackQuery, **kwargs):
    """Show top up options"""
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    await callback.message.edit_text(
        t('topup_balance', user.language),
        reply_markup=topup_keyboard(user.language)
    )

@router.callback_query(F.data == "topup_card")
async def topup_card_callback(callback: CallbackQuery, **kwargs):
    """Handle card payment"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    support_username = config.SUPPORT_USERNAME if config else 'support'
    text = t('payment_card_info', user.language, support=support_username)
    await callback.message.edit_text(
        text,
        reply_markup=back_keyboard("topup_balance", user.language)
    )

@router.callback_query(F.data == "topup_support")
async def topup_support_callback(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Handle support payment"""
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    await callback.message.edit_text(
        t('enter_amount', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.waiting_amount)

@router.message(StateFilter(BotStates.waiting_amount))
async def handle_amount(message: Message, state: FSMContext, db: Database, **kwargs):
    """Handle amount input"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await message.answer("❌ Ошибка пользователя")
        return
    
    is_valid, amount = is_valid_amount(message.text)
    
    if not is_valid:
        await message.answer(t('invalid_amount', user.language))
        return
    
    try:
        # Create payment record
        payment = await db.create_payment(
            user_id=user.telegram_id,
            amount=amount,
            payment_type='topup',
            description=f'Пополнение баланса на {amount} руб.'
        )
        
        support_username = config.SUPPORT_USERNAME if config else 'support'
        
        # Уведомляем админов о запросе на пополнение
        if config and config.ADMIN_IDS:
            admin_text = f"💰 Новый запрос на пополнение!\n\n"
            admin_text += f"👤 Пользователь: {user.first_name or 'N/A'} (@{user.username or 'N/A'})\n"
            admin_text += f"🆔 ID: {user.telegram_id}\n"
            admin_text += f"💵 Сумма: {amount} руб.\n"
            admin_text += f"📝 ID платежа: {payment.id}"
            
            # Отправляем уведомление всем админам
            from aiogram import Bot
            bot = kwargs.get('bot')
            if bot:
                for admin_id in config.ADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id, 
                            admin_text,
                            reply_markup=admin_payment_keyboard(payment.id, user.language)
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify admin {admin_id}: {e}")
        
        text = t('payment_created', user.language, support=support_username)
        await message.answer(
            text,
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )
        await state.clear()
    except Exception as e:
        logger.error(f"Error creating payment: {e}")
        await message.answer(
            t('error_occurred', user.language),
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )
        await state.clear()

@router.callback_query(F.data == "payment_history")
async def payment_history_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Show payment history"""
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        payments = await db.get_user_payments(user.telegram_id)
        
        if not payments:
            text = t('no_payments', user.language)
        else:
            text = "📊 " + t('payment_history', user.language) + ":\n\n"
            for payment in payments[:10]:  # Show last 10 payments
                date_str = format_datetime(payment.created_at, user.language)
                status = format_payment_status(payment.status, user.language)
                text += t('payment_item', user.language,
                    date=date_str,
                    amount=payment.amount,
                    description=payment.description,
                    status=status
                ) + "\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=back_keyboard("balance", user.language)
        )
    except Exception as e:
        logger.error(f"Error getting payment history: {e}")
        await callback.answer(t('error_occurred', user.language))

# Subscriptions
@router.callback_query(F.data == "buy_subscription")
async def buy_subscription_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Show available subscriptions (excluding trial)"""
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        subscriptions = await db.get_all_subscriptions(exclude_trial=True)
        
        if not subscriptions:
            await callback.message.edit_text(
                "❌ Нет доступных подписок",
                reply_markup=back_keyboard("main_menu", user.language)
            )
            return
        
        # Convert to dict format
        sub_list = []
        for sub in subscriptions:
            sub_list.append({
                'id': sub.id,
                'name': sub.name,
                'price': sub.price
            })
        
        await callback.message.edit_text(
            t('buy_subscription', user.language),
            reply_markup=subscriptions_keyboard(sub_list, user.language)
        )
    except Exception as e:
        logger.error(f"Error getting subscriptions: {e}")
        await callback.answer(t('error_occurred', user.language))

@router.callback_query(F.data.startswith("buy_sub_"))
async def buy_subscription_detail(callback: CallbackQuery, db: Database, **kwargs):
    """Show subscription details"""
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        sub_id = int(callback.data.split("_")[2])
        subscription = await db.get_subscription_by_id(sub_id)
        
        if not subscription:
            await callback.answer("❌ Подписка не найдена")
            return
        
        sub_dict = {
            'name': subscription.name,
            'price': subscription.price,
            'duration_days': subscription.duration_days,
            'traffic_limit_gb': subscription.traffic_limit_gb,
            'description': subscription.description or ''
        }
        
        text = format_subscription_info(sub_dict, user.language)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=t('buy_subscription_btn', user.language, price=subscription.price),
                callback_data=f"confirm_buy_{sub_id}"
            )],
            [InlineKeyboardButton(text=t('back', user.language), callback_data="buy_subscription")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error showing subscription detail: {e}")
        await callback.answer(t('error_occurred', user.language))

@router.callback_query(F.data.startswith("confirm_buy_"))
async def confirm_purchase(callback: CallbackQuery, db: Database, **kwargs):
    """Confirm subscription purchase"""
    user = kwargs.get('user')
    api = kwargs.get('api')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        sub_id = int(callback.data.split("_")[2])
        subscription = await db.get_subscription_by_id(sub_id)
        
        if not subscription:
            await callback.answer("❌ Подписка не найдена")
            return
        
        # Check balance
        if user.balance < subscription.price:
            await callback.answer(t('insufficient_balance', user.language))
            return
        
        # Get API from kwargs
        if not api:
            logger.error("API not available in kwargs")
            await callback.message.edit_text(
                t('purchase_error', user.language),
                reply_markup=main_menu_keyboard(user.language, user.is_admin)
            )
            return

        # ИСПРАВЛЕНИЕ: Всегда создавать НОВЫЙ пользователь в RemnaWave для каждой подписки
        # Это позволит избежать дублирования short_uuid и даст каждой подписке уникальную ссылку
        
        username = generate_username()
        password = generate_password()
        
        logger.info(f"Creating new RemnaWave user for subscription {subscription.name}")
        
        remna_user = await api.create_user(
            username=username,
            password=password,
            traffic_limit=subscription.traffic_limit_gb * 1024 * 1024 * 1024 if subscription.traffic_limit_gb > 0 else 0,
            expiry_time=calculate_expiry_date(subscription.duration_days),
            telegram_id=user.telegram_id,
            activeInternalSquads=[subscription.squad_uuid]
        )

        # Handle API response which may use 'data' or 'response' key
        if remna_user:
            if 'data' in remna_user and 'uuid' in remna_user['data']:
                user_uuid = remna_user['data']['uuid']
                short_uuid = remna_user['data'].get('shortUuid')
            elif 'response' in remna_user and 'uuid' in remna_user['response']:
                user_uuid = remna_user['response']['uuid']
                short_uuid = remna_user['response'].get('shortUuid')
            else:
                logger.error(f"Invalid API response structure: {remna_user}")
                await callback.message.edit_text(
                    "❌ Ошибка создания пользователя в системе",
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                return

            if user_uuid:
                # Обновляем основного пользователя только если у него еще нет remnawave_uuid
                if not user.remnawave_uuid:
                    user.remnawave_uuid = user_uuid
                    await db.update_user(user)
                
                # Если shortUuid не получен, запрашиваем его отдельно
                if not short_uuid:
                    user_details = await api.get_user_by_uuid(user_uuid)
                    if user_details and 'shortUuid' in user_details:
                        short_uuid = user_details['shortUuid']
                
                if not short_uuid:
                    logger.error(f"Failed to get shortUuid for new user")
                    await callback.message.edit_text(
                        "❌ Ошибка получения данных подписки",
                        reply_markup=main_menu_keyboard(user.language, user.is_admin)
                    )
                    return
                    
                logger.info(f"Created new user with UUID: {user_uuid}, shortUuid: {short_uuid}")
            else:
                logger.error("Failed to create user in RemnaWave")
                await callback.message.edit_text(
                    "❌ Ошибка создания пользователя",
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                return
        else:
            logger.error("Failed to create user in RemnaWave API")
            await callback.message.edit_text(
                "❌ Ошибка создания пользователя в системе",
                reply_markup=main_menu_keyboard(user.language, user.is_admin)
            )
            return

        # Deduct balance
        user.balance -= subscription.price
        await db.update_user(user)

        # Create user subscription record with the NEW short_uuid
        expires_at = datetime.utcnow() + timedelta(days=subscription.duration_days)
        
        await db.create_user_subscription(
            user_id=user.telegram_id,
            subscription_id=subscription.id,
            short_uuid=short_uuid,  # Это НОВЫЙ уникальный shortUuid
            expires_at=expires_at
        )
        
        # Create payment record
        await db.create_payment(
            user_id=user.telegram_id,
            amount=-subscription.price,
            payment_type='subscription',
            description=f'Покупка подписки: {subscription.name}',
            status='completed'
        )
        
        await callback.message.edit_text(
            t('subscription_purchased', user.language),
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )
        
        log_user_action(user.telegram_id, "subscription_purchased", f"Sub: {subscription.name}")
        
    except Exception as e:
        logger.error(f"Error purchasing subscription: {e}")
        await callback.message.edit_text(
            t('purchase_error', user.language),
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )

@router.callback_query(F.data == "my_subscriptions")
async def my_subscriptions_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Show user's subscriptions"""
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        user_subs = await db.get_user_subscriptions(user.telegram_id)
        
        if not user_subs:
            await callback.message.edit_text(
                t('no_subscriptions', user.language),
                reply_markup=back_keyboard("main_menu", user.language)
            )
            return
        
        # Get subscription details
        sub_list = []
        for user_sub in user_subs:
            subscription = await db.get_subscription_by_id(user_sub.subscription_id)
            if subscription:
                sub_list.append({
                    'id': user_sub.id,
                    'name': subscription.name
                })
        
        await callback.message.edit_text(
            t('my_subscriptions', user.language),
            reply_markup=user_subscriptions_keyboard(sub_list, user.language)
        )
    except Exception as e:
        logger.error(f"Error getting user subscriptions: {e}")
        await callback.answer(t('error_occurred', user.language))

@router.callback_query(F.data.startswith("view_sub_"))
async def view_subscription_detail(callback: CallbackQuery, db: Database, **kwargs):
    """View subscription details"""
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        user_sub_id = int(callback.data.split("_")[2])
        
        # Get user subscription
        user_subs = await db.get_user_subscriptions(user.telegram_id)
        user_sub = next((sub for sub in user_subs if sub.id == user_sub_id), None)
        
        if not user_sub:
            await callback.answer("❌ Подписка не найдена")
            return
        
        subscription = await db.get_subscription_by_id(user_sub.subscription_id)
        if not subscription:
            await callback.answer("❌ Подписка не найдена")
            return
        
        sub_dict = {
            'name': subscription.name,
            'duration_days': subscription.duration_days,
            'traffic_limit_gb': subscription.traffic_limit_gb,
            'description': subscription.description or ''
        }
        
        # Check if subscription is expiring soon (within 3 days)
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        days_until_expiry = (user_sub.expires_at - now).days
        
        # ИСПРАВЛЕНИЕ: Запрещаем продление тестовых подписок
        show_extend = (0 <= days_until_expiry <= 3 and 
                      user_sub.is_active and 
                      not subscription.is_trial)  # Добавили проверку на тестовую подписку
        
        text = format_user_subscription_info(user_sub.__dict__, sub_dict, user_sub.expires_at, user.language)
        
        # Add expiry warning if subscription expires soon (but don't show for trial)
        if show_extend:
            text += f"\n\n⚠️ {t('subscription_expires_soon', user.language, days=days_until_expiry)}"
        elif subscription.is_trial and 0 <= days_until_expiry <= 3:
            # Специальное сообщение для тестовых подписок
            text += f"\n\n ℹ️ Тестовая подписка истекает через {days_until_expiry} дн. Продление недоступно."
        
        await callback.message.edit_text(
            text,
            reply_markup=user_subscription_detail_keyboard(user_sub_id, user.language, show_extend)
        )
    except Exception as e:
        logger.error(f"Error viewing subscription detail: {e}")
        await callback.answer(t('error_occurred', user.language))

@router.callback_query(F.data.startswith("extend_sub_"))
async def extend_subscription_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Show subscription extension confirmation"""
    user = kwargs.get('user')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        user_sub_id = int(callback.data.split("_")[2])
        
        # Получаем все подписки пользователя и находим нужную
        user_subs = await db.get_user_subscriptions(user.telegram_id)
        user_sub = next((sub for sub in user_subs if sub.id == user_sub_id), None)
        
        if not user_sub:
            await callback.answer(t('subscription_not_found', user.language))
            return
        
        # Get subscription details
        subscription = await db.get_subscription_by_id(user_sub.subscription_id)
        if not subscription:
            await callback.answer(t('subscription_not_found', user.language))
            return
        
        # Check if subscription is trial (can't extend trial)
        if subscription.is_trial:
            await callback.answer("❌ Тестовую подписку нельзя продлить")
            return
        
        # Check if user has enough balance
        if user.balance < subscription.price:
            needed = subscription.price - user.balance
            text = f"❌ Недостаточно средств для продления!\n\n"
            text += f"💰 Стоимость продления: {subscription.price} руб.\n"
            text += f"💳 Ваш баланс: {user.balance} руб.\n"
            text += f"💸 Нужно пополнить: {needed} руб."
            
            await callback.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="topup_balance")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data=f"view_sub_{user_sub_id}")]
                ])
            )
            return
        
        # Show confirmation
        text = f"🔄 Продление подписки\n\n"
        text += f"📋 Подписка: {subscription.name}\n"
        text += f"💰 Стоимость: {subscription.price} руб.\n"
        text += f"⏱ Продлить на: {subscription.duration_days} дней\n"
        text += f"💳 Ваш баланс: {user.balance} руб.\n\n"
        text += f"После продления останется: {user.balance - subscription.price} руб."
        
        await callback.message.edit_text(
            text,
            reply_markup=extend_subscription_keyboard(user_sub_id, user.language)
        )
        
    except Exception as e:
        logger.error(f"Error showing extend subscription: {e}")
        await callback.answer(t('error_occurred', user.language))

@router.callback_query(F.data.startswith("confirm_extend_"))
async def confirm_extend_subscription_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Confirm subscription extension"""
    user = kwargs.get('user')
    api = kwargs.get('api')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        user_sub_id = int(callback.data.split("_")[2])
        
        # Получаем все подписки пользователя и находим нужную
        user_subs = await db.get_user_subscriptions(user.telegram_id)
        user_sub = next((sub for sub in user_subs if sub.id == user_sub_id), None)
        
        if not user_sub:
            await callback.answer(t('subscription_not_found', user.language))
            return
        
        # Get subscription details
        subscription = await db.get_subscription_by_id(user_sub.subscription_id)
        if not subscription:
            await callback.answer(t('subscription_not_found', user.language))
            return
        
        # Check if subscription is trial (can't extend trial)
        if subscription.is_trial:
            await callback.answer("❌ Тестовую подписку нельзя продлить")
            return
        
        # Check balance again
        if user.balance < subscription.price:
            await callback.answer("❌ Недостаточно средств")
            return
        
        # Calculate new expiry date
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        
        # ИСПРАВЛЕНИЕ: Правильное вычисление новой даты истечения
        # Если подписка все еще активна, продлеваем от текущей даты истечения
        # Если истекла, продлеваем от текущего момента
        if user_sub.expires_at > now:
            new_expiry = user_sub.expires_at + timedelta(days=subscription.duration_days)
        else:
            new_expiry = now + timedelta(days=subscription.duration_days)
        
        # ГЛАВНОЕ ИСПРАВЛЕНИЕ: Обновляем подписку в RemnaWave с правильными полями
        if api and user_sub.short_uuid:
            try:
                logger.info(f"Updating RemnaWave subscription for shortUuid: {user_sub.short_uuid}")
                
                # Сначала получаем информацию о пользователе по short_uuid
                remna_user_details = await api.get_user_by_short_uuid(user_sub.short_uuid)
                if remna_user_details:
                    user_uuid = remna_user_details.get('uuid')
                    if user_uuid:
                        # ИСПРАВЛЕНИЕ: Используем правильное поле для даты истечения
                        # В RemnaWave API может использоваться 'expireAt' или 'expiryTime'
                        expiry_str = new_expiry.isoformat() + 'Z'
                        
                        # Попробуем оба варианта поля даты истечения
                        update_data_v1 = {
                            'enable': True,
                            'expireAt': expiry_str  # Вариант 1
                        }
                        
                        update_data_v2 = {
                            'enable': True,
                            'expiryTime': expiry_str  # Вариант 2
                        }
                        
                        logger.info(f"Updating user {user_uuid} with new expiry: {expiry_str}")
                        
                        # Пробуем первый вариант
                        result = await api.update_user(user_uuid, update_data_v1)
                        
                        if not result:
                            # Если первый не сработал, пробуем второй
                            logger.info("Trying alternative field name 'expiryTime'")
                            result = await api.update_user(user_uuid, update_data_v2)
                        
                        if result:
                            logger.info(f"Successfully updated RemnaWave user expiry to {expiry_str}")
                        else:
                            logger.warning(f"Failed to update user in RemnaWave - trying direct API call")
                            
                            # ДОПОЛНИТЕЛЬНАЯ ПОПЫТКА: Используем специальный метод для обновления даты истечения
                            if hasattr(api, 'update_user_expiry'):
                                result = await api.update_user_expiry(user_sub.short_uuid, expiry_str)
                                if result:
                                    logger.info(f"Successfully updated expiry using update_user_expiry method")
                    else:
                        logger.warning(f"Could not get user UUID from RemnaWave response")
                else:
                    logger.warning(f"Could not find user in RemnaWave with shortUuid: {user_sub.short_uuid}")
                    
            except Exception as e:
                logger.error(f"Failed to update expiry in RemnaWave: {e}")
                # НЕ прерываем выполнение, продолжаем обновление в локальной БД
        
        # Update local database
        user_sub.expires_at = new_expiry
        user_sub.is_active = True
        await db.update_user_subscription(user_sub)
        
        # Deduct balance
        user.balance -= subscription.price
        await db.update_user(user)
        
        # Create payment record
        await db.create_payment(
            user_id=user.telegram_id,
            amount=-subscription.price,
            payment_type='subscription_extend',
            description=f'Продление подписки: {subscription.name}',
            status='completed'
        )
        
        success_text = f"✅ Подписка успешно продлена!\n\n"
        success_text += f"📋 Подписка: {subscription.name}\n"
        success_text += f"📅 Новая дата истечения: {format_datetime(new_expiry, user.language)}\n"
        success_text += f"💰 Списано: {subscription.price} руб.\n"
        success_text += f"💳 Остаток на балансе: {user.balance} руб."
        
        await callback.message.edit_text(
            success_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои подписки", callback_data="my_subscriptions")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])
        )
        
        log_user_action(user.telegram_id, "subscription_extended", f"Sub: {subscription.name}")
        
    except Exception as e:
        logger.error(f"Error extending subscription: {e}")
        await callback.message.edit_text(
            t('error_occurred', user.language),
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )


@router.callback_query(F.data.startswith("get_connection_"))
async def get_connection_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Get connection link"""
    user = kwargs.get('user')
    api = kwargs.get('api')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        user_subs = await db.get_user_subscriptions(user.telegram_id)
        
        sub_id = int(callback.data.split("_")[2])
        user_sub = next((s for s in user_subs if s.id == sub_id), None)
        if not user_sub:
            await callback.answer("❌ Подписка не найдена")
            return
        
        # Используем shortUuid напрямую
        short_uuid = user_sub.short_uuid
        
        # ИСПРАВЛЕНИЕ: Приоритет config.SUBSCRIPTION_BASE_URL
        if config and config.SUBSCRIPTION_BASE_URL:
            connection_url = f"{config.SUBSCRIPTION_BASE_URL.rstrip('/')}/sub/{short_uuid}"
            logger.info(f"Using config SUBSCRIPTION_BASE_URL: {connection_url}")
        elif api:
            # Если конфиг недоступен, используем API
            connection_url = await api.get_subscription_url(short_uuid)
            logger.info(f"Using API subscription URL: {connection_url}")
        else:
            # Последний fallback
            connection_url = f"{config.SUBSCRIPTION_BASE_URL.rstrip('/')}/sub/{short_uuid}"
            logger.warning(f"Using hardcoded fallback URL: {connection_url}")
        
        text = t('connection_link', user.language, link=connection_url)
        
        # Создаем клавиатуру с кнопкой подключения
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Подключиться", web_app=WebAppInfo(url=connection_url))],
            [InlineKeyboardButton(text=t('back', user.language), callback_data="my_subscriptions")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error getting connection link: {e}")
        await callback.answer(t('error_occurred', user.language))

@router.callback_query(F.data.startswith("connect_sub_"))
async def connect_subscription_callback(callback: CallbackQuery, db: Database, api: RemnaWaveAPI, **kwargs):
    """Handle subscription connection button"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        user_subs = await db.get_user_subscriptions(user.telegram_id)
        
        sub_id = int(callback.data.split("_")[2])
        user_sub = next((s for s in user_subs if s.id == sub_id), None)
        if not user_sub:
            await callback.answer("❌ Подписка не найдена")
            return
        
        # Получаем ссылку из RemnaWave API
        connection_url = await api.get_subscription_by_short_uuid(user_sub.short_uuid)
        
        if not connection_url:
            # Используем config.SUBSCRIPTION_BASE_URL
            subscription_base_url = config.SUBSCRIPTION_BASE_URL if config else "http://localhost"
            connection_url = f"{subscription_base_url.rstrip('/')}/sub/{user_sub.short_uuid}"
        
        # Открываем ссылку через URL кнопку
        await callback.answer(url=connection_url)
        
    except Exception as e:
        logger.error(f"Error connecting to subscription: {e}")
        await callback.answer(t('error_occurred', user.language))

# Support
@router.callback_query(F.data == "support")
async def support_callback(callback: CallbackQuery, **kwargs):
    """Show support info"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    support_username = config.SUPPORT_USERNAME if config else 'support'
    
    text = t('support_message', user.language, support=support_username)
    await callback.message.edit_text(
        text,
        reply_markup=back_keyboard("main_menu", user.language)
    )

# Promocode
@router.callback_query(F.data == "promocode")
async def promocode_callback(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Handle promocode input"""
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    await callback.message.edit_text(
        t('enter_promocode', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.waiting_promocode)

@router.message(StateFilter(BotStates.waiting_promocode))
async def handle_promocode(message: Message, state: FSMContext, db: Database, **kwargs):
    """Handle promocode input"""
    user = kwargs.get('user')
    if not user:
        await message.answer("❌ Ошибка пользователя")
        return
    
    code = message.text.strip().upper()
    
    if not validate_promocode_format(code):
        await message.answer(t('invalid_input', user.language))
        return
    
    try:
        promocode = await db.get_promocode_by_code(code)
        
        if not promocode:
            await message.answer(t('promocode_not_found', user.language))
            return
        
        # Check if promocode is active
        if not promocode.is_active:
            await message.answer(t('promocode_not_found', user.language))
            return
        
        # Check expiry
        if promocode.expires_at and promocode.expires_at < datetime.utcnow():
            await message.answer(t('promocode_expired', user.language))
            return
        
        # Check usage limit
        if promocode.used_count >= promocode.usage_limit:
            await message.answer(t('promocode_limit', user.language))
            return
        
        # Check if user already used this promocode
        success = await db.use_promocode(user.telegram_id, promocode)
        
        if not success:
            await message.answer(t('promocode_used', user.language))
            return
        
        # Add to balance
        await db.add_balance(user.telegram_id, promocode.discount_amount)
        
        # Create payment record
        await db.create_payment(
            user_id=user.telegram_id,
            amount=promocode.discount_amount,
            payment_type='promocode',
            description=f'Промокод: {code}',
            status='completed'
        )
        
        discount_text = f"{promocode.discount_amount} руб."
        await message.answer(
            t('promocode_success', user.language, discount=discount_text),
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )
        
        await state.clear()
        log_user_action(user.telegram_id, "promocode_used", code)
        
    except Exception as e:
        logger.error(f"Error handling promocode: {e}")
        await message.answer(
            t('error_occurred', user.language),
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )
        await state.clear()
