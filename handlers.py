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
from config import *
import base64
import json
from referral_utils import (
    process_referral_rewards, 
    create_referral_from_start_param, 
    create_referral_from_promocode,
    generate_referral_link
)

logger = logging.getLogger(__name__)

# FSM States
class BotStates(StatesGroup):
    waiting_language = State()
    waiting_amount = State()
    waiting_promocode = State()
    waiting_topup_amount = State()
    
    # Admin subscription management
    admin_create_sub_name = State()
    admin_create_sub_desc = State()
    admin_create_sub_price = State()
    admin_create_sub_days = State()
    admin_create_sub_traffic = State()
    admin_create_sub_squad = State()
    admin_create_sub_squad_select = State()
    admin_edit_sub_value = State()
    
    # Admin balance management
    admin_add_balance_user = State()
    admin_add_balance_amount = State()
    admin_payment_history_page = State()
    
    # Admin promocode management
    admin_create_promo_code = State()
    admin_create_promo_discount = State()
    admin_create_promo_limit = State()
    
    # Admin messaging
    admin_send_message_user = State()
    admin_send_message_text = State()
    admin_broadcast_text = State()
    
    # Admin user management
    admin_search_user_uuid = State()
    admin_search_user_any = State()
    admin_edit_user_expiry = State()
    admin_edit_user_traffic = State()
    
    # Admin monitoring
    admin_test_monitor_user = State()

    admin_sync_single_user = State()

    admin_debug_user_structure = State()

    admin_rename_plans_confirm = State()


router = Router()

# Start command 
@router.message(Command("start"))
async def start_command(message: Message, state: FSMContext, db: Database, **kwargs):
    """Handle /start command with referral support and language memory - ИСПРАВЛЕНО"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        logger.error(f"User is None for telegram_id {message.from_user.id}")
        await message.answer("❌ Ошибка инициализации пользователя. Попробуйте позже.")
        return
    
    # Обрабатываем реферальный параметр только если есть параметры
    if message.text and len(message.text.split()) > 1:
        start_param = message.text.split()[1]
        
        # Проверяем реферальную ссылку
        if start_param.startswith("ref_"):
            try:
                referrer_id = int(start_param.replace("ref_", ""))
                
                # ЗАЩИТА: Проверяем взаимное реферальство
                existing_reverse_referral = await db.get_referral_by_referred_id(referrer_id)
                if existing_reverse_referral and existing_reverse_referral.referrer_id == user.telegram_id:
                    await message.answer(
                        "❌ Нельзя использовать ссылку человека, которого вы пригласили!\n\n"
                        "Взаимные рефералы не допускаются."
                    )
                else:
                    bot = kwargs.get('bot')
                    success = await create_referral_from_start_param(user.telegram_id, start_param, db, bot)
                    
                    if success:
                        # Уведомляем нового пользователя
                        import os
                        threshold = float(os.getenv('REFERRAL_THRESHOLD', '300.0'))
                        referred_bonus = float(os.getenv('REFERRAL_REFERRED_BONUS', '150.0'))
                        
                        await message.answer(
                            "🎁 Добро пожаловать!\n\n"
                            f"Вы перешли по реферальной ссылке! После пополнения баланса на {threshold:.0f}₽ "
                            f"вы получите бонус {referred_bonus:.0f}₽!"
                        )
                    elif not success:
                        # Проверяем причину неудачи
                        existing_referral = await db.get_referral_by_referred_id(user.telegram_id)
                        if existing_referral:
                            await message.answer("ℹ️ Вы уже использовали реферальную ссылку ранее.")
            except (ValueError, TypeError):
                # Неверный формат ссылки, просто игнорируем
                pass
    
    # Очищаем состояние
    await state.clear()
    
    if not user.language or user.language == 'ru' or user.language == '':
        # НОВОЕ: Проверяем, это первый запуск или пользователь уже выбирал язык
        if user.language == '' or user.language is None:
            # Первый запуск - показываем выбор языка
            await message.answer(
                t('select_language'),
                reply_markup=language_keyboard()
            )
            await state.set_state(BotStates.waiting_language)
            return
        else:
            # Язык уже выбран (ru) - показываем главное меню
            await show_main_menu(message, user.language, user.is_admin, user.telegram_id, db, config)
    else:
        # У пользователя уже установлен язык - показываем главное меню
        await show_main_menu(message, user.language, user.is_admin, user.telegram_id, db, config)


async def process_referral_rewards(user_id: int, amount: float, payment_id: int, db: Database, bot=None):
    """Process referral rewards after successful payment"""
    try:
        # Проверяем есть ли у пользователя реферер
        referral = await db.get_referral_by_referred_id(user_id)
        
        if not referral:
            return
        
        # Получаем информацию о пользователе
        user = await db.get_user_by_telegram_id(user_id)
        if not user:
            return
        
        # Проверяем первую награду (если баланс стал >= 300 и награда еще не выплачена)
        if not referral.first_reward_paid and user.balance >= 300:
            # Выплачиваем первую награду рефереру (150₽)
            success = await db.create_referral_earning(
                referrer_id=referral.referrer_id,
                referred_id=user_id,
                amount=150.0,
                earning_type='first_reward',
                related_payment_id=payment_id
            )
            
            if success and bot:
                try:
                    # Уведомляем реферера
                    await bot.send_message(
                        referral.referrer_id,
                        f"🎉 Поздравляем! Ваш реферал пополнил баланс на 300₽+\n\n"
                        f"💰 Вам начислено 150₽ за приведенного друга!\n"
                        f"Также вы будете получать 25% с каждого его платежа."
                    )
                    
                    # Уведомляем самого пользователя
                    await bot.send_message(
                        user_id,
                        f"🎁 Бонус активирован! Вам начислено 150₽ за переход по реферальной ссылке!"
                    )
                    
                    # Добавляем бонус рефералу
                    await db.add_balance(user_id, 150.0)
                    await db.create_payment(
                        user_id=user_id,
                        amount=150.0,
                        payment_type='referral',
                        description='Бонус за переход по реферальной ссылке',
                        status='completed'
                    )
                    
                except Exception as e:
                    logger.error(f"Failed to send referral notifications: {e}")
        
        # Выплачиваем процент с платежа (25%)
        if amount > 0:  # Только с положительных платежей
            percentage_reward = amount * 0.25
            
            success = await db.create_referral_earning(
                referrer_id=referral.referrer_id,
                referred_id=user_id,
                amount=percentage_reward,
                earning_type='percentage',
                related_payment_id=payment_id
            )
            
            if success and bot and percentage_reward >= 1.0:  # Уведомляем только если сумма >= 1₽
                try:
                    await bot.send_message(
                        referral.referrer_id,
                        f"💰 Реферальный доход!\n\n"
                        f"Ваш реферал совершил платеж на {amount:.2f}₽\n"
                        f"Вам начислено: {percentage_reward:.2f}₽ (25%)"
                    )
                except Exception as e:
                    logger.error(f"Failed to send percentage notification: {e}")
    
    except Exception as e:
        logger.error(f"Error processing referral rewards: {e}")

# Language selection 
@router.callback_query(F.data.startswith("lang_"))
async def language_callback(callback: CallbackQuery, state: FSMContext, db: Database, **kwargs):
    """Handle language selection - ИСПРАВЛЕНО: правильное сохранение языка"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    lang = callback.data.split("_")[1]
    
    # Update user language
    try:
        # ИСПРАВЛЕНО: Сохраняем выбранный язык в базе
        user.language = lang
        await db.update_user(user)
        logger.info(f"Updated language for user {user.telegram_id} to {lang}")
        
        # Check if this is initial language selection or language change
        current_state = await state.get_state()
        is_initial_setup = current_state == BotStates.waiting_language.state
        
        if is_initial_setup:
            # Первоначальная настройка языка
            await callback.message.edit_text(
                t('language_selected', lang),
                reply_markup=None
            )
            # ВАЖНО: Очищаем состояние и показываем главное меню
            await state.clear()
            await show_main_menu(callback.message, lang, user.is_admin, user.telegram_id, db, config)
        else:
            # Смена языка из главного меню
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

# Trial subscription handlers 
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
    """Confirm and create trial subscription - ДОБАВЛЕНА ПОДДЕРЖКА URL ИЗ API"""
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

        # Создаем временную тестовую подписку
        trial_subscription = await db.create_subscription(
            name=f"Trial_{user.telegram_id}_{int(datetime.utcnow().timestamp())}",
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
        
        # НОВОЕ: Получаем subscription URL и показываем пользователю
        success_text = t('trial_success', user.language)
        
        try:
            subscription_url = await api.get_subscription_url(short_uuid)
            if subscription_url:
                success_text += f"\n\n🔗 <a href='{subscription_url}'>Нажмите для подключения</a>"
                success_text += f"\n📱 Скопируйте ссылку и импортируйте конфигурацию в ваше VPN приложение"
        except Exception as e:
            logger.warning(f"Could not get trial subscription URL: {e}")
        
        await callback.message.edit_text(
            success_text,
            reply_markup=main_menu_keyboard(user.language, user.is_admin),
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        
        log_user_action(user.telegram_id, "trial_subscription_activated", "Free trial")
        
    except Exception as e:
        logger.error(f"Error creating trial subscription: {e}")
        await callback.message.edit_text(
            t('trial_error', user.language),
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )

# Balance handlers 
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

# Subscription handlers 
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
    """Confirm subscription purchase - ПОЛНОСТЬЮ ИСПРАВЛЕННАЯ ВЕРСИЯ"""
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
        
        if not api:
            logger.error("API not available in kwargs")
            await callback.message.edit_text(
                "❌ Временная ошибка сервиса. Попробуйте позже.",
                reply_markup=main_menu_keyboard(user.language, user.is_admin)
            )
            return

        # Показываем индикатор прогресса
        await callback.answer("⏳ Создаю подписку...")

        # Создаем нового пользователя в RemnaWave для каждой подписки
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

        # Handle API response
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
                    "❌ Ошибка создания подписки. Средства не списаны.",
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                return

            if user_uuid:
                # Если shortUuid не получен, запрашиваем его отдельно
                if not short_uuid:
                    try:
                        user_details = await api.get_user_by_uuid(user_uuid)
                        if user_details and 'shortUuid' in user_details:
                            short_uuid = user_details['shortUuid']
                    except Exception as e:
                        logger.error(f"Failed to get shortUuid: {e}")
                
                if not short_uuid:
                    logger.error(f"Failed to get shortUuid for new user")
                    await callback.message.edit_text(
                        "❌ Ошибка получения данных подписки. Средства не списаны.",
                        reply_markup=main_menu_keyboard(user.language, user.is_admin)
                    )
                    return
                    
                logger.info(f"Created new user with UUID: {user_uuid}, shortUuid: {short_uuid}")
            else:
                logger.error("Failed to create user in RemnaWave")
                await callback.message.edit_text(
                    "❌ Ошибка создания подписки. Средства не списаны.",
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                return
        else:
            logger.error("Failed to create user in RemnaWave API")
            await callback.message.edit_text(
                "❌ Ошибка создания подписки. Средства не списаны.",
                reply_markup=main_menu_keyboard(user.language, user.is_admin)
            )
            return

        # Deduct balance ТОЛЬКО после успешного создания пользователя
        user.balance -= subscription.price
        await db.update_user(user)

        # Create user subscription record
        expires_at = datetime.utcnow() + timedelta(days=subscription.duration_days)
        
        user_subscription = await db.create_user_subscription(
            user_id=user.telegram_id,
            subscription_id=subscription.id,
            short_uuid=short_uuid,
            expires_at=expires_at
        )
        
        # Обновляем основного пользователя только если у него еще нет remnawave_uuid
        if not user.remnawave_uuid:
            user.remnawave_uuid = user_uuid
            await db.update_user(user)
        
        # Create payment record
        payment = await db.create_payment(
            user_id=user.telegram_id,
            amount=-subscription.price,
            payment_type='subscription',
            description=f'Покупка подписки: {subscription.name}',
            status='completed'
        )
        
        # ИСПРАВЛЕНО: убираем config из вызова process_referral_rewards
        bot = kwargs.get('bot')
        await process_referral_rewards(user.telegram_id, subscription.price, payment.id, db, bot)
        
        # Формируем сообщение с URL из API
        success_text = f"✅ Подписка успешно создана!\n\n"
        success_text += f"📋 Подписка: {subscription.name}\n"
        success_text += f"⏰ Действует до: {format_date(expires_at, user.language)}\n"
        success_text += f"💰 Стоимость: {subscription.price} руб.\n"
        success_text += f"💳 Остаток: {user.balance} руб.\n\n"
        
        # Получаем subscription URL из API
        try:
            subscription_url = await api.get_subscription_url(short_uuid)
            if subscription_url:
                success_text += f"🔗 <a href='{subscription_url}'>Нажмите для подключения</a>\n\n"
                success_text += "📱 Скопируйте ссылку и импортируйте конфигурацию в ваше VPN приложение"
            else:
                success_text += "⚠️ Ссылка для подключения будет доступна в разделе 'Мои подписки'"
        except Exception as e:
            logger.warning(f"Could not get subscription URL: {e}")
            success_text += "⚠️ Ссылка для подключения будет доступна в разделе 'Мои подписки'"
        
        await callback.message.edit_text(
            success_text,
            reply_markup=main_menu_keyboard(user.language, user.is_admin),
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        
        log_user_action(user.telegram_id, "subscription_purchased", f"Sub: {subscription.name}")
        
    except Exception as e:
        logger.error(f"Error purchasing subscription: {e}", exc_info=True)
        await callback.message.edit_text(
            "❌ Произошла ошибка при создании подписки. Если средства были списаны, обратитесь в поддержку.",
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )

# My subscriptions 
@router.callback_query(F.data == "my_subscriptions")
async def my_subscriptions_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Show user's subscriptions with URLs from API - ИСПРАВЛЕНО: пометки для импортированных"""
    user = kwargs.get('user')
    api = kwargs.get('api')
    
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
        
        text = t('your_subscriptions', user.language) + "\n\n"
        
        for i, user_sub in enumerate(user_subs, 1):
            subscription = await db.get_subscription_by_id(user_sub.subscription_id)
            if not subscription:
                continue
            
            # Определяем статус
            now = datetime.utcnow()
            if user_sub.expires_at < now:
                status = "❌ Истекла"
            elif not user_sub.is_active:
                status = "⏸ Неактивна" 
            else:
                days_left = (user_sub.expires_at - now).days
                status = f"✅ Активна ({days_left} дн.)"
            
            # НОВОЕ: Помечаем импортированные подписки
            subscription_name = subscription.name
            if subscription.is_imported or subscription.name == "Старая подписка":
                subscription_name += " 🔄"  # Добавляем иконку импорта
            
            text += f"{i}. {subscription_name}\n"
            text += f"   {status}\n"
            text += f"   До: {format_date(user_sub.expires_at, user.language)}\n"
            
            # НОВОЕ: Получаем URL из API
            if user_sub.short_uuid and api:
                try:
                    subscription_url = await api.get_subscription_url(user_sub.short_uuid)
                    if subscription_url:
                        text += f"   🔗 <a href='{subscription_url}'>Подключить</a>\n"
                    else:
                        text += f"   🔗 URL недоступен\n"
                except Exception as e:
                    logger.warning(f"Could not get subscription URL for {user_sub.short_uuid}: {e}")
                    text += f"   🔗 URL недоступен\n"
            
            text += "\n"
        
        # НОВОЕ: Добавляем пояснение об иконках
        text += "ℹ️ 🔄 - импортированная из старой системы\n"
        text += "    (продление недоступно)\n\n"
        
        # Convert to old format for keyboard
        sub_list = []
        for user_sub in user_subs:
            subscription = await db.get_subscription_by_id(user_sub.subscription_id)
            if subscription:
                display_name = subscription.name
                if subscription.is_imported or subscription.name == "Старая подписка":
                    display_name += " 🔄"
                
                sub_list.append({
                    'id': user_sub.id,
                    'name': display_name
                })
        
        await callback.message.edit_text(
            text,
            reply_markup=user_subscriptions_keyboard(sub_list, user.language),
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error getting user subscriptions: {e}")
        await callback.answer(t('error_occurred', user.language))

@router.callback_query(F.data.startswith("view_sub_"))
async def view_subscription_detail(callback: CallbackQuery, db: Database, **kwargs):
    """View subscription details with URL from API - ИСПРАВЛЕНО: блокировка продления импортированных подписок"""
    user = kwargs.get('user')
    api = kwargs.get('api')
    
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
        
        # Check if subscription is expiring soon
        now = datetime.utcnow()
        days_until_expiry = (user_sub.expires_at - now).days
        
        # ИСПРАВЛЕНО: Проверяем является ли подписка импортированной
        is_imported = subscription.is_imported or subscription.price == 0
        is_trial = subscription.is_trial
        
        # Показываем кнопку продления только для обычных платных подписок
        show_extend = (0 <= days_until_expiry <= 3 and 
                      user_sub.is_active and 
                      not is_trial and 
                      not is_imported and  # НОВОЕ: блокируем продление импортированных
                      subscription.price > 0)  # НОВОЕ: блокируем продление бесплатных
        
        text = format_user_subscription_info(user_sub.__dict__, sub_dict, user_sub.expires_at, user.language)
        
        # НОВОЕ: Добавляем URL из API в детальный просмотр
        if user_sub.short_uuid and api:
            try:
                subscription_url = await api.get_subscription_url(user_sub.short_uuid)
                if subscription_url:
                    text += f"\n\n🔗 <a href='{subscription_url}'>Ссылка для подключения</a>"
            except Exception as e:
                logger.warning(f"Could not get subscription URL: {e}")
        
        # ИСПРАВЛЕНО: Разные сообщения для разных типов подписок
        if is_imported and 0 <= days_until_expiry <= 3:
            text += f"\n\n⚠️ Это импортированная подписка из старой системы.\n"
            text += f"📅 Истекает через {days_until_expiry} дн.\n"
            text += f"🛒 Для продолжения работы приобретите новый тарифный план."
        elif is_trial and 0 <= days_until_expiry <= 3:
            text += f"\n\nℹ️ Тестовая подписка истекает через {days_until_expiry} дн.\n"
            text += f"🛒 Для продолжения работы приобретите полный тарифный план."
        elif show_extend:
            text += f"\n\n⚠️ {t('subscription_expires_soon', user.language, days=days_until_expiry)}"
        
        await callback.message.edit_text(
            text,
            reply_markup=user_subscription_detail_keyboard(user_sub_id, user.language, show_extend, is_imported),
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Error viewing subscription detail: {e}")
        await callback.answer(t('error_occurred', user.language))

@router.callback_query(F.data.startswith("extend_sub_"))
async def extend_subscription_callback(callback: CallbackQuery, db: Database, **kwargs):
    user = kwargs.get('user')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        user_sub_id = int(callback.data.split("_")[2])
        
        user_subs = await db.get_user_subscriptions(user.telegram_id)
        user_sub = next((sub for sub in user_subs if sub.id == user_sub_id), None)
        
        if not user_sub:
            await callback.answer(t('subscription_not_found', user.language))
            return
        
        subscription = await db.get_subscription_by_id(user_sub.subscription_id)
        if not subscription:
            await callback.answer(t('subscription_not_found', user.language))
            return
        
        # НОВОЕ: Проверяем тип подписки
        if subscription.is_trial:
            await callback.answer("❌ Тестовую подписку нельзя продлить")
            return
        
        if subscription.is_imported or subscription.price == 0:
            await callback.message.edit_text(
                "🚫 Импортированные подписки нельзя продлить\n\n"
                "Эта подписка была перенесена из старой системы.\n"
                "После истечения срока действия приобретите новый тарифный план.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🛒 Купить новую подписку", callback_data="buy_subscription")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data=f"view_sub_{user_sub_id}")]
                ])
            )
            return
        
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
    """Confirm subscription extension - ДОБАВЛЕНА ПОДДЕРЖКА URL ИЗ API"""
    user = kwargs.get('user')
    api = kwargs.get('api')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        user_sub_id = int(callback.data.split("_")[2])
        
        user_subs = await db.get_user_subscriptions(user.telegram_id)
        user_sub = next((sub for sub in user_subs if sub.id == user_sub_id), None)
        
        if not user_sub:
            await callback.answer(t('subscription_not_found', user.language))
            return
        
        subscription = await db.get_subscription_by_id(user_sub.subscription_id)
        if not subscription:
            await callback.answer(t('subscription_not_found', user.language))
            return
        
        if subscription.is_trial:
            await callback.answer("❌ Тестовую подписку нельзя продлить")
            return
        
        if user.balance < subscription.price:
            await callback.answer("❌ Недостаточно средств")
            return
        
        # Calculate new expiry date
        now = datetime.utcnow()
        
        if user_sub.expires_at > now:
            new_expiry = user_sub.expires_at + timedelta(days=subscription.duration_days)
        else:
            new_expiry = now + timedelta(days=subscription.duration_days)
        
        # Update in RemnaWave
        if api and user_sub.short_uuid:
            try:
                logger.info(f"Updating RemnaWave subscription for shortUuid: {user_sub.short_uuid}")
                
                remna_user_details = await api.get_user_by_short_uuid(user_sub.short_uuid)
                if remna_user_details:
                    user_uuid = remna_user_details.get('uuid')
                    if user_uuid:
                        expiry_str = new_expiry.isoformat() + 'Z'
                        
                        update_data = {
                            'enable': True,
                            'expireAt': expiry_str
                        }
                        
                        logger.info(f"Updating user {user_uuid} with new expiry: {expiry_str}")
                        
                        result = await api.update_user(user_uuid, update_data)
                        
                        if not result:
                            # Try alternative field name
                            update_data['expiryTime'] = expiry_str
                            result = await api.update_user(user_uuid, update_data)
                        
                        if result:
                            logger.info(f"Successfully updated RemnaWave user expiry")
                        else:
                            logger.warning(f"Failed to update user in RemnaWave")
                            
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
        
        # НОВОЕ: Получаем обновленный URL из API
        if api and user_sub.short_uuid:
            try:
                subscription_url = await api.get_subscription_url(user_sub.short_uuid)
                if subscription_url:
                    success_text += f"\n\n🔗 <a href='{subscription_url}'>Обновленная ссылка для подключения</a>"
                    success_text += f"\n📱 Можете использовать прежнюю конфигурацию или обновить по ссылке"
            except Exception as e:
                logger.warning(f"Could not get updated subscription URL: {e}")
        
        await callback.message.edit_text(
            success_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои подписки", callback_data="my_subscriptions")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ]),
            parse_mode='HTML',
            disable_web_page_preview=True
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
    """Get connection link from API - ПОЛНОСТЬЮ ПЕРЕРАБОТАН"""
    user = kwargs.get('user')
    api = kwargs.get('api')
    
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
        
        if not user_sub.short_uuid:
            await callback.answer("❌ Данные подписки недоступны")
            return
        
        connection_url = None
        if api:
            try:
                connection_url = await api.get_subscription_url(user_sub.short_uuid)
                logger.info(f"Got subscription URL from API: {connection_url}")
            except Exception as e:
                logger.error(f"Failed to get URL from API: {e}")
        
        if not connection_url:
            await callback.message.edit_text(
                "❌ Не удалось получить ссылку для подключения\n\nПопробуйте позже или обратитесь в поддержку",
                reply_markup=back_keyboard("my_subscriptions", user.language)
            )
            return
        
        text = f"🔗 Ссылка для подключения готова!\n\n"
        text += f"📋 Подписка: {user_sub.id}\n"
        text += f"🔗 Ссылка: <code>{connection_url}</code>\n\n"
        text += f"📱 Инструкция:\n"
        text += f"1. Скопируйте ссылку выше\n"
        text += f"2. Откройте ваше VPN приложение\n"
        text += f"3. Добавьте конфигурацию по ссылке\n\n"
        text += f"💡 Или нажмите кнопку ниже для автоматического подключения"
        
        # Создаем клавиатуру с кнопкой подключения
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Подключиться автоматически", url=connection_url)],
            [InlineKeyboardButton(text="📋 Мои подписки", callback_data="my_subscriptions")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Error getting connection link: {e}")
        await callback.answer(t('error_occurred', user.language))

# Support и Promocode handlers 
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
    """Handle promocode input with referral support and mutual protection"""
    user = kwargs.get('user')
    if not user:
        await message.answer("❌ Ошибка пользователя")
        return
    
    code = message.text.strip().upper()
    
    if not validate_promocode_format(code):
        await message.answer(t('invalid_input', user.language))
        return
    
    try:
        # Сначала проверяем обычные промокоды
        promocode = await db.get_promocode_by_code(code)
        
        if promocode and promocode.is_active:
            # Проверяем условия промокода
            if promocode.expires_at and promocode.expires_at < datetime.utcnow():
                await message.answer(t('promocode_expired', user.language))
                return
            
            if promocode.used_count >= promocode.usage_limit:
                await message.answer(t('promocode_limit', user.language))
                return
            
            # Используем промокод
            success = await db.use_promocode(user.telegram_id, promocode)
            
            if not success:
                await message.answer(t('promocode_used', user.language))
                return
            
            # Добавляем на баланс
            await db.add_balance(user.telegram_id, promocode.discount_amount)
            
            # Создаем запись о платеже
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
            return
        
        # Если обычный промокод не найден, проверяем реферальные коды
        if code.startswith("REF"):
            bot = kwargs.get('bot')
            
            # Сначала проверяем на взаимное реферальство
            async with db.session_factory() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(ReferralProgram).where(ReferralProgram.referral_code == code)
                )
                referral_record = result.scalar_one_or_none()
                
                if referral_record:
                    referrer_id = referral_record.referrer_id
                    
                    # Проверяем взаимное реферальство
                    existing_reverse_referral = await db.get_referral_by_referred_id(referrer_id)
                    if existing_reverse_referral and existing_reverse_referral.referrer_id == user.telegram_id:
                        await message.answer(
                            "❌ Нельзя использовать код человека, которого вы пригласили!\n\n"
                            "Взаимные рефералы не допускаются."
                        )
                        return
            
            success = await create_referral_from_promocode(user.telegram_id, code, db, bot)
            
            if success:
                await message.answer(
                    "🎉 Реферальный код активирован!\n\n"
                    "После пополнения баланса на 200₽ вы получите бонус 150₽!",
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                
                await state.clear()
                log_user_action(user.telegram_id, "referral_code_used", code)
                return
            else:
                # Проверяем причину неудачи
                existing_referral = await db.get_referral_by_referred_id(user.telegram_id)
                if existing_referral:
                    await message.answer("❌ Вы уже использовали реферальный код!")
                else:
                    await message.answer("❌ Неверный реферальный код!")
                return
        
        # Если ничего не найдено
        await message.answer(t('promocode_not_found', user.language))
        
    except Exception as e:
        logger.error(f"Error handling promocode: {e}")
        await message.answer(
            t('error_occurred', user.language),
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )
    
    await state.clear()

@router.callback_query(F.data == "referral_program")
async def referral_program_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Show referral program info - ФИНАЛЬНАЯ ВЕРСИЯ с конфигурацией"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        # Получаем статистику пользователя
        stats = await db.get_user_referral_stats(user.telegram_id)
        
        referral_code = await get_or_create_referral_code(user.telegram_id, db)
        
        # Создаем реферальную ссылку
        bot_username = config.BOT_USERNAME if config and config.BOT_USERNAME else ""
        referral_link = ""
        if bot_username:
            referral_link = f"https://t.me/{bot_username}?start=ref_{user.telegram_id}"
        
        # Добавляем текущее время для обновления
        from datetime import datetime
        current_time = datetime.now().strftime("%H:%M")
        
        text = "🎁 **Реферальная программа**\n\n"
        
        # Условия программы - ИСПРАВЛЕНО: используем значения из конфигурации
        text += "**📋 Условия программы:**\n"
        
        # Получаем значения из конфигурации с fallback
        first_reward = config.REFERRAL_FIRST_REWARD if config else 150.0
        referred_bonus = config.REFERRAL_REFERRED_BONUS if config else 150.0
        threshold = config.REFERRAL_THRESHOLD if config else 300.0
        percentage = config.REFERRAL_PERCENTAGE if config else 0.25
        
        text += f"• Приведи друга и получи **{first_reward:.0f}₽** на баланс\n"
        text += f"• Твой друг получит **{referred_bonus:.0f}₽** после пополнения на {threshold:.0f}₽\n"  
        text += f"• С каждого платежа друга ты получаешь **{percentage*100:.0f}%**\n\n"
        
        # Статистика пользователя
        text += "**📊 Твоя статистика:**\n"
        text += f"• Приглашено: {stats['total_referrals']} человек\n"
        text += f"• Активных рефералов: {stats['active_referrals']}\n"
        text += f"• Заработано всего: {stats['total_earned']:.2f}₽\n\n"
        
        # Ссылка и промокод
        if referral_link:
            text += "**🔗 Твоя реферальная ссылка:**\n"
            text += f"`{referral_link}`\n\n"
        else:
            text += "⚠️ Реферальная ссылка недоступна (не установлен BOT_USERNAME)\n\n"
            
        text += f"**🎫 Твой промокод:** `{referral_code}`\n\n"
        text += "Отправь ссылку или промокод друзьям!"
        
        # Добавляем время обновления чтобы избежать ошибки "message is not modified"
        text += f"\n\n🕐 _Обновлено: {current_time}_"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Мои рефералы", callback_data="my_referrals")],
            [InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="referral_program")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing referral program: {e}")
        # Если не можем отредактировать сообщение, просто отвечаем на callback
        try:
            await callback.answer("✅ Статистика обновлена", show_alert=False)
        except:
            pass

async def get_or_create_referral_code(user_id: int, db: Database) -> str:
    """Get existing referral code or create new one - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    try:
        async with db.session_factory() as session:
            from sqlalchemy import select, text
            
            # Ищем код в любой записи где пользователь - реферер
            result = await session.execute(
                text("SELECT referral_code FROM referral_programs WHERE referrer_id = :user_id LIMIT 1"),
                {"user_id": user_id}
            )
            
            existing_code = result.scalar_one_or_none()
            
            if existing_code:
                logger.info(f"Found existing referral code {existing_code} for user {user_id}")
                return existing_code
        
        # Если кода нет, генерируем новый
        referral_code = await db.generate_unique_referral_code(user_id)
        
        # Создаем запись для сохранения кода
        referral = await db.create_referral(user_id, 0, referral_code)
        
        if referral:
            logger.info(f"Created new referral code {referral_code} for user {user_id}")
            return referral_code
        else:
            logger.warning(f"Failed to create referral code for user {user_id}")
            return f"REF{user_id}"
        
    except Exception as e:
        logger.error(f"Error getting/creating referral code for user {user_id}: {e}")
        return f"REF{user_id}"

@router.callback_query(F.data == "my_referrals")
async def my_referrals_callback(callback: CallbackQuery, db: Database, **kwargs):
    user = kwargs.get('user')
    config = kwargs.get('config')  
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        referrals = await db.get_user_referrals(user.telegram_id)
        
        placeholder_id = 999999999 - user.telegram_id
        real_referrals = []
        
        for referral in referrals:
            # Пропускаем только конкретный placeholder этого пользователя
            if referral.referred_id == placeholder_id or referral.referred_id == 0:
                continue
                
            real_referrals.append(referral)
        
        if not real_referrals:
            text = "👥 У вас пока нет рефералов\n\n"
            text += "Поделитесь своей реферальной ссылкой с друзьями!"
        else:
            text = f"👥 Ваши рефералы ({len(real_referrals)}):\n\n"
            
            threshold = config.REFERRAL_THRESHOLD if config else 300.0
            
            for i, referral in enumerate(real_referrals[:10], 1):  # Показываем первых 10
                # Получаем полную информацию о реферале
                referred_user = await db.get_user_by_telegram_id(referral.referred_id)
                
                if referred_user:
                    # Формируем красивое отображение имени
                    display_name = ""
                    if referred_user.first_name:
                        display_name = referred_user.first_name
                        if referred_user.last_name:
                            display_name += f" {referred_user.last_name}"
                    
                    if referred_user.username:
                        if display_name:
                            display_name += f" (@{referred_user.username})"
                        else:
                            display_name = f"@{referred_user.username}"
                    
                    if not display_name:
                        display_name = f"Пользователь #{referred_user.telegram_id}"
                        
                else:
                    # Если пользователь не найден в базе бота
                    display_name = f"Пользователь ID:{referral.referred_id}"
                
                # Статус реферала
                status_icon = "✅" if referral.first_reward_paid else "⏳"
                status_text = "Активен" if referral.first_reward_paid else "Ожидает активации"
                
                # Сумма заработка
                earned_text = ""
                if referral.total_earned > 0:
                    earned_text = f" (+{referral.total_earned:.0f}₽)"
                
                text += f"{i}. {status_icon} {display_name}{earned_text}\n"
                text += f"   📅 Присоединился: {format_date(referral.created_at)}\n"
                text += f"   📊 Статус: {status_text}\n"
                
                if referral.first_reward_paid and referral.first_reward_at:
                    text += f"   💰 Первая награда: {format_date(referral.first_reward_at)}\n"
                elif not referral.first_reward_paid:
                    text += f"   ⏳ Нужно пополнить баланс на {threshold:.0f}₽\n"
                
                text += "\n"
            
            if len(real_referrals) > 10:
                text += f"... и еще {len(real_referrals) - 10} рефералов"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К программе", callback_data="referral_program")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error showing referrals: {e}")
        await callback.answer("❌ Ошибка загрузки")
