from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
import logging
import secrets
from typing import Optional, Dict, Any

from database import Database, User, ReferralProgram, ServiceRule
from remnawave_api import RemnaWaveAPI
from keyboards import *
from translations import t
from utils import *
from config import *
from stars_handlers import stars_router
import base64
import json
from referral_utils import (
    process_referral_rewards, 
    create_referral_from_start_param, 
    create_referral_from_promocode,
    generate_referral_link
)
from lucky_game import lucky_game_router, LuckyGameStates

logger = logging.getLogger(__name__)

class BotStates(StatesGroup):
    waiting_language = State()
    waiting_amount = State()
    waiting_promocode = State()
    waiting_topup_amount = State()
    admin_create_sub_name = State()
    admin_create_sub_desc = State()
    admin_create_sub_price = State()
    admin_create_sub_days = State()
    admin_create_sub_traffic = State()
    admin_create_sub_squad = State()
    admin_create_sub_squad_select = State()
    admin_edit_sub_value = State()
    admin_add_balance_user = State()
    admin_add_balance_amount = State()
    admin_payment_history_page = State()
    admin_create_promo_code = State()
    admin_create_promo_discount = State()
    admin_create_promo_limit = State()
    admin_edit_promo_value = State()
    admin_create_promo_expiry = State()
    admin_send_message_user = State()
    admin_send_message_text = State()
    admin_broadcast_text = State()
    admin_search_user_uuid = State()
    admin_search_user_any = State()
    admin_edit_user_expiry = State()
    admin_edit_user_traffic = State()
    admin_test_monitor_user = State()
    admin_sync_single_user = State()
    admin_debug_user_structure = State()
    admin_rename_plans_confirm = State()
    waiting_number_choice = State()
    waiting_rule_title = State()
    waiting_rule_content = State()
    waiting_rule_order = State()
    waiting_rule_edit_title = State()
    waiting_rule_edit_content = State()
    waiting_rule_edit_order = State()


router = Router()

@router.message(Command("start"))
async def start_command(message: Message, state: FSMContext, db: Database, **kwargs):
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        logger.error(f"User is None for telegram_id {message.from_user.id}")
        await message.answer("❌ Ошибка инициализации пользователя. Попробуйте позже.")
        return
    
    if message.text and len(message.text.split()) > 1:
        start_param = message.text.split()[1]
        
        if start_param.startswith("ref_"):
            try:
                referrer_id = int(start_param.replace("ref_", ""))
                
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
                        threshold = config.REFERRAL_THRESHOLD if config else 300.0
                        referred_bonus = config.REFERRAL_REFERRED_BONUS if config else 150.0
    
                        await message.answer(
                            "🎁 Добро пожаловать!\n\n"
                            f"Вы перешли по реферальной ссылке! После пополнения баланса на {threshold:.0f}₽ "
                            f"вы получите бонус {referred_bonus:.0f}₽!"
                        )
                    elif not success:
                        existing_referral = await db.get_referral_by_referred_id(user.telegram_id)
                        if existing_referral:
                            await message.answer("ℹ️ Вы уже использовали реферальную ссылку ранее.")
            except (ValueError, TypeError):
                pass
    
    await state.clear()
    
    if not user.language or user.language == 'ru' or user.language == '':
        if user.language == '' or user.language is None:
            await message.answer(
                t('select_language'),
                reply_markup=language_keyboard()
            )
            await state.set_state(BotStates.waiting_language)
            return
        else:
            await show_main_menu(message, user.language, user.is_admin, user.telegram_id, db, config)
    else:
        await show_main_menu(message, user.language, user.is_admin, user.telegram_id, db, config)

@router.callback_query(F.data.startswith("lang_"))
async def language_callback(callback: CallbackQuery, state: FSMContext, db: Database, **kwargs):
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    lang = callback.data.split("_")[1]
    
    try:
        user.language = lang
        await db.update_user(user)
        logger.info(f"Updated language for user {user.telegram_id} to {lang}")
        
        current_state = await state.get_state()
        is_initial_setup = current_state == BotStates.waiting_language.state
        
        if is_initial_setup:
            await callback.message.edit_text(
                t('language_selected', lang),
                reply_markup=None
            )
            await state.clear()
            await show_main_menu(callback.message, lang, user.is_admin, user.telegram_id, db, config)
        else:
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
    try:
        show_trial = False
        show_lucky_game = True 
        
        if config and config.TRIAL_ENABLED and user_id and db:
            has_used = await db.has_used_trial(user_id)
            show_trial = not has_used
        
        if config:
            show_lucky_game = getattr(config, 'LUCKY_GAME_ENABLED', True)
        
        await message.answer(
            t('main_menu', lang),
            reply_markup=main_menu_keyboard(lang, is_admin, show_trial, show_lucky_game)
        )
    except Exception as e:
        logger.error(f"Error showing main menu: {e}")
        await message.answer("❌ Ошибка отображения меню")

@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery, **kwargs):
    user = kwargs.get('user')
    db = kwargs.get('db')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    show_trial = False
    
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
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    if not config or not config.TRIAL_ENABLED:
        await callback.answer(t('trial_not_available', user.language))
        return
    
    try:
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

        trial_subscription = await db.create_subscription(
            name=f"Trial_{user.telegram_id}_{int(datetime.utcnow().timestamp())}",
            description="Автоматически созданная тестовая подписка",
            price=0,
            duration_days=config.TRIAL_DURATION_DAYS,
            traffic_limit_gb=config.TRIAL_TRAFFIC_GB,
            squad_uuid=config.TRIAL_SQUAD_UUID
        )
        
        trial_subscription.is_trial = True
        trial_subscription.is_active = False
        await db.update_subscription(trial_subscription)

        expires_at = datetime.utcnow() + timedelta(days=config.TRIAL_DURATION_DAYS)
        
        await db.create_user_subscription(
            user_id=user.telegram_id,
            subscription_id=trial_subscription.id,
            short_uuid=short_uuid,
            expires_at=expires_at
        )
        
        await db.mark_trial_used(user.telegram_id)
        
        await db.create_payment(
            user_id=user.telegram_id,
            amount=0,
            payment_type='trial',
            description='Активация тестовой подписки',
            status='completed'
        )
        
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

@router.callback_query(F.data == "change_language")
async def change_language_callback(callback: CallbackQuery, **kwargs):
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
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    stars_enabled = config and config.STARS_ENABLED and config.STARS_RATES
    
    text = t('topup_balance', user.language)
    
    if stars_enabled:
        text += "\n\n⭐ **Новинка!** Теперь можно пополнять баланс через Telegram Stars!"
        text += "\n💎 Быстро, безопасно, без комиссий!"
    
    await callback.message.edit_text(
        text,
        reply_markup=topup_keyboard(user.language),
        parse_mode='Markdown'
    )

@router.callback_query(F.data == "topup_card")
async def topup_card_callback(callback: CallbackQuery, **kwargs):
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
        payment = await db.create_payment(
            user_id=user.telegram_id,
            amount=amount,
            payment_type='topup', 
            description=f'Пополнение баланса на {amount} руб.'
        )
        
        support_username = config.SUPPORT_USERNAME if config else 'support'
        
        if config and config.ADMIN_IDS:
            admin_text = f"💰 Новый запрос на пополнение!\n\n"
            admin_text += f"👤 Пользователь: {user.first_name or 'N/A'} (@{user.username or 'N/A'})\n"
            admin_text += f"🆔 ID: {user.telegram_id}\n"
            admin_text += f"💵 Сумма: {amount} руб.\n"
            admin_text += f"📝 ID платежа: {payment.id}"
            
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
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        payments = await db.get_user_payments(user.telegram_id)
        
        star_payments = await db.get_user_star_payments(user.telegram_id, 5)
        
        if not payments and not star_payments:
            text = t('no_payments', user.language)
        else:
            text = "📊 " + t('payment_history', user.language) + ":\n\n"
            
            all_payments = []
            
            for payment in payments[:10]:
                all_payments.append({
                    'type': 'regular',
                    'date': payment.created_at,
                    'amount': payment.amount,
                    'description': payment.description,
                    'status': payment.status
                })
            
            for star_payment in star_payments:
                if star_payment.status == 'completed':
                    all_payments.append({
                        'type': 'stars',
                        'date': star_payment.completed_at or star_payment.created_at,
                        'amount': star_payment.rub_amount,
                        'description': f'Пополнение через Stars ({star_payment.stars_amount} ⭐)',
                        'status': star_payment.status,
                        'stars': star_payment.stars_amount
                    })
            
            all_payments.sort(key=lambda x: x['date'], reverse=True)
            
            for payment in all_payments[:10]:
                date_str = format_datetime(payment['date'], user.language)
                status = format_payment_status(payment['status'], user.language)
                
                if payment['type'] == 'stars':
                    text += f"⭐ +{payment['amount']:.0f}₽ ({payment['stars']} ⭐)\n"
                else:
                    amount_str = f"+{payment['amount']}" if payment['amount'] > 0 else str(payment['amount'])
                    text += f"💳 {amount_str}₽\n"
                
                text += f"   📅 {date_str} | {status}\n"
                text += f"   📝 {payment['description']}\n\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=back_keyboard("balance", user.language)
        )
    except Exception as e:
        logger.error(f"Error getting payment history: {e}")
        await callback.answer(t('error_occurred', user.language))

@router.callback_query(F.data == "buy_subscription")
async def buy_subscription_callback(callback: CallbackQuery, db: Database, **kwargs):
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

        await callback.answer("⏳ Создаю подписку...")

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

        user.balance -= subscription.price
        await db.update_user(user)

        expires_at = datetime.utcnow() + timedelta(days=subscription.duration_days)
        
        user_subscription = await db.create_user_subscription(
            user_id=user.telegram_id,
            subscription_id=subscription.id,
            short_uuid=short_uuid,
            expires_at=expires_at
        )
        
        if not user.remnawave_uuid:
            user.remnawave_uuid = user_uuid
            await db.update_user(user)
        
        payment = await db.create_payment(
            user_id=user.telegram_id,
            amount=-subscription.price, 
            payment_type='subscription', 
            description=f'Покупка подписки: {subscription.name}',
            status='completed'
        )
        
        
        success_text = f"✅ Подписка успешно создана!\n\n"
        success_text += f"📋 Подписка: {subscription.name}\n"
        success_text += f"⏰ Действует до: {format_date(expires_at, user.language)}\n"
        success_text += f"💰 Стоимость: {subscription.price} руб.\n"
        success_text += f"💳 Остаток: {user.balance} руб.\n\n"
        
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

@router.callback_query(F.data == "my_subscriptions")
async def my_subscriptions_callback(callback: CallbackQuery, db: Database, **kwargs):
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
            
            now = datetime.utcnow()
            if user_sub.expires_at < now:
                status = "❌ Истекла"
            elif not user_sub.is_active:
                status = "⏸ Неактивна" 
            else:
                days_left = (user_sub.expires_at - now).days
                status = f"✅ Активна ({days_left} дн.)"
            
            subscription_name = subscription.name
            if subscription.is_imported or subscription.name == "Старая подписка":
                subscription_name += " 🔄" 
            
            text += f"{i}. {subscription_name}\n"
            text += f"   {status}\n"
            text += f"   До: {format_date(user_sub.expires_at, user.language)}\n"
            
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
        
        now = datetime.utcnow()
        days_until_expiry = (user_sub.expires_at - now).days
        
        is_imported = subscription.is_imported or subscription.price == 0
        is_trial = subscription.is_trial
        
        show_extend = (0 <= days_until_expiry <= 3 and 
                      user_sub.is_active and 
                      not is_trial and 
                      not is_imported and 
                      subscription.price > 0) 
        
        text = format_user_subscription_info(user_sub.__dict__, sub_dict, user_sub.expires_at, user.language)
        
        if user_sub.short_uuid and api:
            try:
                subscription_url = await api.get_subscription_url(user_sub.short_uuid)
                if subscription_url:
                    text += f"\n\n🔗 <a href='{subscription_url}'>Ссылка для подключения</a>"
            except Exception as e:
                logger.warning(f"Could not get subscription URL: {e}")
        
        if is_imported and 0 <= days_until_expiry <= 3:
            text += f"\n\n📅 Истекает через {days_until_expiry} дн.\n"
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
        
        now = datetime.utcnow()
        
        if user_sub.expires_at > now:
            new_expiry = user_sub.expires_at + timedelta(days=subscription.duration_days)
        else:
            new_expiry = now + timedelta(days=subscription.duration_days)
        
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
        
        user_sub.expires_at = new_expiry
        user_sub.is_active = True
        await db.update_user_subscription(user_sub)
        
        user.balance -= subscription.price
        await db.update_user(user)
        
        payment = await db.create_payment(
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

@router.callback_query(F.data == "support")
async def support_callback(callback: CallbackQuery, **kwargs):
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
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    await state.clear()
    
    edited_message = await callback.message.edit_text(
        "🎁 Введите промокод:\n\n"
        "• Обычные промокоды (скидки)\n"
        "• Реферальные коды (REF...)\n\n"
        "ℹ️ После ввода вы вернетесь в главное меню",
        reply_markup=cancel_keyboard(user.language)
    )
    
    await state.update_data(promocode_message_id=edited_message.message_id)
    await state.set_state(BotStates.waiting_promocode)

@router.message(StateFilter(BotStates.waiting_promocode))
async def handle_promocode(message: Message, state: FSMContext, db: Database, **kwargs):
    user = kwargs.get('user')
    config = kwargs.get('config')
    bot = kwargs.get('bot')
    
    if not user:
        await message.answer("❌ Ошибка пользователя")
        await state.clear()
        return
    
    code = message.text.strip().upper()
    
    state_data = await state.get_data()
    promocode_message_id = state_data.get('promocode_message_id')
    
    if not validate_promocode_format(code):
        response_msg = await message.answer(
            "❌ Неверный формат промокода",
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )
        
        if bot and promocode_message_id:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=promocode_message_id)
            except Exception as e:
                logger.warning(f"Could not delete promocode request message: {e}")
        
        await state.clear()
        return
    
    try:
        promocode = await db.get_promocode_by_code(code)
        
        if promocode and promocode.is_active:
            if promocode.expires_at and promocode.expires_at < datetime.utcnow():
                response_msg = await message.answer(
                    "❌ Промокод истек",
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                
                if bot and promocode_message_id:
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=promocode_message_id)
                    except:
                        pass
                        
                await state.clear()
                return
            
            if promocode.used_count >= promocode.usage_limit:
                response_msg = await message.answer(
                    "❌ Лимит использования промокода исчерпан",
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                
                if bot and promocode_message_id:
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=promocode_message_id)
                    except:
                        pass
                        
                await state.clear()
                return
            
            success = await db.use_promocode(user.telegram_id, promocode)
            
            if not success:
                response_msg = await message.answer(
                    "❌ Вы уже использовали этот промокод",
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                
                if bot and promocode_message_id:
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=promocode_message_id)
                    except:
                        pass
                        
                await state.clear()
                return
            
            await db.add_balance(user.telegram_id, promocode.discount_amount)
            
            await db.create_payment(
                user_id=user.telegram_id,
                amount=promocode.discount_amount,
                payment_type='promocode',
                description=f'Промокод: {code}',
                status='completed'
            )
            
            discount_text = f"{promocode.discount_amount} руб."
            success_msg = await message.answer(
                t('promocode_success', user.language, discount=discount_text),
                reply_markup=main_menu_keyboard(user.language, user.is_admin)
            )
            
            if bot and promocode_message_id:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=promocode_message_id)
                    logger.info(f"Deleted promocode request message {promocode_message_id}")
                except Exception as e:
                    logger.warning(f"Could not delete promocode request message: {e}")
            
            await state.clear()
            log_user_action(user.telegram_id, "promocode_used", code)
            return
        
        if code.startswith("REF"):
            async with db.session_factory() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(ReferralProgram).where(ReferralProgram.referral_code == code)
                )
                referral_record = result.scalar_one_or_none()
                
                if referral_record:
                    referrer_id = referral_record.referrer_id
                    
                    existing_reverse_referral = await db.get_referral_by_referred_id(referrer_id)
                    if existing_reverse_referral and existing_reverse_referral.referrer_id == user.telegram_id:
                        response_msg = await message.answer(
                            "❌ Нельзя использовать код человека, которого вы пригласили!\n\n"
                            "Взаимные рефералы не допускаются.",
                            reply_markup=main_menu_keyboard(user.language, user.is_admin)
                        )
                        
                        if bot and promocode_message_id:
                            try:
                                await bot.delete_message(chat_id=message.chat.id, message_id=promocode_message_id)
                            except:
                                pass
                                
                        await state.clear()
                        return
            
            success = await create_referral_from_promocode(user.telegram_id, code, db, bot)
            
            if success:
                threshold = config.REFERRAL_THRESHOLD if config else 300.0
                referred_bonus = config.REFERRAL_REFERRED_BONUS if config else 150.0
                
                success_msg = await message.answer(
                    f"🎉 Реферальный код активирован!\n\n"
                    f"После пополнения баланса на {threshold:.0f}₽ вы получите бонус {referred_bonus:.0f}₽!",
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                
                if bot and promocode_message_id:
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=promocode_message_id)
                        logger.info(f"Deleted promocode request message {promocode_message_id}")
                    except Exception as e:
                        logger.warning(f"Could not delete promocode request message: {e}")
                
                await state.clear()
                log_user_action(user.telegram_id, "referral_code_used", code)
                return
            else:
                existing_referral = await db.get_referral_by_referred_id(user.telegram_id)
                if existing_referral:
                    error_text = "❌ Вы уже использовали реферальный код!"
                else:
                    error_text = "❌ Неверный реферальный код!"
                
                response_msg = await message.answer(
                    error_text,
                    reply_markup=main_menu_keyboard(user.language, user.is_admin)
                )
                
                if bot and promocode_message_id:
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=promocode_message_id)
                    except:
                        pass
                        
                await state.clear()
                return
        
        response_msg = await message.answer(
            "❌ Промокод не найден\n\n"
            "Проверьте правильность ввода и попробуйте снова через главное меню.",
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )
        
        if bot and promocode_message_id:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=promocode_message_id)
                logger.info(f"Deleted promocode request message {promocode_message_id}")
            except Exception as e:
                logger.warning(f"Could not delete promocode request message: {e}")
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error handling promocode: {e}")
        response_msg = await message.answer(
            "❌ Произошла ошибка при обработке промокода",
            reply_markup=main_menu_keyboard(user.language, user.is_admin)
        )
        
        if bot and promocode_message_id:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=promocode_message_id)
            except:
                pass
                
        await state.clear()

@router.callback_query(F.data == "referral_program")
async def referral_program_callback(callback: CallbackQuery, db: Database, **kwargs):
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        stats = await db.get_user_referral_stats(user.telegram_id)
        
        referral_code = await get_or_create_referral_code(user.telegram_id, db)
        
        bot_username = config.BOT_USERNAME if config and config.BOT_USERNAME else ""
        referral_link = ""
        if bot_username:
            referral_link = f"https://t.me/{bot_username}?start=ref_{user.telegram_id}"
        
        from datetime import datetime
        current_time = datetime.now().strftime("%H:%M")
        
        text = "🎁 **Реферальная программа**\n\n"
        
        text += "**📋 Условия программы:**\n"
        
        first_reward = config.REFERRAL_FIRST_REWARD if config else 150.0
        referred_bonus = config.REFERRAL_REFERRED_BONUS if config else 150.0
        threshold = config.REFERRAL_THRESHOLD if config else 300.0
        percentage = config.REFERRAL_PERCENTAGE if config else 0.25
        
        text += f"• Приведи друга и получи **{first_reward:.0f}₽** на баланс\n"
        text += f"• Твой друг получит **{referred_bonus:.0f}₽** после пополнения на {threshold:.0f}₽\n"  
        text += f"• С каждого **пополнения баланса** друга ты получаешь **{percentage*100:.0f}%**\n\n"
        
        text += "**📊 Твоя статистика:**\n"
        text += f"• Приглашено: {stats['total_referrals']} человек\n"
        text += f"• Активных рефералов: {stats['active_referrals']}\n"
        text += f"• Заработано всего: {stats['total_earned']:.2f}₽\n\n"
        
        if referral_link:
            text += "**🔗 Твоя реферальная ссылка:**\n"
            text += f"`{referral_link}`\n\n"
        else:
            text += "⚠️ Реферальная ссылка недоступна (не установлен BOT_USERNAME)\n\n"
            
        text += f"**🎫 Твой промокод:** `{referral_code}`\n\n"
        text += "Отправь ссылку или промокод друзьям!"
        
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
        try:
            await callback.answer("✅ Статистика обновлена", show_alert=False)
        except:
            pass

async def get_or_create_referral_code(user_id: int, db: Database) -> str:
    try:
        async with db.session_factory() as session:
            from sqlalchemy import select, text
            
            result = await session.execute(
                text("SELECT referral_code FROM referral_programs WHERE referrer_id = :user_id LIMIT 1"),
                {"user_id": user_id}
            )
            
            existing_code = result.scalar_one_or_none()
            
            if existing_code:
                logger.info(f"Found existing referral code {existing_code} for user {user_id}")
                return existing_code
        
        referral_code = await db.generate_unique_referral_code(user_id)
        
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
            if referral.referred_id == placeholder_id or referral.referred_id == 0:
                continue
                
            real_referrals.append(referral)
        
        if not real_referrals:
            text = "👥 У вас пока нет рефералов\n\n"
            text += "Поделитесь своей реферальной ссылкой с друзьями!"
        else:
            text = f"👥 Ваши рефералы ({len(real_referrals)}):\n\n"
            
            threshold = config.REFERRAL_THRESHOLD if config else 300.0
            
            for i, referral in enumerate(real_referrals[:10], 1): 
                referred_user = await db.get_user_by_telegram_id(referral.referred_id)
                
                if referred_user:
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
                    display_name = f"Пользователь ID:{referral.referred_id}"
                
                status_icon = "✅" if referral.first_reward_paid else "⏳"
                status_text = "Активен" if referral.first_reward_paid else "Ожидает активации"
                
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

@router.callback_query(F.data == "service_rules")
async def service_rules_callback(callback: CallbackQuery, db: Database, **kwargs):
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        rules = await db.get_all_service_rules(active_only=True)
        
        if not rules:
            await callback.message.edit_text(
                "📜 Правила сервиса пока не добавлены администратором.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                ])
            )
            return
        
        await show_rules_page(callback, rules, 0, user.language)
        
    except Exception as e:
        logger.error(f"Error showing service rules: {e}")
        await callback.answer("❌ Ошибка загрузки правил")

async def show_rules_page(callback: CallbackQuery, rules: List[ServiceRule], page_index: int, lang: str = 'ru'):
    if page_index < 0 or page_index >= len(rules):
        page_index = 0
    
    rule = rules[page_index]
    total_pages = len(rules)
    
    safe_title = rule.title.replace('*', '\\*').replace('_', '\\_').replace('[', '\\[').replace(']', '\\]').replace('`', '\\`')
    text = f"📜 **{safe_title}**\n\n"
    text += rule.content
    
    if total_pages > 1:
        text += f"\n\n📄 Страница {page_index + 1} из {total_pages}"
    
    if len(text) > 4000:
        text = text[:3950] + "\n\n... (текст обрезан)"
    
    try:
        await callback.message.edit_text(
            text,
            reply_markup=service_rules_keyboard(page_index, total_pages, lang),
            parse_mode='Markdown'
        )
    except Exception as markdown_error:
        logger.warning(f"Markdown parsing failed in user rules, retrying without markdown: {markdown_error}")
        try:
            clean_text = text.replace('**', '').replace('*', '').replace('_', '').replace('`', '')
            await callback.message.edit_text(
                clean_text,
                reply_markup=service_rules_keyboard(page_index, total_pages, lang)
            )
        except Exception as edit_error:
            await callback.message.answer(
                clean_text,
                reply_markup=service_rules_keyboard(page_index, total_pages, lang)
            )

@router.callback_query(F.data.startswith("rules_page_"))
async def rules_page_callback(callback: CallbackQuery, db: Database, **kwargs):
    user = kwargs.get('user')
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        page_index = int(callback.data.split("_")[-1])
        rules = await db.get_all_service_rules(active_only=True)
        
        if not rules:
            await callback.answer("❌ Правила не найдены")
            return
        
        await show_rules_page(callback, rules, page_index, user.language)
        
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing rules page number: {e}")
        await callback.answer("❌ Ошибка навигации")
    except Exception as e:
        logger.error(f"Error switching rules page: {e}")
        await callback.answer("❌ Ошибка загрузки страницы")

@router.callback_query(F.data == "cancel", StateFilter(BotStates.waiting_promocode))
async def cancel_promocode_callback(callback: CallbackQuery, state: FSMContext, **kwargs):
    user = kwargs.get('user')
    config = kwargs.get('config')
    db = kwargs.get('db')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    await state.clear()
    
    show_trial = False
    show_lucky_game = True
    
    if config and config.TRIAL_ENABLED and db:
        try:
            has_used = await db.has_used_trial(user.telegram_id)
            show_trial = not has_used
        except Exception as e:
            logger.error(f"Error checking trial availability: {e}")
    
    if config:
        show_lucky_game = getattr(config, 'LUCKY_GAME_ENABLED', True)
    
    await callback.message.edit_text(
        t('main_menu', user.language),
        reply_markup=main_menu_keyboard(user.language, user.is_admin, show_trial, show_lucky_game)
    )
