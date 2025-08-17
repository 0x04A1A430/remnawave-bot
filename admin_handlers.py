import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta, timezone
import logging
from typing import List, Dict, Any

from database import Database, User, ReferralProgram, ReferralEarning, ServiceRule
from remnawave_api import RemnaWaveAPI
from keyboards import *
from translations import t
from utils import *
from handlers import BotStates
from referral_utils import process_referral_rewards
try:
    from api_error_handlers import (
        APIErrorHandler, safe_get_nodes, safe_get_system_users, 
        safe_restart_nodes, check_api_health, handle_api_errors
    )
except ImportError:
    logger.warning("api_error_handlers module not found, using fallback functions")
    
    async def safe_get_nodes(api):
        try:
            return True, await api.get_all_nodes() or []
        except Exception as e:
            logger.error(f"Error in safe_get_nodes: {e}")
            return False, []
    
    async def safe_get_system_users(api):
        try:
            return True, await api.get_all_system_users_full() or []
        except Exception as e:
            logger.error(f"Error in safe_get_system_users: {e}")
            return False, []
    
    async def safe_restart_nodes(api, all_nodes=True, node_id=None):
        try:
            if all_nodes:
                result = await api.restart_all_nodes()
            else:
                result = await api.restart_node(node_id)
            return bool(result), "Success" if result else "Failed"
        except Exception as e:
            logger.error(f"Error in safe_restart_nodes: {e}")
            return False, str(e)

logger = logging.getLogger(__name__)

admin_router = Router()

async def check_admin_access(callback: CallbackQuery, user: User) -> bool:
    if not user.is_admin:
        await callback.answer(t('not_admin', user.language))
        return False
    return True

@admin_router.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('admin_menu', user.language),
        reply_markup=admin_menu_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        db_stats = await db.get_stats()
        
        referral_stats = await get_referral_stats(db)
        
        lucky_stats = await db.get_lucky_game_admin_stats()
        
        recent_topups = await get_recent_topups(db)
        recent_lucky_games = await get_recent_lucky_games(db)
        recent_ref_earnings = await get_recent_referral_earnings(db)
        
        text = "📊 Расширенная статистика системы\n\n"
        
        text += "💾 База данных бота:\n"
        text += f"👥 Пользователей: {db_stats['total_users']}\n"
        text += f"📋 Подписок: {db_stats['total_subscriptions_non_trial']}\n"
        text += f"💰 Доходы: {db_stats['total_revenue']:.1f}₽\n\n"
        
        text += "👥 Реферальная программа:\n"
        text += f"🎁 Всего выплачено: {referral_stats['total_paid']:.1f}₽\n"
        text += f"👤 Активных рефереров: {referral_stats['active_referrers']}\n"
        text += f"🔥 Всего рефералов: {referral_stats['total_referrals']}\n\n"
        
        text += "🎰 Игра в удачу:\n"
        if lucky_stats and lucky_stats['total_games'] > 0:
            text += f"🎲 Всего игр: {lucky_stats['total_games']}\n"
            text += f"🏆 Выигрышей: {lucky_stats['total_wins']} ({lucky_stats['win_rate']:.1f}%)\n"
            text += f"👥 Уникальных игроков: {lucky_stats['unique_players']}\n"
            text += f"💎 Выплачено наград: {lucky_stats['total_rewards']:.1f}₽\n"
            
            if lucky_stats.get('games_today', 0) > 0:
                text += f"📅 За сегодня: {lucky_stats['games_today']} игр, {lucky_stats['wins_today']} побед\n"
        else:
            text += "🎯 Игр еще не было\n"
        text += "\n"
        
        if recent_topups:
            text += "💰 Последние 5 пополнений:\n"
            for topup in recent_topups:
                username = topup.get('username') or 'N/A'
                try:
                    date_str = format_datetime(topup['created_at'], user.language)
                except Exception:
                    date_str = str(topup['created_at'])[:16]
                text += f"• @{username}: {topup['amount']:.0f}₽ ({date_str})\n"
            text += "\n"
        
        if recent_lucky_games:
            text += "🎰 Последние 5 игр в удачу:\n"
            for game in recent_lucky_games:
                username = game.get('username') or 'N/A'
                result = "🏆" if game.get('is_winner') else "❌"
                reward = f" +{game['reward_amount']:.0f}₽" if game.get('is_winner') else ""
                try:
                    date_str = format_datetime(game['played_at'], user.language)
                except Exception:
                    date_str = str(game['played_at'])[:16]
                text += f"• {result} @{username}: #{game['chosen_number']}{reward} ({date_str})\n"
            text += "\n"
        
        if recent_ref_earnings:
            text += "🎁 Последние 5 реферальных выплат:\n"
            for earning in recent_ref_earnings:
                referrer_name = earning.get('referrer_name') or 'N/A'
                earning_type = "🎁" if earning.get('earning_type') == 'first_reward' else "💵"
                try:
                    date_str = format_datetime(earning['created_at'], user.language)
                except Exception:
                    date_str = str(earning['created_at'])[:16]
                text += f"• {earning_type} @{referrer_name}: {earning['amount']:.0f}₽ ({date_str})\n"
            text += "\n"
        
        if api:
            try:
                nodes_stats = await api.get_nodes_statistics()
                if nodes_stats and 'data' in nodes_stats:
                    nodes = nodes_stats['data']
                    online_nodes = len([n for n in nodes if n.get('status') == 'online'])
                    text += f"🖥 Ноды RemnaWave: {online_nodes}/{len(nodes)} онлайн\n"
            except Exception as e:
                logger.warning(f"Failed to get RemnaWave stats: {e}")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎰 Детали игры в удачу", callback_data="lucky_game_admin_details")],
            [InlineKeyboardButton(text="👥 Реферальная статистика", callback_data="referral_statistics")],
            [InlineKeyboardButton(text="🖥 Системная статистика", callback_data="admin_system")],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats")],
            [InlineKeyboardButton(text="🔙 " + t('back', user.language), callback_data="admin_panel")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        await callback.message.edit_text(
            "❌ " + t('error_occurred', user.language),
            reply_markup=back_keyboard("admin_panel", user.language)
        )

async def get_referral_stats(db: Database) -> Dict[str, Any]:
    try:
        async with db.session_factory() as session:
            from sqlalchemy import select, func, and_
            from database import ReferralProgram, ReferralEarning
            
            total_paid = await session.execute(
                select(func.sum(ReferralEarning.amount))
            )
            total_paid = total_paid.scalar() or 0.0
            
            active_referrers = await session.execute(
                select(func.count(func.distinct(ReferralEarning.referrer_id)))
            )
            active_referrers = active_referrers.scalar() or 0
            
            total_referrals = await session.execute(
                select(func.count(ReferralProgram.id)).where(
                    and_(
                        ReferralProgram.referred_id < 900000000,
                        ReferralProgram.referred_id > 0
                    )
                )
            )
            total_referrals = total_referrals.scalar() or 0
            
            return {
                'total_paid': total_paid,
                'active_referrers': active_referrers,
                'total_referrals': total_referrals
            }
    except Exception as e:
        logger.error(f"Error getting referral stats: {e}")
        return {'total_paid': 0.0, 'active_referrers': 0, 'total_referrals': 0}

async def get_recent_topups(db: Database) -> List[Dict[str, Any]]:
    try:
        async with db.session_factory() as session:
            from sqlalchemy import select, desc
            from database import Payment, User
            
            result = await session.execute(
                select(
                    Payment.amount,
                    Payment.created_at,
                    Payment.payment_type,
                    User.username,
                    User.first_name
                ).select_from(
                    Payment.__table__.join(User.__table__, Payment.user_id == User.telegram_id)
                ).where(
                    and_(
                        Payment.status == 'completed',
                        Payment.payment_type.in_(['topup', 'subscription', 'subscription_extend', 'promocode', 'admin_topup', 'stars'])
                    )
                ).order_by(desc(Payment.created_at)).limit(5)
            )
            
            topups = []
            for row in result.fetchall():
                topups.append({
                    'amount': row.amount,
                    'created_at': row.created_at,
                    'payment_type': row.payment_type,
                    'username': row.username,
                    'first_name': row.first_name
                })
            
            return topups
    except Exception as e:
        logger.error(f"Error getting recent topups: {e}")
        return []

async def get_recent_lucky_games(db: Database) -> List[Dict[str, Any]]:
    try:
        async with db.session_factory() as session:
            from sqlalchemy import select, desc
            from database import LuckyGame, User
            
            result = await session.execute(
                select(
                    LuckyGame.chosen_number,
                    LuckyGame.is_winner,
                    LuckyGame.reward_amount,
                    LuckyGame.played_at,
                    User.username,
                    User.first_name
                ).select_from(
                    LuckyGame.__table__.join(User.__table__, LuckyGame.user_id == User.telegram_id)
                ).order_by(desc(LuckyGame.played_at)).limit(5)
            )
            
            games = []
            for row in result.fetchall():
                games.append({
                    'chosen_number': row.chosen_number,
                    'is_winner': row.is_winner,
                    'reward_amount': row.reward_amount,
                    'played_at': row.played_at,
                    'username': row.username,
                    'first_name': row.first_name
                })
            
            return games
    except Exception as e:
        logger.error(f"Error getting recent lucky games: {e}")
        return []

async def get_recent_referral_earnings(db: Database) -> List[Dict[str, Any]]:
    try:
        async with db.session_factory() as session:
            from sqlalchemy import select, desc
            from database import ReferralEarning, User
            
            result = await session.execute(
                select(
                    ReferralEarning.amount,
                    ReferralEarning.earning_type,
                    ReferralEarning.created_at,
                    User.username,
                    User.first_name
                ).select_from(
                    ReferralEarning.__table__.join(User.__table__, ReferralEarning.referrer_id == User.telegram_id)
                ).order_by(desc(ReferralEarning.created_at)).limit(5)
            )
            
            earnings = []
            for row in result.fetchall():
                earnings.append({
                    'amount': row.amount,
                    'earning_type': row.earning_type,
                    'created_at': row.created_at,
                    'referrer_name': row.username,
                    'referrer_first_name': row.first_name
                })
            
            return earnings
    except Exception as e:
        logger.error(f"Error getting recent referral earnings: {e}")
        return []

@admin_router.callback_query(F.data == "admin_subscriptions")
async def admin_subscriptions_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('manage_subscriptions', user.language),
        reply_markup=admin_subscriptions_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "create_subscription")
async def create_subscription_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_sub_name', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_sub_name)

@admin_router.message(StateFilter(BotStates.admin_create_sub_name))
async def handle_sub_name(message: Message, state: FSMContext, user: User, **kwargs):
    name = message.text.strip()
    if not (3 <= len(name) <= 100):
        await message.answer("❌ Название должно быть от 3 до 100 символов")
        return
    
    await state.update_data(name=name)
    await message.answer(
        t('enter_sub_description', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_sub_desc)

@admin_router.message(StateFilter(BotStates.admin_create_sub_desc))
async def handle_sub_description(message: Message, state: FSMContext, user: User, **kwargs):
    description = message.text.strip()
    if len(description) > 500:
        await message.answer("❌ Описание не должно превышать 500 символов")
        return
    
    await state.update_data(description=description)
    await message.answer(
        t('enter_sub_price', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_sub_price)

@admin_router.message(StateFilter(BotStates.admin_create_sub_price))
async def handle_sub_price(message: Message, state: FSMContext, user: User, **kwargs):
    is_valid, price = is_valid_amount(message.text)
    
    if not is_valid:
        await message.answer(t('invalid_amount', user.language))
        return
    
    await state.update_data(price=price)
    await message.answer(
        t('enter_sub_days', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_sub_days)

@admin_router.message(StateFilter(BotStates.admin_create_sub_days))
async def handle_sub_days(message: Message, state: FSMContext, user: User, **kwargs):
    try:
        days = int(message.text.strip())
        if days <= 0 or days > 365:
            await message.answer("❌ Длительность должна быть от 1 до 365 дней")
            return
    except ValueError:
        await message.answer("❌ Введите число")
        return
    
    await state.update_data(days=days)
    await message.answer(
        t('enter_sub_traffic', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_sub_traffic)

@admin_router.message(StateFilter(BotStates.admin_create_sub_traffic))
async def handle_sub_traffic(message: Message, state: FSMContext, user: User, api: RemnaWaveAPI = None, **kwargs):
    try:
        traffic_gb = int(message.text.strip())
        if traffic_gb < 0 or traffic_gb > 10000:
            await message.answer("❌ Лимит трафика должен быть от 0 до 10000 ГБ")
            return
    except ValueError:
        await message.answer("❌ Введите число")
        return
    
    await state.update_data(traffic_gb=traffic_gb)
    
    if api:
        try:
            logger.info("Attempting to fetch squads from API")
            squads = await api.get_internal_squads_list()
            logger.info(f"API returned squads: {squads}")
            
            if squads and len(squads) > 0:
                logger.info(f"Found {len(squads)} squads, showing selection keyboard")
                await message.answer(
                    "📋 Выберите Squad из списка:",
                    reply_markup=squad_selection_keyboard(squads, user.language)
                )
                await state.set_state(BotStates.admin_create_sub_squad_select)
                return
            else:
                logger.warning("No squads returned from API or empty list")
        except Exception as e:
            logger.error(f"Failed to get squads from API: {e}", exc_info=True)
    else:
        logger.warning("No API instance provided")
    
    logger.info("Falling back to manual squad UUID input")
    await message.answer(
        t('enter_squad_uuid', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_sub_squad)

def squad_selection_keyboard(squads: List[Dict], language: str = 'ru') -> InlineKeyboardMarkup:
    logger.info(f"Creating squad selection keyboard for {len(squads)} squads")
    buttons = []
    
    for squad in squads:
        logger.debug(f"Processing squad: {squad}")
        
        squad_name = squad.get('name', 'Unknown Squad')
        squad_uuid = squad.get('uuid', '')
        
        if not squad_uuid:
            logger.warning(f"Squad without UUID: {squad}")
            continue
        
        if len(squad_name) > 30:
            display_name = squad_name[:27] + "..."
        else:
            display_name = squad_name
        
        info_text = ""
        if 'info' in squad:
            members_count = squad['info'].get('membersCount', 0)
            inbounds_count = squad['info'].get('inboundsCount', 0)
            info_text = f" ({members_count}👥, {inbounds_count}🔗)"
        
        button_text = f"📋 {display_name}{info_text}"
        logger.debug(f"Creating button: {button_text} -> {squad_uuid}")
            
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"select_squad_{squad_uuid}"
            )
        ])
    
    if not buttons:
        logger.warning("No valid squads found for keyboard")
        buttons.append([
            InlineKeyboardButton(
                text="✏️ Ввести UUID вручную",
                callback_data="manual_squad_input"
            )
        ])
    else:
        buttons.append([
            InlineKeyboardButton(
                text="✏️ Ввести UUID вручную",
                callback_data="manual_squad_input"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(
            text=t('cancel', language),
            callback_data="main_menu"
        )
    ])
    
    logger.info(f"Created keyboard with {len(buttons)} buttons")
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data == "manual_squad_input")
async def manual_squad_input(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_squad_uuid', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_sub_squad)

@admin_router.callback_query(F.data.startswith("select_squad_"))
async def handle_squad_selection(callback: CallbackQuery, state: FSMContext, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        squad_uuid = callback.data.replace("select_squad_", "")
        
        if not validate_squad_uuid(squad_uuid):
            await callback.answer("❌ Неверный формат UUID")
            return
        
        data = await state.get_data()
        
        subscription = await db.create_subscription(
            name=data['name'],
            description=data['description'],
            price=data['price'],
            duration_days=data['days'],
            traffic_limit_gb=data['traffic_gb'],
            squad_uuid=squad_uuid
        )
        
        await callback.message.edit_text(
            t('subscription_created', user.language),
            reply_markup=admin_menu_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "subscription_created", data['name'])
        
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        await callback.message.edit_text(
            t('error_occurred', user.language),
            reply_markup=admin_menu_keyboard(user.language)
        )
    
    await state.clear()

@admin_router.message(StateFilter(BotStates.admin_create_sub_squad))
async def handle_sub_squad(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    squad_uuid = message.text.strip()
    
    if not validate_squad_uuid(squad_uuid):
        await message.answer("❌ Неверный формат UUID")
        return
    
    data = await state.get_data()
    
    try:
        subscription = await db.create_subscription(
            name=data['name'],
            description=data['description'],
            price=data['price'],
            duration_days=data['days'],
            traffic_limit_gb=data['traffic_gb'],
            squad_uuid=squad_uuid
        )
        
        await message.answer(
            t('subscription_created', user.language),
            reply_markup=admin_menu_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "subscription_created", data['name'])
        
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        await message.answer(
            t('error_occurred', user.language),
            reply_markup=admin_menu_keyboard(user.language)
        )
    
    await state.clear()

@admin_router.callback_query(F.data == "list_admin_subscriptions")
async def list_admin_subscriptions(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        subs = await db.get_all_subscriptions(include_inactive=True, exclude_trial=True)
        if not subs:
            await callback.message.edit_text(
                "❌ Подписки не найдены",
                reply_markup=back_keyboard("admin_subscriptions", user.language)
            )
            return
        
        keyboard = admin_subscriptions_list_keyboard(subs, user.language)
        await callback.message.edit_text(
            t('subscriptions_list', user.language),
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Error listing subscriptions: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data.startswith("toggle_sub_"))
async def toggle_subscription(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        sub_id = int(callback.data.split("_")[2])
        sub = await db.get_subscription_by_id(sub_id)
        if not sub:
            await callback.answer("❌ Подписка не найдена")
            return
        
        sub.is_active = not sub.is_active
        await db.update_subscription(sub)
        
        status = t('enabled', user.language) if sub.is_active else t('disabled', user.language)
        await callback.answer(f"✅ Подписка «{sub.name}» {status}")
        
        subs = await db.get_all_subscriptions(include_inactive=True)
        await callback.message.edit_reply_markup(
            reply_markup=admin_subscriptions_list_keyboard(subs, user.language)
        )
    except Exception as e:
        logger.error(f"Error toggling subscription: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data.startswith("edit_sub_"))
async def edit_sub_menu(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        sub_id = int(callback.data.split("_")[2])
        await state.update_data(edit_sub_id=sub_id)
        
        buttons = [
            [InlineKeyboardButton(text="📝 Название", callback_data="edit_field_name")],
            [InlineKeyboardButton(text="💰 Цена", callback_data="edit_field_price")],
            [InlineKeyboardButton(text="📅 Дни", callback_data="edit_field_days")],
            [InlineKeyboardButton(text="📊 Трафик", callback_data="edit_field_traffic")],
            [InlineKeyboardButton(text="📋 Описание", callback_data="edit_field_description")],
            [InlineKeyboardButton(text=t('back', user.language), callback_data="list_admin_subscriptions")]
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text("🔧 Выберите поле для редактирования:", reply_markup=kb)
    except Exception as e:
        logger.error(f"Error showing edit menu: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data.startswith("edit_field_"))
async def ask_new_value(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    field = callback.data.split("_")[2]
    await state.update_data(edit_field=field)
    
    field_names = {
        'name': 'название',
        'price': 'цену',
        'days': 'количество дней',
        'traffic': 'лимит трафика (ГБ)',
        'description': 'описание'
    }
    
    field_name = field_names.get(field, field)
    await callback.message.edit_text(
        f"📝 Введите новое значение для поля '{field_name}':",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_edit_sub_value)
@admin_router.message(StateFilter(BotStates.admin_edit_sub_value))
async def handle_edit_value(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    data = await state.get_data()
    sub_id = data.get('edit_sub_id')
    field = data.get('edit_field')
    new_value = message.text.strip()
    
    try:
        sub = await db.get_subscription_by_id(sub_id)
        if not sub:
            await message.answer("❌ Подписка не найдена")
            await state.clear()
            return
        
        if field == 'name':
            if len(new_value) < 3 or len(new_value) > 100:
                await message.answer("❌ Название должно быть от 3 до 100 символов")
                return
            sub.name = new_value
        elif field == 'price':
            is_valid, price = is_valid_amount(new_value)
            if not is_valid:
                await message.answer(t('invalid_amount', user.language))
                return
            sub.price = price
        elif field == 'days':
            try:
                days = int(new_value)
                if days <= 0 or days > 365:
                    await message.answer("❌ Длительность должна быть от 1 до 365 дней")
                    return
                sub.duration_days = days
            except ValueError:
                await message.answer("❌ Введите число")
                return
        elif field == 'traffic':
            try:
                traffic = int(new_value)
                if traffic < 0 or traffic > 10000:
                    await message.answer("❌ Лимит трафика должен быть от 0 до 10000 ГБ")
                    return
                sub.traffic_limit_gb = traffic
            except ValueError:
                await message.answer("❌ Введите число")
                return
        elif field == 'description':
            if len(new_value) > 500:
                await message.answer("❌ Описание не должно превышать 500 символов")
                return
            sub.description = new_value
        
        await db.update_subscription(sub)
        await message.answer(
            "✅ Подписка обновлена",
            reply_markup=admin_menu_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "subscription_edited", f"Sub: {sub.name}, Field: {field}")
        
    except Exception as e:
        logger.error(f"Error updating subscription: {e}")
        await message.answer(t('error_occurred', user.language))
    
    await state.clear()

@admin_router.callback_query(F.data.startswith("delete_sub_"))
async def delete_subscription_confirm(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    sub_id = int(callback.data.split("_")[2])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_sub_{sub_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="list_admin_subscriptions")
        ]
    ])
    
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите удалить эту подписку?\nЭто действие нельзя отменить!",
        reply_markup=keyboard
    )

@admin_router.callback_query(F.data.startswith("confirm_delete_sub_"))
async def delete_subscription(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        sub_id = int(callback.data.split("_")[3])
        sub = await db.get_subscription_by_id(sub_id)
        
        if not sub:
            await callback.answer("❌ Подписка не найдена")
            return
        
        success = await db.delete_subscription(sub_id)
        
        if success:
            await callback.answer(f"✅ Подписка «{sub.name}» удалена")
            log_user_action(user.telegram_id, "subscription_deleted", sub.name)
        else:
            await callback.answer("❌ Ошибка удаления")
        
        subs = await db.get_all_subscriptions(include_inactive=True)
        if subs:
            await callback.message.edit_text(
                t('subscriptions_list', user.language),
                reply_markup=admin_subscriptions_list_keyboard(subs, user.language)
            )
        else:
            await callback.message.edit_text(
                "❌ Подписки не найдены",
                reply_markup=back_keyboard("admin_subscriptions", user.language)
            )
    except Exception as e:
        logger.error(f"Error deleting subscription: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data == "admin_users")
async def admin_users_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('manage_users', user.language),
        reply_markup=admin_users_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "list_users")
async def list_users_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        users = await db.get_all_users()
        
        if not users:
            await callback.message.edit_text(
                "❌ Пользователи не найдены",
                reply_markup=back_keyboard("admin_users", user.language)
            )
            return
        
        text = t('user_list', user.language) + "\n\n"
        
        for u in users[:20]:
            username = u.username or "N/A"
            text += t('user_item', user.language,
                id=u.telegram_id,
                username=username,
                balance=u.balance
            ) + "\n"
        
        if len(users) > 20:
            text += f"\n... и еще {len(users) - 20} пользователей"
        
        await callback.message.edit_text(
            text,
            reply_markup=back_keyboard("admin_users", user.language)
        )
        
    except Exception as e:
        logger.error(f"Error listing users: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data == "admin_balance")
async def admin_balance_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('manage_balance', user.language),
        reply_markup=admin_balance_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "admin_add_balance")
async def admin_add_balance_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_user_id', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_add_balance_user)

@admin_router.message(StateFilter(BotStates.admin_add_balance_user))
async def handle_balance_user_id(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    telegram_id = parse_telegram_id(message.text)
    
    if not telegram_id:
        await message.answer("❌ Неверный Telegram ID")
        return
    
    target_user = await db.get_user_by_telegram_id(telegram_id)
    if not target_user:
        await message.answer(t('user_not_found', user.language))
        return
    
    await state.update_data(target_user_id=telegram_id)
    await message.answer(
        t('enter_balance_amount', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_add_balance_amount)

@admin_router.message(StateFilter(BotStates.admin_add_balance_amount))
async def handle_balance_amount(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    is_valid, amount = is_valid_amount(message.text)
    
    if not is_valid:
        await message.answer(t('invalid_amount', user.language))
        return
    
    data = await state.get_data()
    target_user_id = data['target_user_id']
    
    try:
        success = await db.add_balance(target_user_id, amount)
        
        if success:
            payment = await db.create_payment(
                user_id=target_user_id,
                amount=amount,
                payment_type='admin_topup', 
                description=f'Пополнение администратором (ID: {user.telegram_id})',
                status='completed'
            )
            
            bot = kwargs.get('bot')
            await process_referral_rewards(
                target_user_id, 
                amount, 
                payment.id, 
                db, 
                bot, 
                payment_type='admin_topup'
            )
            
            await message.answer(
                t('balance_added', user.language),
                reply_markup=admin_menu_keyboard(user.language)
            )
            
            log_user_action(user.telegram_id, "admin_balance_added", f"User: {target_user_id}, Amount: {amount}")
        else:
            await message.answer(t('user_not_found', user.language))
    
    except Exception as e:
        logger.error(f"Error adding balance: {e}")
        await message.answer(t('error_occurred', user.language))
    
    await state.clear()

@admin_router.callback_query(F.data == "admin_payment_history")
async def admin_payment_history_callback(callback: CallbackQuery, user: User, db: Database, state: FSMContext, **kwargs):
    logger.info(f"admin_payment_history_callback called for user {user.telegram_id}")
    
    if not await check_admin_access(callback, user):
        logger.warning(f"Admin access denied for user {user.telegram_id}")
        return
    
    logger.info("Admin access granted, clearing state and showing payment history")
    await state.clear() 
    await show_payment_history_page(callback, user, db, state, page=0)

async def show_payment_history_page(callback: CallbackQuery, user: User, db: Database, state: FSMContext, page: int = 0):
    logger.info(f"show_payment_history_page called: page={page}, user={user.telegram_id}")

    try:
        page_size = 10
        offset = page * page_size
        
        payments, total_count = await db.get_all_payments_paginated(offset=offset, limit=page_size)

        logger.info(f"Got {len(payments) if payments else 0} payments, total_count={total_count}")
        
        if not payments and page == 0:
            await callback.message.edit_text(
                "❌ История платежей пуста",
                reply_markup=back_keyboard("admin_balance", user.language)
            )
            return
        
        if not payments and page > 0:
            await show_payment_history_page(callback, user, db, state, page - 1)
            return
        
        total_pages = (total_count + page_size - 1) // page_size
        text = f"💳 История платежей (стр. {page + 1}/{total_pages})\n"
        text += f"📊 Всего записей: {total_count}\n\n"
        
        for payment in payments:
            payment_user = await db.get_user_by_telegram_id(payment.user_id)
            username = payment_user.username if payment_user and payment_user.username else "N/A"
            first_name = payment_user.first_name if payment_user and payment_user.first_name else "N/A"
            
            status_emoji = {
                'completed': '✅',
                'pending': '⏳',
                'cancelled': '❌'
            }.get(payment.status, '❓')
            
            type_emoji = {
                'topup': '💰',
                'subscription': '📱',
                'subscription_extend': '🔄',
                'promocode': '🎫',
                'trial': '🆓',
                'admin_topup': '👨‍💼'
            }.get(payment.payment_type, '💳')
            
            date_str = format_datetime(payment.created_at, user.language)
            amount_str = f"+{payment.amount}" if payment.amount > 0 else str(payment.amount)
            
            text += f"{status_emoji} {type_emoji} {amount_str} руб.\n"
            text += f"👤 {first_name} (@{username}) ID:{payment.user_id}\n"
            text += f"📝 {payment.description}\n"
            text += f"📅 {date_str}\n\n"
        
        await state.update_data(current_page=page)
        await state.set_state(BotStates.admin_payment_history_page)
        
        keyboard = create_pagination_keyboard(page, total_pages, "payment_history", user.language)
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error showing payment history: {e}")
        await callback.message.edit_text(
            t('error_occurred', user.language),
            reply_markup=back_keyboard("admin_balance", user.language)
        )

def create_pagination_keyboard(current_page: int, total_pages: int, callback_prefix: str, language: str) -> InlineKeyboardMarkup:
    buttons = []
    
    nav_buttons = []
    
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{callback_prefix}_page_{current_page - 1}"))
    
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"{callback_prefix}_page_{current_page + 1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    if total_pages > 1:
        buttons.append([InlineKeyboardButton(text=f"📄 {current_page + 1}/{total_pages}", callback_data="noop")])
    
    buttons.append([InlineKeyboardButton(text=t('back', language), callback_data="admin_balance")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("payment_history_page_"))
async def payment_history_page_callback(callback: CallbackQuery, user: User, db: Database, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        page = int(callback.data.split("_")[-1])
        await show_payment_history_page(callback, user, db, state, page)
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing page number: {e}")
        await callback.answer("❌ Ошибка навигации")

@admin_router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery, **kwargs):
    await callback.answer()

@admin_router.callback_query(F.data.startswith("approve_payment_"))
async def approve_payment(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        payment_id = int(callback.data.split("_")[2])
        payment = await db.get_payment_by_id(payment_id)
        
        if not payment:
            await callback.answer("❌ Платеж не найден")
            return
        
        if payment.status != 'pending':
            await callback.answer("❌ Платеж уже обработан")
            return
        
        success = await db.add_balance(payment.user_id, payment.amount)
        
        if success:
            payment.status = 'completed'
            await db.update_payment(payment)
            
            bot = kwargs.get('bot')
            await process_referral_rewards(
                payment.user_id, 
                payment.amount, 
                payment.id, 
                db, 
                bot, 
                payment_type=payment.payment_type
            )
            
            await callback.message.edit_text(
                f"✅ Платеж одобрен!\n💰 Пользователю {payment.user_id} добавлено {payment.amount} руб."
            )
            
            if bot:
                try:
                    await bot.send_message(
                        payment.user_id,
                        f"✅ Ваш баланс пополнен на {payment.amount} руб."
                    )
                except Exception as e:
                    logger.error(f"Failed to notify user {payment.user_id}: {e}")
            
            log_user_action(user.telegram_id, "payment_approved", f"Payment: {payment_id}, Amount: {payment.amount}")
        else:
            await callback.answer("❌ Ошибка при пополнении баланса")
            
    except Exception as e:
        logger.error(f"Error approving payment: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data.startswith("reject_payment_"))
async def reject_payment(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        payment_id = int(callback.data.split("_")[2])
        payment = await db.get_payment_by_id(payment_id)
        
        if not payment:
            await callback.answer("❌ Платеж не найден")
            return
        
        if payment.status != 'pending':
            await callback.answer("❌ Платеж уже обработан")
            return
        
        payment.status = 'cancelled'
        await db.update_payment(payment)
        
        await callback.message.edit_text(
            f"❌ Платеж отклонен!\n💰 Платеж пользователя {payment.user_id} на сумму {payment.amount} руб. отклонен."
        )
        
        bot = kwargs.get('bot')
        if bot:
            try:
                await bot.send_message(
                    payment.user_id,
                    f"❌ Ваш запрос на пополнение баланса на {payment.amount} руб. отклонен."
                )
            except Exception as e:
                logger.error(f"Failed to notify user {payment.user_id}: {e}")
        
        log_user_action(user.telegram_id, "payment_rejected", f"Payment: {payment_id}, Amount: {payment.amount}")
        
    except Exception as e:
        logger.error(f"Error rejecting payment: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data == "admin_promocodes")
async def admin_promocodes_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('manage_promocodes', user.language),
        reply_markup=admin_promocodes_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "create_promocode")
async def create_promocode_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_promo_code', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_promo_code)

@admin_router.message(StateFilter(BotStates.admin_create_promo_code))
async def handle_promo_code(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    code = message.text.strip().upper()
    
    if not validate_promocode_format(code):
        await message.answer("❌ Промокод должен содержать только буквы и цифры (3-20 символов)")
        return
    
    existing = await db.get_promocode_by_code(code)
    if existing:
        await message.answer(t('promocode_exists', user.language))
        return
    
    await state.update_data(code=code)
    await message.answer(
        t('enter_promo_discount', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_promo_discount)

@admin_router.message(StateFilter(BotStates.admin_create_promo_discount))
async def handle_promo_discount(message: Message, state: FSMContext, user: User, **kwargs):
    is_valid, discount = is_valid_amount(message.text)
    
    if not is_valid:
        await message.answer(t('invalid_amount', user.language))
        return
    
    await state.update_data(discount=discount)
    await message.answer(
        t('enter_promo_limit', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_promo_limit)

@admin_router.message(StateFilter(BotStates.admin_create_promo_limit))
async def handle_promo_limit(message: Message, state: FSMContext, user: User, **kwargs):
    try:
        limit = int(message.text.strip())
        if limit <= 0 or limit > 10000:
            await message.answer("❌ Лимит должен быть от 1 до 10000")
            return
    except ValueError:
        await message.answer("❌ Введите число")
        return
    
    await state.update_data(limit=limit)
    
    await message.answer(
        "⏰ Введите срок действия промокода:\n\n"
        "• Дату в формате YYYY-MM-DD (например: 2025-12-31)\n"
        "• Количество дней (например: 30)\n"
        "• Или напишите 'нет' для бессрочного промокода\n\n"
        "📝 Введите значение:",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_promo_expiry)

@admin_router.message(StateFilter(BotStates.admin_create_promo_expiry))
async def handle_promo_expiry(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    expiry_input = message.text.strip().lower()
    expires_at = None
    
    try:
        if expiry_input in ['нет', 'no', 'none', '']:
            expires_at = None
        else:
            try:
                days = int(expiry_input)
                if days <= 0 or days > 3650: 
                    await message.answer("❌ Количество дней должно быть от 1 до 3650")
                    return
                expires_at = datetime.utcnow() + timedelta(days=days)
            except ValueError:
                try:
                    expires_at = datetime.strptime(expiry_input, "%Y-%m-%d")
                    
                    if expires_at <= datetime.utcnow():
                        await message.answer("❌ Дата должна быть в будущем")
                        return
                        
                except ValueError:
                    await message.answer(
                        "❌ Неверный формат даты\n\n"
                        "Используйте:\n"
                        "• YYYY-MM-DD (например: 2025-12-31)\n"
                        "• Количество дней (например: 30)\n"
                        "• 'нет' для бессрочного"
                    )
                    return
        
        data = await state.get_data()
        
        try:
            promocode = await db.create_promocode(
                code=data['code'],
                discount_amount=data['discount'],
                usage_limit=data['limit'],
                expires_at=expires_at
            )
            
            success_text = "✅ Промокод создан успешно!\n\n"
            success_text += f"🎫 Код: {data['code']}\n"
            success_text += f"💰 Скидка: {data['discount']}₽\n"
            success_text += f"📊 Лимит: {data['limit']} использований\n"
            
            if expires_at:
                success_text += f"⏰ Действует до: {format_datetime(expires_at, user.language)}\n"
            else:
                success_text += f"⏰ Срок: Бессрочный\n"
            
            await message.answer(
                success_text,
                reply_markup=admin_menu_keyboard(user.language)
            )
            
            log_user_action(user.telegram_id, "promocode_created", data['code'])
            
        except Exception as e:
            logger.error(f"Error creating promocode: {e}")
            await message.answer(
                t('error_occurred', user.language),
                reply_markup=admin_menu_keyboard(user.language)
            )
        
    except Exception as e:
        logger.error(f"Error parsing promocode expiry: {e}")
        await message.answer(
            "❌ Ошибка обработки срока действия",
            reply_markup=admin_menu_keyboard(user.language)
        )
    
    await state.clear()

@admin_router.callback_query(F.data == "list_promocodes")
async def list_promocodes_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        promocodes = await db.get_all_promocodes()
        
        if not promocodes:
            await callback.message.edit_text(
                "❌ Промокоды не найдены",
                reply_markup=back_keyboard("admin_promocodes", user.language)
            )
            return
        
        regular_promocodes = []
        referral_codes = []
        
        current_time = datetime.utcnow()
        
        for promo in promocodes:
            if promo.code.startswith('REF'):
                referral_codes.append(promo)
            else:
                regular_promocodes.append(promo)
        
        expired_count = 0
        active_count = 0
        
        for promo in regular_promocodes:
            if promo.expires_at and promo.expires_at < current_time:
                expired_count += 1
            elif promo.is_active:
                active_count += 1
        
        current_time_str = current_time.strftime("%H:%M:%S")
        
        text = "📋 Управление промокодами\n\n"
        text += f"📊 Статистика:\n"
        text += f"• Всего промокодов: {len(regular_promocodes)}\n"
        text += f"• Активных: {active_count}\n"
        text += f"• Истекших: {expired_count}\n"
        text += f"• Реферальных кодов: {len(referral_codes)}\n\n"
        
        if regular_promocodes:
            text += "🎫 Нажмите на промокод для управления\n\n"
        else:
            text += "🎫 Обычных промокодов нет\n\n"
        
        if referral_codes:
            text += f"👥 Реферальных кодов: {len(referral_codes)} (автоматические)\n"
        
        text += f"\n🕐 Обновлено: {current_time_str}"
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=promocodes_management_keyboard(regular_promocodes, user.language)
            )
        except Exception as edit_error:
            if "message is not modified" in str(edit_error).lower():
                await callback.answer("✅ Список обновлен", show_alert=False)
            else:
                logger.error(f"Error editing promocodes message: {edit_error}")
                await callback.answer("❌ Ошибка обновления", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error listing promocodes: {e}")
        await callback.answer(t('error_occurred', user.language))

def promocodes_management_keyboard(promocodes: List, language: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    for promo in promocodes[:10]: 
        status_icon = "🟢" if promo.is_active else "🔴"
        
        if promo.expires_at and promo.expires_at < datetime.utcnow():
            status_icon = "⏰" 
        
        promo_text = f"{status_icon} {promo.code} ({promo.used_count}/{promo.usage_limit})"
        buttons.append([
            InlineKeyboardButton(
                text=promo_text,
                callback_data=f"promo_info_{promo.id}"
            )
        ])
    
    if len(promocodes) > 10:
        buttons.append([
            InlineKeyboardButton(text=f"... и еще {len(promocodes) - 10}", callback_data="noop")
        ])
    
    buttons.extend([
        [
            InlineKeyboardButton(text="🎫 Создать промокод", callback_data="create_promocode"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="promocodes_stats")
        ],
        [
            InlineKeyboardButton(text="🧹 Очистить истекшие", callback_data="cleanup_expired_promos"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="list_promocodes")
        ],
        [InlineKeyboardButton(text=t('back', language), callback_data="admin_promocodes")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("toggle_promo_"))
async def toggle_promocode_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        promo_id = int(callback.data.split("_")[2])
        
        promocode = await db.get_promocode_by_id(promo_id)
        if not promocode:
            await callback.answer("❌ Промокод не найден")
            return
        
        if promocode.code.startswith('REF'):
            await callback.answer("❌ Нельзя изменять реферальные коды")
            return
        
        promocode.is_active = not promocode.is_active
        await db.update_promocode(promocode)
        
        status_text = "активирован" if promocode.is_active else "деактивирован"
        await callback.answer(f"✅ Промокод {promocode.code} {status_text}")
        
        log_user_action(user.telegram_id, "promocode_toggled", f"Code: {promocode.code}, Active: {promocode.is_active}")
        
        await list_promocodes_callback(callback, user, db, **kwargs)
        
    except Exception as e:
        logger.error(f"Error toggling promocode: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data.startswith("edit_promo_field_"))
async def edit_promocode_field_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        parts = callback.data.split("_")
        logger.info(f"Parsing callback data: {callback.data}, parts: {parts}")
        
        if len(parts) < 5:
            await callback.answer("❌ Неверный формат данных", show_alert=True)
            return
            
        promo_id = int(parts[3])
        field = parts[4]
        
        logger.info(f"Editing promocode {promo_id}, field {field}")
        
        await state.update_data(edit_promo_id=promo_id, edit_promo_field=field)
        
        field_names = {
            'discount': 'размер скидки (₽)',
            'limit': 'лимит использований', 
            'expiry': 'дату истечения (YYYY-MM-DD или пусто)'
        }
        
        field_name = field_names.get(field, field)
        
        await callback.message.edit_text(
            f"✏️ Введите новое значение для поля '{field_name}':",
            reply_markup=cancel_keyboard(user.language)
        )
        await state.set_state(BotStates.admin_edit_promo_value)
        
    except Exception as e:
        logger.error(f"Error editing promocode field: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data.startswith("edit_promo_"))
async def edit_promocode_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        if "edit_promo_field_" in callback.data:
            await edit_promocode_field_callback(callback, user, state, **kwargs)
            return
        
        promo_id = int(callback.data.split("_")[2])
        await state.update_data(edit_promo_id=promo_id)
        
        promocode = await db.get_promocode_by_id(promo_id)
        if not promocode:
            await callback.answer("❌ Промокод не найден")
            return
        
        if promocode.code.startswith('REF'):
            await callback.answer("❌ Нельзя редактировать реферальные коды")
            return
        
        text = f"✏️ Редактирование промокода\n\n"
        text += f"📋 Код: `{promocode.code}`\n"
        text += f"💰 Скидка: {promocode.discount_amount}₽\n"
        text += f"📊 Лимит: {promocode.usage_limit}\n"
        text += f"🔘 Статус: {'Активен' if promocode.is_active else 'Неактивен'}\n"
        text += f"📈 Использовано: {promocode.used_count}\n"
        
        if promocode.expires_at:
            text += f"⏰ Истекает: {format_datetime(promocode.expires_at, user.language)}\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=promocode_edit_keyboard(promo_id, user.language),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing promocode edit: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.message(StateFilter(BotStates.admin_edit_promo_value))
async def handle_edit_promocode_value(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    data = await state.get_data()
    promo_id = data.get('edit_promo_id')
    field = data.get('edit_promo_field')
    new_value = message.text.strip()
    
    try:
        promocode = await db.get_promocode_by_id(promo_id)
        if not promocode:
            await message.answer("❌ Промокод не найден")
            await state.clear()
            return
        
        if promocode.code.startswith('REF'):
            await message.answer("❌ Нельзя редактировать реферальные коды")
            await state.clear()
            return
        
        if field == 'discount':
            is_valid, amount = is_valid_amount(new_value)
            if not is_valid:
                await message.answer(t('invalid_amount', user.language))
                return
            promocode.discount_amount = amount
            
        elif field == 'limit':
            try:
                limit = int(new_value)
                if limit <= 0:
                    await message.answer("❌ Лимит должен быть больше 0")
                    return
                promocode.usage_limit = limit
            except ValueError:
                await message.answer("❌ Введите число")
                return
                
        elif field == 'expiry':
            if new_value.lower() in ['', 'нет', 'no', 'none']:
                promocode.expires_at = None
            else:
                try:
                    expire_date = datetime.strptime(new_value, "%Y-%m-%d")
                    if expire_date < datetime.utcnow():
                        await message.answer("❌ Дата не может быть в прошлом")
                        return
                    promocode.expires_at = expire_date
                except ValueError:
                    await message.answer("❌ Неверный формат даты. Используйте YYYY-MM-DD")
                    return
        
        await db.update_promocode(promocode)
        
        await message.answer(
            "✅ Промокод обновлен",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 К списку промокодов", callback_data="list_promocodes")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])
        )
        
        log_user_action(user.telegram_id, "promocode_edited", f"Code: {promocode.code}, Field: {field}")
        
    except Exception as e:
        logger.error(f"Error updating promocode: {e}")
        await message.answer(t('error_occurred', user.language))
    
    await state.clear()

@admin_router.callback_query(F.data.startswith("delete_promo_"))
async def delete_promocode_confirm_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        promo_id = int(callback.data.split("_")[2])
        
        promocode = await db.get_promocode_by_id(promo_id)
        if not promocode:
            await callback.answer("❌ Промокод не найден")
            return
        
        if promocode.code.startswith('REF'):
            await callback.answer("❌ Нельзя удалять реферальные коды")
            return
        
        text = f"⚠️ Удаление промокода\n\n"
        text += f"📋 Код: `{promocode.code}`\n"
        text += f"💰 Скидка: {promocode.discount_amount}₽\n"
        text += f"📊 Использован: {promocode.used_count}/{promocode.usage_limit} раз\n\n"
        text += f"❗️ Это действие нельзя отменить!"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_promo_{promo_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="list_promocodes")
            ]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing delete confirmation: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data.startswith("confirm_delete_promo_"))
async def confirm_delete_promocode_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        promo_id = int(callback.data.split("_")[3])
        
        promocode = await db.get_promocode_by_id(promo_id)
        if not promocode:
            await callback.answer("❌ Промокод не найден")
            return
        
        if promocode.code.startswith('REF'):
            await callback.answer("❌ Нельзя удалять реферальные коды")
            return
        
        success = await db.delete_promocode(promo_id)
        
        if success:
            await callback.answer(f"✅ Промокод {promocode.code} удален")
            log_user_action(user.telegram_id, "promocode_deleted", promocode.code)
        else:
            await callback.answer("❌ Ошибка удаления промокода")
        
        await list_promocodes_callback(callback, user, db, **kwargs)
        
    except Exception as e:
        logger.error(f"Error deleting promocode: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data.startswith("promo_info_"))
async def promocode_info_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        promo_id = int(callback.data.split("_")[2])
        
        promocode = await db.get_promocode_by_id(promo_id)
        if not promocode:
            await callback.answer("❌ Промокод не найден")
            return
        
        usage_records = await db.get_promocode_usage_by_id(promo_id)
        
        text = f"📋 Детальная информация о промокоде\n\n"
        text += f"🎫 Код: `{promocode.code}`\n"
        text += f"💰 Скидка: {promocode.discount_amount}₽\n"
        text += f"📊 Лимит: {promocode.usage_limit}\n"
        text += f"📈 Использовано: {promocode.used_count}\n"
        text += f"🔘 Статус: {'🟢 Активен' if promocode.is_active else '🔴 Неактивен'}\n"
        
        if promocode.expires_at:
            try:
                current_time = datetime.utcnow()
                if promocode.expires_at < current_time:
                    text += f"⏰ Истек: {format_datetime(promocode.expires_at, user.language)}\n"
                else:
                    text += f"⏰ Истекает: {format_datetime(promocode.expires_at, user.language)}\n"
            except Exception as date_error:
                logger.error(f"Error formatting expiry date: {date_error}")
                text += f"⏰ Срок: Ошибка отображения даты\n"
        else:
            text += f"⏰ Срок: Бессрочный\n"
        
        text += f"📅 Создан: {format_datetime(promocode.created_at, user.language)}\n"
        
        total_discount = promocode.discount_amount * promocode.used_count
        text += f"\n💸 Общая сумма скидок: {total_discount}₽\n"
        
        if promocode.usage_limit > 0:
            usage_percent = (promocode.used_count / promocode.usage_limit) * 100
            text += f"📊 Использовано: {usage_percent:.1f}%\n"
        
        if usage_records:
            text += f"\n📜 Последние использования:\n"
            for i, usage in enumerate(usage_records[:5], 1):
                usage_date = format_datetime(usage.used_at, user.language)
                text += f"{i}. ID:{usage.user_id} - {usage_date}\n"
            
            if len(usage_records) > 5:
                text += f"... и еще {len(usage_records) - 5} использований\n"
        else:
            text += f"\n📜 Промокод еще не использовался\n"
        
        is_referral = promocode.code.startswith('REF')
        
        await callback.message.edit_text(
            text,
            reply_markup=promocode_info_keyboard(promo_id, is_referral, user.language),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing promocode info: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data == "cleanup_expired_promos")
async def cleanup_expired_promos_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        db = kwargs.get('db')
        expired_promos = await db.get_expired_promocodes()
        
        if not expired_promos:
            await callback.answer("✅ Нет истекших промокодов для удаления", show_alert=True)
            return
        
        text = f"🧹 Очистка истекших промокодов\n\n"
        text += f"Найдено истекших промокодов: {len(expired_promos)}\n\n"
        
        text += f"Примеры:\n"
        for i, promo in enumerate(expired_promos[:5], 1):
            expired_days = (datetime.utcnow() - promo.expires_at).days
            text += f"{i}. `{promo.code}` (истек {expired_days} дн. назад)\n"
        
        if len(expired_promos) > 5:
            text += f"... и еще {len(expired_promos) - 5}\n"
        
        text += f"\n⚠️ Все истекшие промокоды будут удалены без возможности восстановления!"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить все", callback_data="confirm_cleanup_expired"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="list_promocodes")
            ]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing cleanup confirmation: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data == "confirm_cleanup_expired")
async def confirm_cleanup_expired_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        await callback.answer("🧹 Удаляю истекшие промокоды...")
        
        deleted_count = await db.cleanup_expired_promocodes()
        
        if deleted_count > 0:
            text = f"✅ Очистка завершена!\n\n"
            text += f"Удалено истекших промокодов: {deleted_count}"
            
            log_user_action(user.telegram_id, "expired_promocodes_cleaned", f"Count: {deleted_count}")
        else:
            text = f"ℹ️ Истекших промокодов не найдено"
        
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 К списку промокодов", callback_data="list_promocodes")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])
        )
        
    except Exception as e:
        logger.error(f"Error cleaning up expired promocodes: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка при удалении истекших промокодов",
            reply_markup=back_keyboard("list_promocodes", user.language)
        )

@admin_router.callback_query(F.data == "promocodes_stats")
async def promocodes_stats_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        await callback.answer("📊 Собираю статистику...")
        
        stats = await db.get_promocode_stats()
        
        text = f"📊 Статистика промокодов\n\n"
        
        text += f"📋 Общая информация:\n"
        text += f"• Всего промокодов: {stats['total_promocodes']}\n"
        text += f"• Активных: {stats['active_promocodes']}\n"
        text += f"• Истекших: {stats['expired_promocodes']}\n"
        text += f"• Неактивных: {stats['total_promocodes'] - stats['active_promocodes'] - stats['expired_promocodes']}\n\n"
        
        text += f"📈 Использование:\n"
        text += f"• Всего использований: {stats['total_usage']}\n"
        text += f"• Общая сумма скидок: {stats['total_discount_amount']:.2f}₽\n"
        
        if stats['total_promocodes'] > 0:
            avg_usage = stats['total_usage'] / stats['total_promocodes']
            text += f"• Среднее использований на промокод: {avg_usage:.1f}\n"
        
        if stats['top_promocodes']:
            text += f"\n🏆 Топ-5 популярных промокодов:\n"
            for i, (code, used_count, discount) in enumerate(stats['top_promocodes'], 1):
                if used_count > 0:
                    total_discount = used_count * discount
                    text += f"{i}. `{code}` - {used_count} исп. ({total_discount:.0f}₽)\n"
        
        text += f"\n🕐 Обновлено: {format_datetime(datetime.utcnow(), user.language)}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="promocodes_stats")],
            [InlineKeyboardButton(text="🧹 Очистить истекшие", callback_data="cleanup_expired_promos")],
            [InlineKeyboardButton(text="📋 К списку", callback_data="list_promocodes")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error getting promocodes stats: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data == "confirm_deactivate_all")
async def confirm_deactivate_all_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        await callback.answer("🔴 Деактивирую все промокоды...")
        
        deactivated_count = await db.deactivate_all_regular_promocodes()
        
        if deactivated_count > 0:
            text = f"✅ Деактивация завершена!\n\n"
            text += f"Деактивировано промокодов: {deactivated_count}\n\n"
            text += f"ℹ️ Реферальные коды не затронуты"
            
            log_user_action(user.telegram_id, "all_promocodes_deactivated", f"Count: {deactivated_count}")
        else:
            text = f"ℹ️ Нет активных промокодов для деактивации"
        
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 К списку промокодов", callback_data="list_promocodes")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])
        )
        
    except Exception as e:
        logger.error(f"Error deactivating all promocodes: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка при деактивации промокодов",
            reply_markup=back_keyboard("list_promocodes", user.language)
        )

@admin_router.callback_query(F.data == "main_menu", StateFilter(
    BotStates.admin_create_sub_name,
    BotStates.admin_create_sub_desc,
    BotStates.admin_create_sub_price,
    BotStates.admin_create_sub_days,
    BotStates.admin_create_sub_traffic,
    BotStates.admin_create_sub_squad,
    BotStates.admin_add_balance_user,
    BotStates.admin_add_balance_amount,
    BotStates.admin_create_promo_code,
    BotStates.admin_create_promo_discount,
    BotStates.admin_create_promo_limit,
    BotStates.admin_edit_promo_value,
    BotStates.admin_edit_sub_value,
    BotStates.admin_send_message_user,
    BotStates.admin_send_message_text,
    BotStates.admin_broadcast_text,
    BotStates.admin_payment_history_page,
    BotStates.admin_search_user_any,  
    BotStates.admin_edit_user_expiry,
    BotStates.admin_edit_user_traffic,
    BotStates.admin_test_monitor_user,
    BotStates.admin_rename_plans_confirm,
    BotStates.waiting_rule_title,
    BotStates.waiting_rule_content,
    BotStates.waiting_rule_order,
    BotStates.waiting_rule_edit_title,
    BotStates.waiting_rule_edit_content,
    BotStates.waiting_rule_edit_order
))
async def cancel_rule_editing(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    """Отмена редактирования правил"""
    await state.clear()
    await callback.message.edit_text(
        t('main_menu', user.language),
        reply_markup=main_menu_keyboard(user.language, user.is_admin)
    )
    
async def cancel_admin_action(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    await state.clear()
    await callback.message.edit_text(
        t('main_menu', user.language),
        reply_markup=main_menu_keyboard(user.language, user.is_admin)
    )

@admin_router.callback_query(F.data == "admin_messages")
async def admin_messages_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('send_message', user.language),
        reply_markup=admin_messages_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "admin_send_to_user")
async def admin_send_to_user_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_user_id_message', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_send_message_user)

@admin_router.message(StateFilter(BotStates.admin_send_message_user))
async def handle_message_user_id(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    telegram_id = parse_telegram_id(message.text)
    
    if not telegram_id:
        await message.answer("❌ Неверный Telegram ID")
        return
    
    target_user = await db.get_user_by_telegram_id(telegram_id)
    if not target_user:
        await message.answer(t('user_not_found', user.language))
        return
    
    await state.update_data(target_user_id=telegram_id)
    await message.answer(
        t('enter_message_text', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_send_message_text)

@admin_router.callback_query(F.data == "admin_monitor")
async def admin_monitor_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "🔍 Управление сервисом мониторинга",
        reply_markup=admin_monitor_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "monitor_status")
async def monitor_status_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    monitor_service = kwargs.get('monitor_service')
    if not monitor_service:
        await callback.message.edit_text(
            "❌ Сервис мониторинга недоступен",
            reply_markup=back_keyboard("admin_monitor", user.language)
        )
        return
    
    try:
        status = await monitor_service.get_service_status()
        
        status_text = "🔍 **Статус сервиса мониторинга:**\n\n"
        
        if status['is_running']:
            status_text += "✅ **Статус:** Работает\n"
        else:
            status_text += "❌ **Статус:** Остановлен\n"
        
        status_text += f"⚙️ **Настройки:**\n"
        status_text += f"• Включен: {'✅' if status['monitor_enabled'] else '❌'}\n"
        status_text += f"• Интервал проверки: {status['check_interval']} сек\n"
        status_text += f"• Ежедневная проверка: {status['daily_check_hour']}:00\n"
        status_text += f"• Предупреждение за: {status['warning_days']} дн.\n\n"
        
        status_text += f"🗑️ **Настройки удаления:**\n"
        status_text += f"• Удалять триальные через: {status['delete_trial_days']} дн.\n"
        status_text += f"• Удалять обычные через: {status['delete_regular_days']} дн.\n"
        status_text += f"• Автоудаление: {'✅' if status['auto_delete_enabled'] else '❌'}\n\n"
        
        status_text += f"📊 **Состояние задач:**\n"
        status_text += f"• Мониторинг: {status['task_status']['monitor_task']}\n"
        status_text += f"• Ежедневная: {status['task_status']['daily_task']}\n"
        
        if status['last_check']:
            status_text += f"\n⏰ **Последняя проверка:** {status['last_check']}"

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🧪 Тест пользователя", callback_data="monitor_test_user"),
                InlineKeyboardButton(text="🚀 Принудительная проверка", callback_data="monitor_force_check")
            ],
            [
                InlineKeyboardButton(text="🗑️ Удалить истекшие триальные", callback_data="delete_expired_trials"),
                InlineKeyboardButton(text="🗑️ Удалить истекшие обычные", callback_data="delete_expired_regular")
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_monitor")]
        ])
        
        await callback.message.edit_text(status_text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error getting monitor status: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения статуса",
            reply_markup=back_keyboard("admin_monitor", user.language)
        )

@admin_router.callback_query(F.data == "delete_expired_trials")
async def delete_expired_trials_handler(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return

    monitor_service = kwargs.get('monitor_service')
    if not monitor_service:
        await callback.answer("❌ Сервис мониторинга недоступен", show_alert=True)
        return

    try:
        text = "⚠️ **УДАЛЕНИЕ ИСТЕКШИХ ТРИАЛЬНЫХ ПОДПИСОК**\n\n"
        text += "Вы собираетесь удалить все истекшие триальные подписки.\n\n"
        text += "🗑️ **Что будет удалено:**\n"
        text += f"• Триальные подписки, истекшие более {getattr(monitor_service.config, 'DELETE_EXPIRED_TRIAL_DAYS', 1)} дн. назад\n"
        text += "• Данные будут удалены из базы бота\n"
        text += "• Пользователи будут удалены из панели RemnaWave\n\n"
        text += "❗ **Это действие НЕОБРАТИМО!**\n\n"
        text += "Продолжить?"

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete_trials"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="monitor_status")
            ]
        ])

        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in delete_expired_trials_handler: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)

@admin_router.callback_query(F.data == "confirm_delete_trials")
async def confirm_delete_trials_handler(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return

    monitor_service = kwargs.get('monitor_service')
    if not monitor_service:
        await callback.answer("❌ Сервис мониторинга недоступен", show_alert=True)
        return

    try:
        processing_text = "🗑️ **Удаление истекших триальных подписок...**\n\n"
        processing_text += "⏳ Поиск и удаление подписок...\n"
        processing_text += "Пожалуйста, подождите..."

        await callback.message.edit_text(processing_text)
        await callback.answer("🗑️ Начинаю удаление триальных подписок...")

        result = await monitor_service.delete_expired_trial_subscriptions(force=False)

        text = "🗑️ **Удаление триальных подписок завершено**\n\n"
        text += f"📊 **Результаты:**\n"
        text += f"• Проверено: {result['total_checked']}\n"
        text += f"• Удалено из БД: {result['deleted_from_db']}\n"
        text += f"• Удалено из API: {result['deleted_from_api']}\n"
        text += f"• Ошибки: {len(result['errors'])}\n\n"

        if result['deleted_subscriptions']:
            text += f"✅ **Удаленные подписки:**\n"
            for sub in result['deleted_subscriptions'][:10]: 
                text += f"• {sub['subscription_name']} (пользователь {sub['user_id']})\n"
            
            if len(result['deleted_subscriptions']) > 10:
                text += f"• ... и еще {len(result['deleted_subscriptions']) - 10}\n"
        
        if result['errors']:
            text += f"\n❌ **Ошибки:**\n"
            for error in result['errors'][:5]: 
                text += f"• {error}\n"
            
            if len(result['errors']) > 5:
                text += f"• ... и еще {len(result['errors']) - 5}\n"

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К статусу мониторинга", callback_data="monitor_status")]
        ])

        await callback.message.edit_text(text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error in confirm_delete_trials_handler: {e}")
        error_text = f"❌ **Ошибка удаления триальных подписок**\n\n{str(e)}"
        
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К статусу мониторинга", callback_data="monitor_status")]
        ])
        await callback.message.edit_text(error_text, reply_markup=keyboard)

@admin_router.callback_query(F.data == "delete_expired_regular")
async def delete_expired_regular_handler(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return

    monitor_service = kwargs.get('monitor_service')
    if not monitor_service:
        await callback.answer("❌ Сервис мониторинга недоступен", show_alert=True)
        return

    try:
        text = "⚠️ **УДАЛЕНИЕ ИСТЕКШИХ ОБЫЧНЫХ ПОДПИСОК**\n\n"
        text += "Вы собираетесь удалить все истекшие обычные подписки.\n\n"
        text += "🗑️ **Что будет удалено:**\n"
        text += f"• Обычные подписки, истекшие более {getattr(monitor_service.config, 'DELETE_EXPIRED_REGULAR_DAYS', 7)} дн. назад\n"
        text += "• Данные будут удалены из базы бота\n"
        text += "• Пользователи будут удалены из панели RemnaWave\n"
        text += "• Импортированные подписки НЕ затрагиваются\n\n"
        text += "❗ **Это действие НЕОБРАТИМО!**\n\n"
        text += "Продолжить?"

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete_regular"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="monitor_status")
            ]
        ])

        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in delete_expired_regular_handler: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)

@admin_router.callback_query(F.data == "confirm_delete_regular")
async def confirm_delete_regular_handler(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return

    monitor_service = kwargs.get('monitor_service')
    if not monitor_service:
        await callback.answer("❌ Сервис мониторинга недоступен", show_alert=True)
        return

    try:
        processing_text = "🗑️ **Удаление истекших обычных подписок...**\n\n"
        processing_text += "⏳ Поиск и удаление подписок...\n"
        processing_text += "Пожалуйста, подождите..."

        await callback.message.edit_text(processing_text)
        await callback.answer("🗑️ Начинаю удаление обычных подписок...")

        result = await monitor_service.delete_expired_regular_subscriptions(force=False)

        text = "🗑️ **Удаление обычных подписок завершено**\n\n"
        text += f"📊 **Результаты:**\n"
        text += f"• Проверено: {result['total_checked']}\n"
        text += f"• Удалено из БД: {result['deleted_from_db']}\n"
        text += f"• Удалено из API: {result['deleted_from_api']}\n"
        text += f"• Ошибки: {len(result['errors'])}\n\n"

        if result['deleted_subscriptions']:
            text += f"✅ **Удаленные подписки:**\n"
            for sub in result['deleted_subscriptions'][:10]: 
                text += f"• {sub['subscription_name']} (пользователь {sub['user_id']})\n"
            
            if len(result['deleted_subscriptions']) > 10:
                text += f"• ... и еще {len(result['deleted_subscriptions']) - 10}\n"
        
        if result['errors']:
            text += f"\n❌ **Ошибки:**\n"
            for error in result['errors'][:5]: 
                text += f"• {error}\n"
            
            if len(result['errors']) > 5:
                text += f"• ... и еще {len(result['errors']) - 5}\n"

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К статусу мониторинга", callback_data="monitor_status")]
        ])

        await callback.message.edit_text(text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error in confirm_delete_regular_handler: {e}")
        error_text = f"❌ **Ошибка удаления обычных подписок**\n\n{str(e)}"
        
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К статусу мониторинга", callback_data="monitor_status")]
        ])
        await callback.message.edit_text(error_text, reply_markup=keyboard)

@admin_router.callback_query(F.data == "monitor_force_check")
async def monitor_force_check_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    monitor_service = kwargs.get('monitor_service')
    if not monitor_service:
        await callback.answer("❌ Сервис мониторинга недоступен")
        return
    
    try:
        await callback.answer("⏳ Запускаю принудительную проверку...")
        await monitor_service.force_daily_check()
        await callback.message.edit_text(
            "✅ Принудительная проверка завершена",
            reply_markup=back_keyboard("admin_monitor", user.language)
        )
        
    except Exception as e:
        logger.error(f"Error forcing check: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при выполнении проверки",
            reply_markup=back_keyboard("admin_monitor", user.language)
        )

@admin_router.callback_query(F.data == "monitor_deactivate_expired")
async def monitor_deactivate_expired_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    monitor_service = kwargs.get('monitor_service')
    if not monitor_service:
        await callback.answer("❌ Сервис мониторинга недоступен")
        return
    
    try:
        await callback.answer("⏳ Деактивирую истекшие подписки...")
        count = await monitor_service.deactivate_expired_subscriptions()
        
        await callback.message.edit_text(
            f"✅ Деактивировано {count} истекших подписок",
            reply_markup=back_keyboard("admin_monitor", user.language)
        )
        
        log_user_action(user.telegram_id, "expired_subscriptions_deactivated", f"Count: {count}")
        
    except Exception as e:
        logger.error(f"Error deactivating expired subscriptions: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при деактивации подписок",
            reply_markup=back_keyboard("admin_monitor", user.language)
        )

@admin_router.callback_query(F.data == "monitor_test_user")
async def monitor_test_user_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "👤 Введите Telegram ID пользователя для тестирования уведомлений:",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_test_monitor_user)

@admin_router.message(StateFilter(BotStates.admin_test_monitor_user))
async def handle_monitor_test_user(message: Message, state: FSMContext, user: User, **kwargs):
    telegram_id = parse_telegram_id(message.text)
    
    if not telegram_id:
        await message.answer("❌ Неверный Telegram ID")
        return
    
    monitor_service = kwargs.get('monitor_service')
    if not monitor_service:
        await message.answer("❌ Сервис мониторинга недоступен")
        await state.clear()
        return
    
    try:
        results = await monitor_service.check_single_user(telegram_id)
        
        if not results:
            await message.answer("❌ Результаты не получены")
        else:
            text = f"📊 Результаты тестирования для пользователя {telegram_id}:\n\n"
            
            for i, result in enumerate(results, 1):
                success = result.get('success', False)
                message_text = result.get('message', 'No message')
                error = result.get('error', None)
                
                status = "✅" if success else "❌"
                text += f"{i}. {status} {message_text}\n"
                
                if error:
                    text += f"   ⚠️ Ошибка: {error}\n"
                
                text += "\n"
            
            try:
                config = kwargs.get('config')
                if config:
                    text += f"⚙️ Настройки мониторинга:\n"
                    text += f"• Предупреждение за: {config.MONITOR_WARNING_DAYS} дней\n"
                    text += f"• Интервал проверки: {config.MONITOR_CHECK_INTERVAL} сек\n"
                    text += f"• Ежедневная проверка: {config.MONITOR_DAILY_CHECK_HOUR}:00\n"
            except Exception as config_error:
                logger.warning(f"Could not get config info: {config_error}")
            
            await message.answer(
                text,
                reply_markup=admin_menu_keyboard(user.language)
            )
        
        log_user_action(user.telegram_id, "monitor_test_user", f"User: {telegram_id}")
        
    except Exception as e:
        logger.error(f"Error testing monitor for user: {e}")
        await message.answer("❌ Ошибка при тестировании")
    
    await state.clear()

@admin_router.message(StateFilter(BotStates.admin_send_message_text))
async def handle_send_message(message: Message, state: FSMContext, user: User, **kwargs):
    message_text = message.text.strip()
    
    if len(message_text) < 1:
        await message.answer("❌ Сообщение не может быть пустым")
        return
    
    data = await state.get_data()
    target_user_id = data['target_user_id']
    
    try:
        bot = kwargs.get('bot')
        if bot:
            await bot.send_message(target_user_id, message_text)
            await message.answer(
                t('message_sent', user.language),
                reply_markup=admin_menu_keyboard(user.language)
            )
            log_user_action(user.telegram_id, "message_sent", f"To user: {target_user_id}")
        else:
            await message.answer("❌ Ошибка отправки сообщения")
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        await message.answer("❌ Ошибка отправки сообщения (пользователь заблокировал бота?)")
    
    await state.clear()

@admin_router.callback_query(F.data == "admin_send_to_all")
async def admin_send_to_all_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_message_text', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_broadcast_text)

@admin_router.callback_query(F.data == "main_menu", StateFilter(BotStates.admin_test_monitor_user))
async def cancel_monitor_test(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    await state.clear()
    await callback.message.edit_text(
        t('main_menu', user.language),
        reply_markup=main_menu_keyboard(user.language, user.is_admin)
    )

@admin_router.message(StateFilter(BotStates.admin_broadcast_text))
async def handle_broadcast_message(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    message_text = message.text.strip()
    
    if len(message_text) < 1:
        await message.answer("❌ Сообщение не может быть пустым")
        return
    
    try:
        users = await db.get_all_users()
        
        if not users:
            await message.answer("❌ Пользователи не найдены")
            await state.clear()
            return
        
        bot = kwargs.get('bot')
        if not bot:
            await message.answer("❌ Ошибка отправки сообщения")
            await state.clear()
            return
        
        sent_count = 0
        error_count = 0
        
        progress_msg = await message.answer(f"📤 Отправка сообщения {len(users)} пользователям...")
        
        for target_user in users:
            try:
                await bot.send_message(target_user.telegram_id, message_text)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to send broadcast to {target_user.telegram_id}: {e}")
                error_count += 1
            
            await asyncio.sleep(0.05)
        
        await progress_msg.edit_text(
            t('broadcast_sent', user.language) + "\n" + 
            t('broadcast_stats', user.language, sent=sent_count, errors=error_count),
            reply_markup=admin_menu_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "broadcast_sent", f"Sent: {sent_count}, Errors: {error_count}")
        
    except Exception as e:
        logger.error(f"Error sending broadcast: {e}")
        await message.answer(
            t('error_occurred', user.language),
            reply_markup=admin_menu_keyboard(user.language)
        )
    
    await state.clear()

@admin_router.callback_query(F.data == "admin_system")
async def admin_system_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "🖥 Управление системой RemnaWave",
        reply_markup=admin_system_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "system_stats")
async def system_stats_callback(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await show_system_stats(callback, user, db, api)

@admin_router.callback_query(F.data == "refresh_system_stats")
async def refresh_system_stats_callback(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.answer("🔄 Обновляю статистику...")
    await show_system_stats(callback, user, db, api)

@admin_router.callback_query(F.data == "debug_users_api")
async def debug_users_api_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not api:
        await callback.answer("❌ API недоступен", show_alert=True)
        return
    
    try:
        await callback.answer("🔍 Анализирую структуру API...")
        
        debug_info = await api.debug_users_api()
        
        text = "🔬 **Отладка API пользователей**\n\n"
        
        if 'error' in debug_info:
            text += f"❌ Ошибка: {debug_info['error']}\n"
        else:
            text += f"📦 Тип ответа: `{debug_info.get('api_response_type', 'unknown')}`\n"
            
            if debug_info.get('api_response_keys'):
                text += f"🔑 Ключи ответа: `{', '.join(debug_info['api_response_keys'][:5])}`\n"
            
            if debug_info.get('has_users'):
                text += f"✅ Пользователи найдены\n"
                text += f"📍 Расположение: `{debug_info.get('users_location', 'unknown')}`\n"
                
                if debug_info.get('first_user_structure'):
                    text += f"\n📋 **Структура пользователя:**\n"
                    for field in debug_info['first_user_structure'][:10]:
                        text += f"  • `{field}`\n"
                    if len(debug_info['first_user_structure']) > 10:
                        text += f"  _... и еще {len(debug_info['first_user_structure']) - 10} полей_\n"
            else:
                text += "❌ Пользователи не найдены в ответе\n"
            
            if debug_info.get('total_count') is not None:
                text += f"\n📊 Всего пользователей: {debug_info['total_count']}\n"
                text += f"📍 Поле счетчика: `{debug_info.get('total_count_field', 'unknown')}`\n"
        
        text += "\n--- **Тест получения пользователей** ---\n"
        
        users = await api.get_all_system_users_full()
        if users:
            text += f"✅ Успешно получено {len(users)} пользователей\n"
            active = len([u for u in users if u.get('status') == 'ACTIVE'])
            text += f"• Активных: {active}\n"
            text += f"• Неактивных: {len(users) - active}\n"
            
            if users:
                text += f"\n**Пример пользователя:**\n"
                example_user = users[0]
                text += f"• Username: `{example_user.get('username', 'N/A')}`\n"
                text += f"• Status: `{example_user.get('status', 'N/A')}`\n"
                text += f"• UUID: `{str(example_user.get('uuid', 'N/A'))[:20]}...`\n"
        else:
            text += "❌ Не удалось получить пользователей\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Повторить тест", callback_data="debug_users_api")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="system_users")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error in debug_users_api: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка отладки API\n\n{str(e)[:200]}",
            reply_markup=back_keyboard("system_users", user.language)
        )

@admin_router.callback_query(F.data == "debug_api_comprehensive")
async def debug_api_comprehensive_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not api:
        await callback.message.edit_text(
            "❌ API недоступен для диагностики",
            reply_markup=admin_system_keyboard(user.language)
        )
        return
    
    await callback.answer("🔍 Запуск полной диагностики API...")
    
    endpoints_to_test = [
        ('/api/nodes', 'GET', 'Ноды'),
        ('/api/users?limit=3', 'GET', 'Пользователи'),
        ('/api/internal-squads', 'GET', 'Сквады'),
    ]
    
    diagnostic_text = "🔬 Диагностика RemnaWave API\n\n"
    
    for endpoint, method, description in endpoints_to_test:
        try:
            diagnostic_text += f"🔹 {description} ({endpoint}):\n"
            
            debug_result = await api.debug_api_response(endpoint, method)
            
            if debug_result.get('success'):
                diagnostic_text += f"   ✅ Статус: {debug_result.get('status')}\n"
                
                if 'response_keys' in debug_result:
                    keys = debug_result['response_keys']
                    diagnostic_text += f"   🔑 Ключи: {', '.join(keys[:5])}\n"
                
                if 'data_type' in debug_result:
                    data_type = debug_result['data_type']
                    diagnostic_text += f"   📊 Тип данных: {data_type}\n"
                    
                    if 'data_count' in debug_result:
                        count = debug_result['data_count']
                        diagnostic_text += f"   📈 Количество: {count}\n"
                
                if 'nodes' in endpoint and debug_result.get('json'):
                    await analyze_nodes_response(debug_result['json'], diagnostic_text)
                
                if 'users' in endpoint and debug_result.get('json'):
                    await analyze_users_response(debug_result['json'], diagnostic_text)
                    
            else:
                diagnostic_text += f"   ❌ Ошибка: {debug_result.get('status', 'N/A')}\n"
                if 'error' in debug_result:
                    diagnostic_text += f"   💥 Детали: {debug_result['error'][:50]}...\n"
            
            diagnostic_text += "\n"
            
        except Exception as e:
            diagnostic_text += f"   💥 Исключение: {str(e)[:50]}...\n\n"
    
    diagnostic_text += "💡 Рекомендации:\n"
    diagnostic_text += "• Проверьте токен авторизации\n"
    diagnostic_text += "• Убедитесь в корректности base_url\n"
    diagnostic_text += "• Проверьте доступность RemnaWave сервера\n"
    diagnostic_text += "• Просмотрите логи на предмет ошибок\n"
    
    diagnostic_text += f"\n🕐 Диагностика завершена: {format_datetime(datetime.now(), user.language)}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Повторить диагностику", callback_data="debug_api_comprehensive")],
        [InlineKeyboardButton(text="📊 Простая статистика", callback_data="system_stats")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_system")]
    ])
    
    if len(diagnostic_text) > 4000:
        diagnostic_text = diagnostic_text[:3900] + "\n\n... (текст обрезан)"
    
    try:
        await callback.message.edit_text(diagnostic_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Failed to send diagnostic results: {e}")
        await callback.answer("❌ Ошибка отправки результатов диагностики", show_alert=True)

async def analyze_nodes_response(json_data, diagnostic_text):
    try:
        nodes_list = []
        
        if isinstance(json_data, dict):
            if 'data' in json_data and isinstance(json_data['data'], list):
                nodes_list = json_data['data']
            elif 'response' in json_data and isinstance(json_data['response'], list):
                nodes_list = json_data['response']
        elif isinstance(json_data, list):
            nodes_list = json_data
        
        if nodes_list:
            diagnostic_text += f"   🖥 Найдено нод: {len(nodes_list)}\n"
            
            status_counts = {}
            for node in nodes_list:
                status = str(node.get('status', 'unknown')).lower()
                status_counts[status] = status_counts.get(status, 0) + 1
            
            diagnostic_text += f"   📊 Статусы: {dict(status_counts)}\n"
            
            for i, node in enumerate(nodes_list[:2]):
                name = node.get('name', f'Node-{i+1}')
                status = node.get('status', 'unknown')
                diagnostic_text += f"   📡 {name}: {status}\n"
        
    except Exception as e:
        diagnostic_text += f"   ⚠️ Ошибка анализа нод: {str(e)[:30]}...\n"

async def analyze_users_response(json_data, diagnostic_text):
    try:
        users_list = []
        
        if isinstance(json_data, dict):
            if 'data' in json_data and isinstance(json_data['data'], list):
                users_list = json_data['data']
            elif 'response' in json_data and isinstance(json_data['response'], list):
                users_list = json_data['response']
        elif isinstance(json_data, list):
            users_list = json_data
        
        if users_list:
            diagnostic_text += f"   👥 Найдено пользователей: {len(users_list)}\n"
            
            active_count = len([u for u in users_list if str(u.get('status', '')).upper() == 'ACTIVE'])
            diagnostic_text += f"   ✅ Активных: {active_count}\n"
            
            statuses = [str(u.get('status', 'N/A')).upper() for u in users_list[:3]]
            diagnostic_text += f"   📊 Примеры статусов: {', '.join(statuses)}\n"
        
    except Exception as e:
        diagnostic_text += f"   ⚠️ Ошибка анализа пользователей: {str(e)[:30]}...\n"

@admin_router.callback_query(F.data == "nodes_management")
async def nodes_management_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await show_nodes_management_improved(callback, user, api)

async def show_nodes_management_improved(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None):
    try:
        if not api:
            await callback.message.edit_text(
                "❌ API RemnaWave недоступен\n\n"
                "Для управления нодами необходимо подключение к API.",
                reply_markup=admin_system_keyboard(user.language)
            )
            return
        
        await callback.answer("🖥 Загружаю информацию о нодах...")
        
        nodes = await api.get_all_nodes()
        
        if not nodes:
            await callback.message.edit_text(
                "❌ Ноды не найдены\n\n"
                "Возможные причины:\n"
                "• В системе не настроены ноды\n"
                "• Проблемы с подключением к API\n"
                "• Недостаточно прав доступа",
                reply_markup=admin_system_keyboard(user.language)
            )
            return
        
        online_nodes = []
        offline_nodes = []
        disabled_nodes = []
        
        for node in nodes:
            status = node.get('status', 'unknown')
            if status == 'online':
                online_nodes.append(node)
            elif status == 'disabled':
                disabled_nodes.append(node)
            else:
                offline_nodes.append(node)
        
        from datetime import datetime
        current_time = datetime.now().strftime("%H:%M:%S")
        
        text = "🖥 **Управление нодами**\n\n"
        
        text += "📊 **Общая статистика:**\n"
        text += f"├ Всего нод: {len(nodes)}\n"
        text += f"├ 🟢 Онлайн: {len(online_nodes)}\n"
        text += f"├ 🔴 Оффлайн: {len(offline_nodes)}\n"
        text += f"└ ⚫ Отключено: {len(disabled_nodes)}\n\n"
        
        if len(online_nodes) == len(nodes):
            text += "🟢 **Система работает нормально**\n\n"
        elif len(online_nodes) >= len(nodes) * 0.7:
            text += "🟡 **Система работает с предупреждениями**\n\n"
        elif len(online_nodes) > 0:
            text += "🟠 **Система работает частично**\n\n"
        else:
            text += "🔴 **Критическое состояние системы**\n\n"
        
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if online_nodes:
            text += "🟢 **Активные ноды:**\n"
            for i, node in enumerate(online_nodes[:3], 1):
                text += format_node_info(node, i)
            if len(online_nodes) > 3:
                text += f"   _... и еще {len(online_nodes) - 3} активных нод_\n"
            text += "\n"
        
        if offline_nodes:
            text += "🔴 **Оффлайн ноды:**\n"
            for i, node in enumerate(offline_nodes[:2], 1):
                text += format_node_info(node, i)
            if len(offline_nodes) > 2:
                text += f"   _... и еще {len(offline_nodes) - 2} оффлайн нод_\n"
            text += "\n"
        
        if disabled_nodes:
            text += "⚫ **Отключенные ноды:**\n"
            for i, node in enumerate(disabled_nodes[:2], 1):
                text += format_node_info(node, i)
            if len(disabled_nodes) > 2:
                text += f"   _... и еще {len(disabled_nodes) - 2} отключенных нод_\n"
        
        text += f"\n🕐 _Обновлено: {current_time}_"
        
        keyboard = nodes_management_keyboard(nodes, user.language)
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        except Exception as edit_error:
            if "message is not modified" in str(edit_error).lower():
                await callback.answer("✅ Информация о нодах актуальна", show_alert=False)
            else:
                logger.error(f"Error editing nodes management message: {edit_error}")
                await callback.answer("❌ Ошибка обновления", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error in show_nodes_management_improved: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка загрузки информации о нодах\n\n"
            f"Детали: {str(e)[:100]}",
            reply_markup=admin_system_keyboard(user.language)
        )

def format_node_info(node: Dict, index: int) -> str:
    name = node.get('name', f'Node-{index}')
    address = node.get('address', 'N/A')
    
    if len(name) > 25:
        name = name[:22] + "..."
    if len(address) > 30:
        address = address[:27] + "..."
    
    text = f"{index}. **{name}**\n"
    
    if address != 'N/A':
        text += f"   📍 {address}\n"
    
    if node.get('countryCode'):
        text += f"   🌍 {node['countryCode']}\n"
    
    if node.get('cpuUsage') or node.get('memUsage'):
        text += "   💻 "
        if node.get('cpuUsage'):
            cpu = node['cpuUsage']
            cpu_emoji = "🔴" if cpu > 80 else "🟡" if cpu > 50 else "🟢"
            text += f"CPU: {cpu_emoji} {cpu:.0f}% "
        if node.get('memUsage'):
            mem = node['memUsage']
            mem_emoji = "🔴" if mem > 80 else "🟡" if mem > 50 else "🟢"
            text += f"MEM: {mem_emoji} {mem:.0f}%"
        text += "\n"
    
    if node.get('usersCount'):
        text += f"   👥 Пользователей: {node['usersCount']}\n"
    
    if node.get('trafficUsedBytes'):
        traffic_used = format_bytes(node['trafficUsedBytes'])
        text += f"   📊 Трафик: {traffic_used}\n"
    
    return text

@admin_router.callback_query(F.data == "refresh_nodes_stats")
async def refresh_nodes_stats_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.answer("🔄 Обновляю информацию о нодах...")
    await show_nodes_management_improved(callback, user, api)

@admin_router.callback_query(F.data.startswith("refresh_nodes_stats_"))
async def refresh_nodes_stats_with_timestamp_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.answer("🔄 Обновляю информацию о нодах...")
    await show_nodes_management_improved(callback, user, api)


@admin_router.callback_query(F.data == "restart_all_nodes")
async def restart_all_nodes_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите перезагрузить ВСЕ ноды?\n\n"
        "Это может привести к временной недоступности сервиса для всех пользователей!",
        reply_markup=confirm_restart_keyboard(None, user.language)
    )

@admin_router.callback_query(F.data == "confirm_restart_all_nodes")
async def confirm_restart_all_nodes_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        if not api:
            await callback.message.edit_text(
                "❌ API недоступен\n\n"
                "Невозможно выполнить перезагрузку без подключения к RemnaWave API.\n"
                "Обратитесь к администратору для настройки подключения.",
                reply_markup=admin_system_keyboard(user.language)
            )
            return
        
        await callback.answer("🔄 Отправляю команду перезагрузки всех нод...")
        
        logger.info("Attempting to restart all nodes via API")
        result = await api.restart_all_nodes()
        logger.debug(f"Restart all nodes result: {result}")
        
        if result:
            text = "✅ Команда перезагрузки всех нод отправлена успешно!\n\n"
            text += "⏳ Пожалуйста, подождите несколько минут для завершения перезагрузки.\n"
            text += "💡 Вы можете проверить статус нод через меню управления нодами."
            log_user_action(user.telegram_id, "restart_all_nodes", "Success")
        else:
            text = "❌ Ошибка при отправке команды перезагрузки\n\n"
            text += "Возможные причины:\n"
            text += "• Ноды уже перезагружаются\n"
            text += "• Проблема с API соединением\n"
            text += "• Недостаточно прав для операции\n\n"
            text += "🔄 Попробуйте повторить операцию через несколько минут"
        
        await callback.message.edit_text(
            text,
            reply_markup=admin_system_keyboard(user.language)
        )
    
    except Exception as e:
        logger.error(f"Error restarting all nodes: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Критическая ошибка при перезагрузке\n\n"
            f"Детали: {str(e)[:100]}{'...' if len(str(e)) > 100 else ''}\n\n"
            f"Обратитесь к администратору для решения проблемы.",
            reply_markup=admin_system_keyboard(user.language)
        )

@admin_router.callback_query(F.data.startswith("node_details_"))
async def node_details_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        node_id = callback.data.replace("node_details_", "")
        
        if not api:
            await callback.answer("❌ API недоступен", show_alert=True)
            return
        
        nodes = await api.get_all_nodes()
        node = None
        
        for n in nodes:
            if str(n.get('id')) == node_id or str(n.get('uuid')) == node_id:
                node = n
                break
        
        if not node:
            await callback.answer("❌ Нода не найдена", show_alert=True)
            return
        
        text = "🖥 **Детальная информация о ноде**\n\n"
        
        text += f"📛 **Название:** {node.get('name', 'Unknown')}\n"
        text += f"🆔 **ID:** `{node.get('id', node.get('uuid', 'N/A'))}`\n"
        
        status = node.get('status', 'unknown')
        status_emoji = {
            'online': '🟢',
            'offline': '🔴',
            'disabled': '⚫',
            'disconnected': '🔴',
            'xray_stopped': '🟡'
        }.get(status, '⚪')
        
        text += f"🔘 **Статус:** {status_emoji} {status.upper()}\n\n"
        
        text += "📡 **Подключение:**\n"
        text += f"├ Подключена: {'✅' if node.get('isConnected') else '❌'}\n"
        text += f"├ Включена: {'✅' if not node.get('isDisabled') else '❌'}\n"
        text += f"├ Нода онлайн: {'✅' if node.get('isNodeOnline') else '❌'}\n"
        text += f"└ Xray работает: {'✅' if node.get('isXrayRunning') else '❌'}\n\n"
        
        text += "🌍 **Местоположение:**\n"
        if node.get('countryCode'):
            text += f"├ Страна: {node['countryCode']}\n"
        if node.get('address'):
            text += f"└ Адрес: `{node['address']}`\n"
        text += "\n"
        
        text += "💻 **Информация о системе:**\n"
        if node.get('cpuModel'):
            cpu_model = node['cpuModel']
            if len(cpu_model) > 40:
                cpu_model = cpu_model[:37] + "..."
            text += f"├ CPU: {cpu_model}\n"
        
        if node.get('totalRam'):
            text += f"├ RAM: {node['totalRam']}\n"
        
        if node.get('nodeVersion'):
            text += f"├ Версия ноды: {node['nodeVersion']}\n"
        
        if node.get('xrayVersion'):
            text += f"└ Версия Xray: {node['xrayVersion']}\n"
        text += "\n"
        
        if node.get('cpuUsage') or node.get('memUsage'):
            text += "📊 **Использование ресурсов:**\n"
            if node.get('cpuUsage'):
                cpu = node['cpuUsage']
                cpu_bar = create_progress_bar(cpu)
                text += f"├ CPU: {cpu_bar} {cpu:.1f}%\n"
            if node.get('memUsage'):
                mem = node['memUsage']
                mem_bar = create_progress_bar(mem)
                text += f"└ RAM: {mem_bar} {mem:.1f}%\n"
            text += "\n"
        
        text += "⏱ **Время работы и трафик:**\n"
        if node.get('xrayUptime'):
            uptime_seconds = int(node['xrayUptime'])
            uptime_hours = uptime_seconds // 3600
            uptime_days = uptime_hours // 24
            uptime_hours = uptime_hours % 24
            
            if uptime_days > 0:
                text += f"├ Время работы Xray: {uptime_days}д {uptime_hours}ч\n"
            else:
                text += f"├ Время работы Xray: {uptime_hours}ч {(uptime_seconds % 3600) // 60}м\n"
        
        if node.get('trafficUsedBytes'):
            traffic_used = format_bytes(node['trafficUsedBytes'])
            text += f"├ Использовано трафика: {traffic_used}\n"
        
        if node.get('usersCount') is not None:
            text += f"└ Активных пользователей: {node['usersCount']}\n"
        text += "\n"
        
        if node.get('viewPosition'):
            text += f"📌 **Позиция в списке:** {node['viewPosition']}\n\n"
        
        keyboard = create_node_actions_keyboard(node_id, status, user.language)
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing node details: {e}")
        await callback.answer("❌ Ошибка загрузки информации", show_alert=True)

async def show_system_stats(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, force_refresh: bool = False):
    try:
        db_stats = await db.get_stats()
        current_time = datetime.now()
        
        text = "📊 Системная статистика\n\n"
        
        text += "💾 База данных бота:\n"
        text += f"👥 Пользователей: {db_stats['total_users']}\n"
        text += f"📋 Подписок: {db_stats['total_subscriptions_non_trial']}\n"
        text += f"💰 Доходы: {db_stats['total_revenue']} руб.\n\n"
        
        if api:
            text += "🔗 API RemnaWave: 🟢 Подключен\n\n"
            
            try:
                logger.info("=== FETCHING ENHANCED SYSTEM STATS ===")
                
                await callback.answer("📊 Загружаю статистику системы...")
                
                system_stats = await api.get_system_stats()
                
                if system_stats:
                    text += "🖥 Система RemnaWave:\n"
                    
                    total_users = system_stats.get('total_users', 0)
                    active_users = system_stats.get('active_users', 0)
                    disabled_users = system_stats.get('disabled_users', 0)
                    limited_users = system_stats.get('limited_users', 0)
                    expired_users = system_stats.get('expired_users', 0)
                    
                    text += f"👤 Пользователей в системе: {total_users}\n"
                    text += f"✅ Активных: {active_users}\n"
                    
                    online_stats = system_stats.get('online_stats', {})
                    if online_stats:
                        online_now = online_stats.get('online_now', 0)
                        last_day = online_stats.get('last_day', 0)
                        last_week = online_stats.get('last_week', 0)
                        never_online = online_stats.get('never_online', 0)
                        
                        text += f"🟢 Онлайн сейчас: {online_now}\n"
                        text += f"📅 За сутки: {last_day}\n"
                        text += f"📅 За неделю: {last_week}\n"
                        
                        if never_online > 0:
                            text += f"⚫ Никогда не подключались: {never_online}\n"
                    
                    if disabled_users > 0 or limited_users > 0 or expired_users > 0:
                        text += f"❌ Неактивных: {disabled_users + limited_users + expired_users}\n"
                        if disabled_users > 0:
                            text += f"  • Отключено: {disabled_users}\n"
                        if limited_users > 0:
                            text += f"  • Ограничено: {limited_users}\n"
                        if expired_users > 0:
                            text += f"  • Истекло: {expired_users}\n"
                    
                    nodes_info = system_stats.get('nodes', {})
                    if nodes_info:
                        total_nodes = nodes_info.get('total', 0)
                        online_nodes = nodes_info.get('online', 0)
                        offline_nodes = nodes_info.get('offline', 0)
                        
                        text += f"\n📡 Ноды ({total_nodes} шт.):\n"
                        text += f"🟢 Онлайн: {online_nodes}\n"
                        if offline_nodes > 0:
                            text += f"🔴 Оффлайн: {offline_nodes}\n"
                        
                        if total_nodes > 0:
                            if online_nodes >= total_nodes:
                                health_status = "🟢 Отличное"
                            else:
                                health_percent = (online_nodes / total_nodes) * 100
                                if health_percent >= 80:
                                    health_status = "🟡 Хорошее"
                                elif health_percent >= 50:
                                    health_status = "🟠 Удовлетворительное"
                                else:
                                    health_status = "🔴 Критическое"
                            
                            text += f"🏥 Состояние: {health_status}\n"
                    
                    system_resources = system_stats.get('system_resources', {})
                    if system_resources:
                        text += f"\n💻 Системные ресурсы:\n"
                        
                        cpu_info = system_resources.get('cpu', {})
                        if cpu_info.get('cores'):
                            cores = cpu_info.get('cores', 0)
                            physical_cores = cpu_info.get('physical_cores', 0)
                            text += f"🔧 CPU: {cores} ядер"
                            if physical_cores != cores:
                                text += f" ({physical_cores} физических)"
                            text += "\n"
                        
                        memory_info = system_resources.get('memory', {})
                        if memory_info.get('total_gb'):
                            total_gb = memory_info.get('total_gb', 0)
                            active_gb = memory_info.get('active_gb', 0)
                            available_gb = memory_info.get('available_gb', 0)
                            usage_percent = memory_info.get('usage_percent', 0)
                            
                            text += f"💾 RAM: {active_gb:.1f}/{total_gb:.1f} ГБ ({usage_percent:.1f}%)\n"
                            text += f"📈 Доступно: {available_gb:.1f} ГБ\n"
                        
                        uptime = system_resources.get('uptime', 0)
                        if uptime > 0:
                            uptime_hours = int(uptime // 3600)
                            uptime_days = uptime_hours // 24
                            uptime_hours = uptime_hours % 24
                            
                            if uptime_days > 0:
                                text += f"⏱ Время работы: {uptime_days}д {uptime_hours}ч\n"
                            else:
                                text += f"⏱ Время работы: {uptime_hours}ч\n"
                    
                    total_traffic = system_stats.get('total_traffic_bytes', '0')
                    if total_traffic and total_traffic != '0':
                        try:
                            traffic_bytes = int(total_traffic)
                            traffic_formatted = format_bytes(traffic_bytes)
                            text += f"\n📊 Общий трафик пользователей: {traffic_formatted}\n"
                        except (ValueError, TypeError):
                            pass
                    
                    bandwidth_stats = system_stats.get('bandwidth', {})
                    if bandwidth_stats:
                        text += f"\n📈 **Трафик системы:**\n"
                        
                        if 'bandwidthLastTwoDays' in bandwidth_stats:
                            daily_data = bandwidth_stats['bandwidthLastTwoDays']
                            current_day = daily_data.get('current', '0')
                            previous_day = daily_data.get('previous', '0')
                            difference = daily_data.get('difference', '0')
                            
                            if current_day != '0':
                                text += f"• За сегодня: {current_day}\n"
                                if previous_day != '0':
                                    text += f"• За вчера: {previous_day}\n"
                                    
                                    if difference.startswith('-'):
                                        diff_emoji = "📉"
                                        diff_text = difference[1:]
                                    elif difference.startswith('+') or not difference.startswith('0'):
                                        diff_emoji = "📈"
                                        diff_text = difference.replace('+', '')
                                    else:
                                        diff_emoji = "➡️"
                                        diff_text = "без изменений"
                                    
                                    text += f"• Изменение: {diff_emoji} {diff_text}\n"
                        
                        if 'bandwidthCalendarMonth' in bandwidth_stats:
                            current_month = bandwidth_stats['bandwidthCalendarMonth'].get('current', '0')
                            if current_month != '0':
                                text += f"• За месяц: {current_month}\n"
                        
                        if 'bandwidthCurrentYear' in bandwidth_stats:
                            current_year = bandwidth_stats['bandwidthCurrentYear'].get('current', '0')
                            if current_year != '0':
                                text += f"• За год: {current_year}\n"
                    
                    logger.info(f"Users stats: Total={total_users}, Active={active_users}, Online={online_stats.get('online_now', 0) if online_stats else 0}")
                    
                else:
                    text += "\n❌ Ошибка получения статистики RemnaWave\n"
                    
            except Exception as api_error:
                logger.error(f"Failed to get RemnaWave stats: {api_error}", exc_info=True)
                text += "\n❌ Ошибка получения статистики RemnaWave\n"
                text += f"Детали: {str(api_error)[:60]}...\n"
        else:
            text += "\n🔗 API RemnaWave: 🔴 Недоступен\n"
        
        text += f"\n🕐 Обновлено: {format_datetime(current_time, user.language)}"
        
        keyboard = system_stats_keyboard(user.language, timestamp=int(current_time.timestamp()) if force_refresh else None)
        
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='Markdown')
        except Exception as edit_error:
            if "message is not modified" in str(edit_error).lower():
                await callback.answer("✅ Статистика обновлена", show_alert=False)
            else:
                logger.error(f"Failed to edit system stats message: {edit_error}")
                raise edit_error
        
    except Exception as e:
        logger.error(f"Critical error in show_system_stats: {e}", exc_info=True)
        try:
            await callback.message.edit_text(
                f"❌ Критическая ошибка получения статистики\n\n"
                f"Детали: {str(e)[:100]}{'...' if len(str(e)) > 100 else ''}\n\n"
                f"Обратитесь к администратору для решения проблемы.",
                reply_markup=admin_system_keyboard(user.language)
            )
        except:
            await callback.answer("❌ Критическая ошибка системы", show_alert=True)

def create_progress_bar(percent: float, length: int = 10) -> str:
    filled = int(percent / 100 * length)
    bar = '█' * filled + '░' * (length - filled)
    return f"[{bar}]"

def create_node_actions_keyboard(node_id: str, status: str, language: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    if status == 'disabled':
        buttons.append([
            InlineKeyboardButton(text="✅ Включить ноду", callback_data=f"enable_node_{node_id}")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="⚫ Отключить ноду", callback_data=f"disable_node_{node_id}")
        ])
    
    buttons.append([
        InlineKeyboardButton(text="🔄 Перезагрузить ноду", callback_data=f"restart_node_{node_id}")
    ])
    
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить информацию", callback_data=f"refresh_node_{node_id}")
    ])
    
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад к списку нод", callback_data="nodes_management")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("enable_node_"))
async def enable_node_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        node_id = callback.data.replace("enable_node_", "")
        
        if not api:
            await callback.answer("❌ API недоступен", show_alert=True)
            return
        
        await callback.answer("🔄 Включаю ноду...")
        
        result = await api.enable_node(node_id)
        
        if result:
            await callback.answer("✅ Нода успешно включена", show_alert=True)
            log_user_action(user.telegram_id, "node_enabled", f"Node ID: {node_id}")
            
            await node_details_callback(callback, user, api=api)
        else:
            await callback.answer("❌ Ошибка включения ноды", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error enabling node: {e}")
        await callback.answer("❌ Ошибка операции", show_alert=True)

@admin_router.callback_query(F.data.startswith("disable_node_"))
async def disable_node_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        node_id = callback.data.replace("disable_node_", "")
        
        if not api:
            await callback.answer("❌ API недоступен", show_alert=True)
            return
        
        await callback.answer("🔄 Отключаю ноду...")
        
        result = await api.disable_node(node_id)
        
        if result:
            await callback.answer("✅ Нода успешно отключена", show_alert=True)
            log_user_action(user.telegram_id, "node_disabled", f"Node ID: {node_id}")
            
            await node_details_callback(callback, user, api=api)
        else:
            await callback.answer("❌ Ошибка отключения ноды", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error disabling node: {e}")
        await callback.answer("❌ Ошибка операции", show_alert=True)

@admin_router.callback_query(F.data.startswith("restart_node_"))
async def restart_node_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    node_id = callback.data.replace("restart_node_", "")
    
    await callback.message.edit_text(
        f"⚠️ Вы уверены, что хотите перезагрузить ноду ID: {node_id}?",
        reply_markup=confirm_restart_keyboard(node_id, user.language)
    )

@admin_router.callback_query(F.data.startswith("confirm_restart_node_"))
async def confirm_restart_node_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        node_id = callback.data.replace("confirm_restart_node_", "")
        await callback.answer("🔄 Перезагружаю ноду...")
        
        if api:
            await callback.message.edit_text(
                f"✅ Команда перезагрузки ноды {node_id} отправлена!",
                reply_markup=admin_system_keyboard(user.language)
            )
            log_user_action(user.telegram_id, "restart_node", f"Node ID: {node_id}")
        else:
            await callback.message.edit_text(
                "❌ API недоступен",
                reply_markup=admin_system_keyboard(user.language)
            )
    
    except Exception as e:
        logger.error(f"Error restarting node: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при перезагрузке ноды",
            reply_markup=admin_system_keyboard(user.language)
        )

@admin_router.callback_query(F.data.startswith("refresh_node_"))
async def refresh_node_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        node_id = callback.data.replace("refresh_node_", "")
        
        if not api:
            await callback.answer("❌ API недоступен", show_alert=True)
            return
        
        await callback.answer("🔄 Обновляю информацию о ноде...")
        
        nodes = await api.get_all_nodes()
        node = None
        
        for n in nodes:
            if str(n.get('id')) == node_id or str(n.get('uuid')) == node_id:
                node = n
                break
        
        if not node:
            await callback.answer("❌ Нода не найдена", show_alert=True)
            return
        
        from datetime import datetime
        current_time = datetime.now().strftime("%H:%M:%S")
        
        text = "🖥 **Детальная информация о ноде**\n\n"
        
        text += f"📛 **Название:** {node.get('name', 'Unknown')}\n"
        text += f"🆔 **ID:** `{node.get('id', node.get('uuid', 'N/A'))}`\n"
        
        status = node.get('status', 'unknown')
        status_emoji = {
            'online': '🟢',
            'offline': '🔴',
            'disabled': '⚫',
            'disconnected': '🔴',
            'xray_stopped': '🟡'
        }.get(status, '⚪')
        
        text += f"🔘 **Статус:** {status_emoji} {status.upper()}\n\n"
        
        text += "📡 **Подключение:**\n"
        text += f"├ Подключена: {'✅' if node.get('isConnected') else '❌'}\n"
        text += f"├ Включена: {'✅' if not node.get('isDisabled') else '❌'}\n"
        text += f"├ Нода онлайн: {'✅' if node.get('isNodeOnline') else '❌'}\n"
        text += f"└ Xray работает: {'✅' if node.get('isXrayRunning') else '❌'}\n\n"
        
        text += "🌍 **Местоположение:**\n"
        if node.get('countryCode'):
            text += f"├ Страна: {node['countryCode']}\n"
        if node.get('address'):
            text += f"└ Адрес: `{node['address']}`\n"
        text += "\n"
        
        text += "💻 **Информация о системе:**\n"
        if node.get('cpuModel'):
            cpu_model = node['cpuModel']
            if len(cpu_model) > 40:
                cpu_model = cpu_model[:37] + "..."
            text += f"├ CPU: {cpu_model}\n"
        
        if node.get('totalRam'):
            text += f"├ RAM: {node['totalRam']}\n"
        
        if node.get('nodeVersion'):
            text += f"├ Версия ноды: {node['nodeVersion']}\n"
        
        if node.get('xrayVersion'):
            text += f"└ Версия Xray: {node['xrayVersion']}\n"
        text += "\n"
        
        if node.get('cpuUsage') or node.get('memUsage'):
            text += "📊 **Использование ресурсов:**\n"
            if node.get('cpuUsage'):
                cpu = node['cpuUsage']
                cpu_bar = create_progress_bar(cpu)
                text += f"├ CPU: {cpu_bar} {cpu:.1f}%\n"
            if node.get('memUsage'):
                mem = node['memUsage']
                mem_bar = create_progress_bar(mem)
                text += f"└ RAM: {mem_bar} {mem:.1f}%\n"
            text += "\n"
        
        text += "⏱ **Время работы и трафик:**\n"
        if node.get('xrayUptime'):
            uptime_seconds = int(node['xrayUptime'])
            uptime_hours = uptime_seconds // 3600
            uptime_days = uptime_hours // 24
            uptime_hours = uptime_hours % 24
            
            if uptime_days > 0:
                text += f"├ Время работы Xray: {uptime_days}д {uptime_hours}ч\n"
            else:
                text += f"├ Время работы Xray: {uptime_hours}ч {(uptime_seconds % 3600) // 60}м\n"
        
        if node.get('trafficUsedBytes'):
            traffic_used = format_bytes(node['trafficUsedBytes'])
            text += f"├ Использовано трафика: {traffic_used}\n"
        
        if node.get('usersCount') is not None:
            text += f"└ Активных пользователей: {node['usersCount']}\n"
        text += "\n"
        
        if node.get('viewPosition'):
            text += f"📌 **Позиция в списке:** {node['viewPosition']}\n\n"
        
        text += f"🕐 _Обновлено: {current_time}_"
        
        keyboard = create_node_actions_keyboard(node_id, status, user.language)
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        except Exception as edit_error:
            if "message is not modified" in str(edit_error).lower():
                await callback.answer("✅ Информация актуальна", show_alert=False)
            else:
                logger.error(f"Error editing node details message: {edit_error}")
                await callback.answer("❌ Ошибка обновления", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error refreshing node details: {e}")
        await callback.answer("❌ Ошибка обновления", show_alert=True)

@admin_router.callback_query(F.data == "system_users")
async def system_users_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        text = "👥 Управление пользователями системы RemnaWave\n\n"
        text += "Выберите действие из меню ниже:"
        
        keyboard = system_users_keyboard(user.language)
        
        await callback.message.edit_text(
            text, 
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error in system_users_callback: {e}")
        await callback.answer("Меню пользователей системы", show_alert=False)
        
        try:
            await callback.message.answer(
                "👥 Управление пользователями системы RemnaWave\n\nВыберите действие:",
                reply_markup=system_users_keyboard(user.language)
            )
        except Exception as send_error:
            logger.error(f"Failed to send new message: {send_error}")

async def safe_edit_message(callback: CallbackQuery, text: str, reply_markup=None, parse_mode=None, answer_text="✅ Обновлено"):
    try:
        await callback.message.edit_text(
            text, 
            reply_markup=reply_markup, 
            parse_mode=parse_mode
        )
    except Exception as e:
        if "message is not modified" in str(e).lower():
            await callback.answer(answer_text, show_alert=False)
        else:
            logger.error(f"Error editing message: {e}")
            try:
                await callback.answer(answer_text, show_alert=False)
            except:
                pass 


@admin_router.callback_query(F.data == "bulk_operations")
async def bulk_operations_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "🗂 Массовые операции с пользователями\n\n"
        "⚠️ Внимание: эти операции затрагивают всех пользователей системы!",
        reply_markup=bulk_operations_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "bulk_reset_traffic")
async def bulk_reset_traffic_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, сбросить", callback_data="confirm_bulk_reset_traffic"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="bulk_operations")
        ]
    ])
    
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите сбросить трафик для ВСЕХ пользователей системы?",
        reply_markup=keyboard
    )

@admin_router.callback_query(F.data == "confirm_bulk_reset_traffic")
async def confirm_bulk_reset_traffic_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        await callback.answer("🔄 Сбрасываю трафик для всех пользователей...")
        
        if api:
            await callback.message.edit_text("⏳ Выполняется массовый сброс трафика...")
            
            result = await api.bulk_reset_all_traffic()
            
            if result:
                await callback.message.edit_text(
                    "✅ Трафик сброшен для всех пользователей!",
                    reply_markup=bulk_operations_keyboard(user.language)
                )
                log_user_action(user.telegram_id, "bulk_reset_traffic", "All users")
            else:
                await callback.message.edit_text(
                    "❌ Ошибка при сбросе трафика (возможно, нет пользователей)",
                    reply_markup=bulk_operations_keyboard(user.language)  
                )
        else:
            await callback.message.edit_text(
                "❌ API недоступен",
                reply_markup=bulk_operations_keyboard(user.language)
            )
    
    except Exception as e:
        logger.error(f"Error in bulk traffic reset: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при сбросе трафика",
            reply_markup=bulk_operations_keyboard(user.language)
        )

@admin_router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        db_stats = await db.get_stats()
        
        text = "📊 Краткая статистика\n\n"
        text += "💾 База данных бота:\n"
        text += f"👥 Пользователей: {db_stats['total_users']}\n"
        text += f"📋 Подписок: {db_stats['total_subscriptions_non_trial']}\n"
        text += f"💰 Доходы: {db_stats['total_revenue']} руб.\n"
        
        if api:
            try:
                nodes_stats = await api.get_nodes_statistics()
                if nodes_stats and 'data' in nodes_stats:
                    nodes = nodes_stats['data']
                    online_nodes = len([n for n in nodes if n.get('status') == 'online'])
                    text += f"\n🖥 Ноды RemnaWave: {online_nodes}/{len(nodes)} онлайн"
            except Exception as e:
                logger.warning(f"Failed to get quick RemnaWave stats: {e}")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖥 Подробная системная статистика", callback_data="admin_system")],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats")],
            [InlineKeyboardButton(text="🔙 " + t('back', user.language), callback_data="admin_panel")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        await callback.message.edit_text(
            t('error_occurred', user.language),
            reply_markup=back_keyboard("admin_panel", user.language)
        )

@admin_router.callback_query(F.data == "list_all_system_users")
async def list_all_system_users_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, state: FSMContext = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if state:
        await state.clear()
        await state.update_data(users_page=0)
    
    if not api:
        await callback.message.edit_text(
            "❌ API RemnaWave недоступен\n\n"
            "Для просмотра пользователей системы необходимо подключение к API.",
            reply_markup=back_keyboard("admin_system", user.language)
        )
        await callback.answer()
        return
    
    await show_system_users_list_paginated(callback, user, api, state, page=0)

async def show_system_users_list_paginated(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, 
                                           state: FSMContext = None, page: int = 0):
    try:
        if not api:
            await callback.message.edit_text(
                "❌ API недоступен",
                reply_markup=system_users_keyboard(user.language)
            )
            return
        
        await callback.answer("📋 Загружаю список пользователей...")
        
        all_users = await api.get_all_system_users_full()
        if not all_users:
            await callback.message.edit_text(
                "❌ Пользователи не найдены",
                reply_markup=system_users_keyboard(user.language)
            )
            return
        
        all_users.sort(key=lambda x: (
            0 if x.get('status') == 'ACTIVE' else 1,
            x.get('createdAt', ''),
        ), reverse=True)
        
        users_per_page = 8
        total_pages = (len(all_users) + users_per_page - 1) // users_per_page
        start_idx = page * users_per_page
        end_idx = min(start_idx + users_per_page, len(all_users))
        page_users = all_users[start_idx:end_idx]
        
        active_count = len([u for u in all_users if u.get('status') == 'ACTIVE'])
        disabled_count = len(all_users) - active_count
        with_telegram = len([u for u in all_users if u.get('telegramId')])
        
        text = f"👥 Пользователи системы RemnaWave\n"
        text += f"📄 Страница {page + 1} из {total_pages}\n\n"
        
        text += f"📊 Статистика:\n"
        text += f"├ Всего: {len(all_users)}\n"
        text += f"├ ✅ Активных: {active_count}\n"
        text += f"├ ❌ Отключенных: {disabled_count}\n"
        text += f"└ 📱 С Telegram: {with_telegram}\n\n"
        
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for i, sys_user in enumerate(page_users, start=start_idx + 1):
            status = sys_user.get('status', 'UNKNOWN')
            if status == 'ACTIVE':
                status_icon = "🟢"
            elif status == 'DISABLED':
                status_icon = "🔴"
            elif status == 'LIMITED':
                status_icon = "🟡"
            elif status == 'EXPIRED':
                status_icon = "⏰"
            else:
                status_icon = "⚪"
            
            username = sys_user.get('username', 'N/A')
            username = username.replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
            
            short_uuid = sys_user.get('shortUuid', '')[:8] + "..." if sys_user.get('shortUuid') else 'N/A'
            
            text += f"{i}. {status_icon} {username}\n" 
            
            if sys_user.get('telegramId'):
                telegram_id = str(sys_user['telegramId'])
                text += f"   📱 TG: {telegram_id}\n" 
            
            text += f"   🔗 {short_uuid}\n"
            
            if sys_user.get('expireAt'):
                try:
                    expire_dt = datetime.fromisoformat(sys_user['expireAt'].replace('Z', '+00:00'))
                    days_left = (expire_dt - datetime.now()).days
                    
                    if days_left < 0:
                        text += f"   ❌ Истекла {abs(days_left)} дн. назад\n"
                    elif days_left == 0:
                        text += f"   ⚠️ Истекает сегодня\n"
                    elif days_left <= 3:
                        text += f"   ⚠️ Осталось {days_left} дн.\n"
                    else:
                        text += f"   ⏰ До {expire_dt.strftime('%d.%m.%Y')}\n"
                except:
                    expire_date = sys_user['expireAt'][:10] if sys_user['expireAt'] else 'N/A'
                    text += f"   ⏰ {expire_date}\n"
            
            traffic_limit = sys_user.get('trafficLimitBytes', 0)
            used_traffic = sys_user.get('usedTrafficBytes', 0)
            
            if traffic_limit > 0:
                usage_percent = (used_traffic / traffic_limit) * 100
                if usage_percent >= 90:
                    traffic_icon = "🔴"
                elif usage_percent >= 70:
                    traffic_icon = "🟡"
                else:
                    traffic_icon = "🟢"
                
                used_str = format_bytes(used_traffic)
                limit_str = format_bytes(traffic_limit)
                text += f"   📊 {traffic_icon} {usage_percent:.0f}% ({used_str}/{limit_str})\n"
            else:
                used_str = format_bytes(used_traffic)
                text += f"   📊 ♾️ Безлимит ({used_str})\n"
            
            text += "\n"
        
        keyboard = create_users_pagination_keyboard(page, total_pages, user.language)
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error showing system users: {e}", exc_info=True)
        try:
            await callback.message.edit_text(
                f"❌ Ошибка загрузки пользователей\n\nДетали: {str(e)[:100]}",
                reply_markup=system_users_keyboard(user.language)
            )
        except:
            await callback.answer("❌ Ошибка загрузки пользователей", show_alert=True)

def create_users_pagination_keyboard(current_page: int, total_pages: int, language: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    buttons.append([
        InlineKeyboardButton(text="🔍 Поиск", callback_data="search_user_uuid"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_users_page_{current_page}")
    ])
    
    if total_pages > 1:
        nav_row = []
        
        if current_page > 0:
            nav_row.append(InlineKeyboardButton(text="⏮", callback_data="users_page_0"))
        
        if current_page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"users_page_{current_page - 1}"))
        
        nav_row.append(InlineKeyboardButton(text=f"{current_page + 1}/{total_pages}", callback_data="noop"))
        
        if current_page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"users_page_{current_page + 1}"))
        
        if current_page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="⏭", callback_data=f"users_page_{total_pages - 1}"))
        
        buttons.append(nav_row)
    
    buttons.append([
        InlineKeyboardButton(text="✅ Только активные", callback_data="filter_users_active"),
        InlineKeyboardButton(text="📱 С Telegram", callback_data="filter_users_telegram")
    ])
    
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="system_users")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("users_page_"))
async def users_page_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, state: FSMContext = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        page = int(callback.data.split("_")[-1])
        await show_system_users_list_paginated(callback, user, api, state, page)
    except Exception as e:
        logger.error(f"Error in pagination: {e}")
        await callback.answer("❌ Ошибка навигации", show_alert=True)

@admin_router.callback_query(F.data.startswith("refresh_system_users_"))
async def refresh_system_users_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await show_system_users_list(callback, user, api, force_refresh=True)

def system_stats_keyboard(language: str, timestamp: int = None) -> InlineKeyboardMarkup:
    refresh_callback = f"refresh_system_stats_{timestamp}" if timestamp else "refresh_system_stats"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖥 Управление нодами", callback_data="nodes_management")],
        [InlineKeyboardButton(text="👥 Пользователи системы", callback_data="system_users")],
        [InlineKeyboardButton(text="🗂 Массовые операции", callback_data="bulk_operations")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=refresh_callback)],
        [InlineKeyboardButton(text="🔙 " + t('back', language), callback_data="admin_system")]
    ])

def nodes_management_keyboard(nodes: List[Dict], language: str, timestamp: int = None) -> InlineKeyboardMarkup:
    buttons = []
    
    if nodes:
        for i, node in enumerate(nodes[:3]):
            node_id = node.get('id', f'{i}')
            node_name = node.get('name', f'Node-{i+1}')
            is_online = (node.get('isConnected', False) and 
                        not node.get('isDisabled', True) and 
                        node.get('isNodeOnline', False) and 
                        node.get('isXrayRunning', False))
            status_emoji = "🟢" if is_online else "🔴"
            
            buttons.append([
                InlineKeyboardButton(
                    text=f"{status_emoji} {node_name}",
                    callback_data=f"node_details_{node_id}"
                )
            ])
        
        buttons.append([
            InlineKeyboardButton(text="🔄 Перезагрузить все ноды", callback_data="restart_all_nodes")
        ])
    
    refresh_callback = f"refresh_nodes_stats_{timestamp}" if timestamp else "refresh_nodes_stats"
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=refresh_callback)
    ])
    
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_system")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("refresh_system_stats_"))
async def refresh_system_stats_with_timestamp_callback(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await show_system_stats(callback, user, db, api, force_refresh=True)

@admin_router.callback_query(F.data == "users_statistics")
async def users_statistics_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        if not api:
            await callback.message.edit_text(
                "❌ API недоступен",
                reply_markup=system_users_keyboard(user.language)
            )
            return
        
        await callback.answer("📊 Собираю статистику...")
        
        system_stats = await api.get_system_stats()
        users_count = await api.get_users_count()
        
        text = "📊 Детальная статистика пользователей\n\n"
        
        if users_count is not None:
            text += f"👥 Всего пользователей: {users_count}\n"
        
        if system_stats:
            if 'users' in system_stats:
                text += f"• Активных пользователей: {system_stats['users']}\n"
            
            if 'bandwidth' in system_stats:
                bandwidth = system_stats['bandwidth']
                if bandwidth.get('downlink') or bandwidth.get('uplink'):
                    text += f"\n📈 Трафик:\n"
                    text += f"• Загружено: {format_bytes(bandwidth.get('downlink', 0))}\n"
                    text += f"• Отдано: {format_bytes(bandwidth.get('uplink', 0))}\n"
        
        health_info = await api.get_system_health()
        if health_info:
            text += f"\n🏥 Состояние системы: {health_info.get('status', 'unknown')}\n"
            if 'nodes_online' in health_info and 'nodes_total' in health_info:
                text += f"🖥 Ноды: {health_info['nodes_online']}/{health_info['nodes_total']} онлайн\n"
        
        text += f"\n🕐 Обновлено: {format_datetime(datetime.now(), user.language)}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="users_statistics")],
            [InlineKeyboardButton(text="👥 Список пользователей", callback_data="list_all_system_users")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="system_users")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error getting users statistics: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения статистики",
            reply_markup=system_users_keyboard(user.language)
        )

@admin_router.callback_query(F.data == "search_user_uuid")
async def search_user_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "🔍 Поиск пользователя\n\n"
        "Вы можете искать по:\n"
        "• UUID (полный)\n"
        "• Short UUID\n"
        "• Telegram ID\n"
        "• Username\n"
        "• Email\n\n"
        "📝 Введите любой идентификатор:",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_search_user_any)

@admin_router.message(StateFilter(BotStates.admin_search_user_any))
async def handle_search_user_any(message: Message, state: FSMContext, user: User, api: RemnaWaveAPI = None, db: Database = None, **kwargs):
    search_input = message.text.strip()
    
    if not api:
        await message.answer(
            "❌ API недоступен",
            reply_markup=system_users_keyboard(user.language)
        )
        await state.clear()
        return
    
    try:
        search_msg = await message.answer("🔍 Поиск пользователя...")
        user_data = None
        search_method = None
        
        if validate_squad_uuid(search_input):
            user_data = await api.get_user_by_uuid(search_input)
            search_method = "UUID"
        
        if not user_data:
            try:
                telegram_id = int(search_input)
                user_data = await api.get_user_by_telegram_id(telegram_id)
                search_method = "Telegram ID"
            except ValueError:
                pass
        
        if not user_data:
            user_data = await api.get_user_by_short_uuid(search_input)
            search_method = "Short UUID"
        
        if not user_data:
            user_data = await api.get_user_by_username(search_input)
            search_method = "Username"
        
        if not user_data and '@' in search_input:
            user_data = await api.get_user_by_email(search_input)
            search_method = "Email"
        
        if not user_data:
            await search_msg.edit_text(
                f"❌ Пользователь не найден\n\n"
                f"Искомое значение: `{search_input}`\n\n"
                f"Проверены методы поиска:\n"
                f"• UUID\n"
                f"• Short UUID\n"
                f"• Telegram ID\n"
                f"• Username\n"
                f"• Email\n\n"
                f"Проверьте правильность ввода и попробуйте снова",
                reply_markup=system_users_keyboard(user.language),
                parse_mode='Markdown'
            )
            await state.clear()
            return
        
        local_user = None
        if user_data.get('telegramId') and db:
            local_user = await db.get_user_by_telegram_id(user_data['telegramId'])
        
        text = f"👤 Информация о пользователе\n"
        text += f"🔍 Найден по: {search_method}\n\n"
        
        text += f"📛 Username: `{user_data.get('username', 'N/A')}`\n"
        text += f"🆔 UUID: `{user_data.get('uuid', 'N/A')}`\n"
        text += f"🔗 Short UUID: `{user_data.get('shortUuid', 'N/A')}`\n"
        
        if user_data.get('telegramId'):
            text += f"📱 Telegram ID: `{user_data.get('telegramId')}`\n"
            if local_user:
                text += f"💰 Баланс в боте: {local_user.balance} руб.\n"
        
        if user_data.get('email'):
            text += f"📧 Email: {user_data.get('email')}\n"
        
        status = user_data.get('status', 'UNKNOWN')
        status_emoji = "✅" if status == 'ACTIVE' else "❌"
        text += f"\n🔘 Статус: {status_emoji} {status}\n"
        
        if user_data.get('expireAt'):
            expire_date = user_data['expireAt']
            text += f"⏰ Истекает: {expire_date[:10]}\n"
            
            try:
                expire_dt = datetime.fromisoformat(expire_date.replace('Z', '+00:00'))
                days_left = (expire_dt - datetime.now()).days
                if days_left > 0:
                    text += f"📅 Осталось дней: {days_left}\n"
                else:
                    text += f"❌ Подписка истекла\n"
            except:
                pass
        
        traffic_limit = user_data.get('trafficLimitBytes', 0)
        used_traffic = user_data.get('usedTrafficBytes', 0)
        
        if traffic_limit > 0:
            text += f"\n📊 Лимит трафика: {format_bytes(traffic_limit)}\n"
            text += f"📈 Использовано: {format_bytes(used_traffic)}\n"
            usage_percent = (used_traffic / traffic_limit) * 100
            text += f"📉 Использовано: {usage_percent:.1f}%\n"
        else:
            text += f"\n📊 Лимит трафика: Безлимитный\n"
            text += f"📈 Использовано: {format_bytes(used_traffic)}\n"
        
        keyboard = create_user_management_keyboard(user_data.get('uuid'), user_data.get('status'), user.language)
        
        await search_msg.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error searching user: {e}")

def create_user_management_keyboard(user_uuid: str, status: str, language: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    if status == 'ACTIVE':
        buttons.append([
            InlineKeyboardButton(text="❌ Отключить", callback_data=f"disable_user_{user_uuid}"),
            InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data=f"reset_user_traffic_{user_uuid}")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="✅ Включить", callback_data=f"enable_user_{user_uuid}"),
            InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data=f"reset_user_traffic_{user_uuid}")
        ])
    
    buttons.append([
        InlineKeyboardButton(text="📅 Изменить срок", callback_data=f"edit_user_expiry_{user_uuid}"),
        InlineKeyboardButton(text="📊 Изменить трафик", callback_data=f"edit_user_traffic_{user_uuid}")
    ])
    
    buttons.append([
        InlineKeyboardButton(text="📈 Статистика", callback_data=f"user_usage_stats_{user_uuid}"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_user_{user_uuid}")
    ])
    
    buttons.append([
        InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_user_uuid"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="system_users")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("edit_user_expiry_"))
async def edit_user_expiry_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    user_uuid = callback.data.replace("edit_user_expiry_", "")
    await state.update_data(edit_user_uuid=user_uuid)
    
    await callback.message.edit_text(
        "📅 Изменение срока действия подписки\n\n"
        "Введите новую дату истечения:\n"
        "• YYYY-MM-DD (например: 2025-12-31)\n"
        "• Или количество дней (например: 30)\n\n"
        "📝 Введите значение:",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_edit_user_expiry)

@admin_router.message(StateFilter(BotStates.admin_edit_user_expiry))
async def handle_edit_user_expiry(message: Message, state: FSMContext, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not api:
        await message.answer("❌ API недоступен")
        await state.clear()
        return
    
    data = await state.get_data()
    user_uuid = data.get('edit_user_uuid')
    input_value = message.text.strip()
    
    try:
        new_expiry = None
        
        try:
            days = int(input_value)
            if days > 0:
                new_expiry = datetime.now() + timedelta(days=days)
        except ValueError:
            try:
                new_expiry = datetime.strptime(input_value, "%Y-%m-%d")
            except ValueError:
                await message.answer("❌ Неверный формат даты. Используйте YYYY-MM-DD или количество дней")
                return
        
        if not new_expiry:
            await message.answer("❌ Не удалось определить дату")
            return
        
        expiry_str = new_expiry.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
        result = await api.update_user(user_uuid, {'expireAt': expiry_str, 'status': 'ACTIVE'})
        
        if result:
            await message.answer(
                f"✅ Срок действия обновлен!\n\n"
                f"Новая дата истечения: {new_expiry.strftime('%Y-%m-%d %H:%M')}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Вернуться к пользователю", callback_data=f"refresh_user_{user_uuid}")],
                    [InlineKeyboardButton(text="🔙 В меню", callback_data="system_users")]
                ])
            )
            log_user_action(user.telegram_id, "user_expiry_updated", f"UUID: {user_uuid}, New expiry: {expiry_str}")
        else:
            await message.answer("❌ Ошибка обновления срока действия")
        
    except Exception as e:
        logger.error(f"Error updating user expiry: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
    
    await state.clear()

@admin_router.message(StateFilter(BotStates.admin_edit_user_expiry))
async def handle_edit_user_expiry(message: Message, state: FSMContext, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not api:
        await message.answer("❌ API недоступен")
        await state.clear()
        return
    
    data = await state.get_data()
    user_uuid = data.get('edit_user_uuid')
    input_value = message.text.strip()
    
    try:
        new_expiry = None
        
        try:
            days = int(input_value)
            if days > 0:
                new_expiry = datetime.now() + timedelta(days=days)
        except ValueError:
            try:
                new_expiry = datetime.strptime(input_value, "%Y-%m-%d")
            except ValueError:
                await message.answer("❌ Неверный формат даты. Используйте YYYY-MM-DD или количество дней")
                return
        
        if not new_expiry:
            await message.answer("❌ Не удалось определить дату")
            return
        
        expiry_str = new_expiry.isoformat() + 'Z'
        result = await api.update_user(user_uuid, {'expireAt': expiry_str, 'status': 'ACTIVE'})
        
        if result:
            await message.answer(
                f"✅ Срок действия обновлен!\n\n"
                f"Новая дата истечения: {new_expiry.strftime('%Y-%m-%d %H:%M')}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Вернуться к пользователю", callback_data=f"refresh_user_{user_uuid}")],
                    [InlineKeyboardButton(text="🔙 В меню", callback_data="system_users")]
                ])
            )
            log_user_action(user.telegram_id, "user_expiry_updated", f"UUID: {user_uuid}, New expiry: {expiry_str}")
        else:
            await message.answer("❌ Ошибка обновления срока действия")
        
    except Exception as e:
        logger.error(f"Error updating user expiry: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
    
    await state.clear()

@admin_router.callback_query(F.data.startswith("edit_user_traffic_"))
async def edit_user_traffic_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    user_uuid = callback.data.replace("edit_user_traffic_", "")
    await state.update_data(edit_user_uuid=user_uuid)
    
    await callback.message.edit_text(
        "📊 Изменение лимита трафика\n\n"
        "Введите новый лимит трафика:\n"
        "• Число в ГБ (например: 100)\n"
        "• 0 для безлимитного трафика\n\n"
        "📝 Введите значение:",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_edit_user_traffic)

@admin_router.message(StateFilter(BotStates.admin_edit_user_traffic))
async def handle_edit_user_traffic(message: Message, state: FSMContext, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not api:
        await message.answer("❌ API недоступен")
        await state.clear()
        return
    
    data = await state.get_data()
    user_uuid = data.get('edit_user_uuid')
    
    try:
        traffic_gb = int(message.text.strip())
        if traffic_gb < 0:
            await message.answer("❌ Значение не может быть отрицательным")
            return
        
        result = await api.update_user_traffic_limit(user_uuid, traffic_gb)
        
        if result:
            traffic_text = f"{traffic_gb} ГБ" if traffic_gb > 0 else "Безлимитный"
            await message.answer(
                f"✅ Лимит трафика обновлен!\n\n"
                f"Новый лимит: {traffic_text}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Вернуться к пользователю", callback_data=f"refresh_user_{user_uuid}")],
                    [InlineKeyboardButton(text="🔙 В меню", callback_data="system_users")]
                ])
            )
            log_user_action(user.telegram_id, "user_traffic_updated", f"UUID: {user_uuid}, New limit: {traffic_gb} GB")
        else:
            await message.answer("❌ Ошибка обновления лимита трафика")
        
    except ValueError:
        await message.answer("❌ Введите число")
    except Exception as e:
        logger.error(f"Error updating user traffic: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
    
    await state.clear()

@admin_router.callback_query(F.data.startswith("refresh_user_"))
async def refresh_user_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    user_uuid = callback.data.replace("refresh_user_", "")
    
    if not api:
        await callback.answer("❌ API недоступен", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Обновляю информацию...")
        
        user_data = await api.get_user_by_uuid(user_uuid)
        if not user_data:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        text = f"👤 Информация о пользователе (обновлено)\n\n"
        
        keyboard = create_user_management_keyboard(user_uuid, user_data.get('status'), user.language)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error refreshing user: {e}")
        await callback.answer("❌ Ошибка обновления", show_alert=True)

@admin_router.callback_query(F.data == "sync_remnawave")
async def sync_remnawave_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "🔄 Синхронизация с RemnaWave\n\n"
        "Выберите тип синхронизации:",
        reply_markup=sync_remnawave_keyboard(user.language)
    )

def sync_remnawave_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    buttons = [
        #[InlineKeyboardButton(text="👥 Синхронизировать пользователей", callback_data="sync_users_remnawave")],
        #[InlineKeyboardButton(text="📋 Синхронизировать подписки", callback_data="sync_subscriptions_remnawave")],
        [InlineKeyboardButton(text="🔄 Полная синхронизация", callback_data="sync_full_remnawave")],
        [InlineKeyboardButton(text="👤 Синхронизировать одного", callback_data="sync_single_user")],
        [InlineKeyboardButton(text="🌍 ИМПОРТ ВСЕХ по Telegram ID", callback_data="import_all_by_telegram")],
        [InlineKeyboardButton(text="📋 Просмотр планов", callback_data="view_imported_plans")],
        [InlineKeyboardButton(text="📊 Статус синхронизации", callback_data="sync_status_remnawave")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_system")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data == "sync_users_remnawave")
async def sync_users_remnawave_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, db: Database = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not api or not db:
        await callback.answer("❌ API или база данных недоступны", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Запускаю синхронизацию пользователей...")
        
        progress_msg = await callback.message.edit_text("⏳ Синхронизация пользователей...\n\n0% выполнено")
        
        remna_users = await api.get_all_system_users_full()
        if not remna_users:
            await progress_msg.edit_text(
                "❌ Не удалось получить пользователей из RemnaWave",
                reply_markup=back_keyboard("sync_remnawave", user.language)
            )
            return
        
        total_users = len(remna_users)
        synced = 0
        created = 0
        updated = 0
        errors = 0
        
        for i, remna_user in enumerate(remna_users):
            try:
                if i % 10 == 0:
                    progress = (i / total_users) * 100
                    await progress_msg.edit_text(
                        f"⏳ Синхронизация пользователей...\n\n"
                        f"{progress:.1f}% выполнено\n"
                        f"Обработано: {i}/{total_users}"
                    )
                
                telegram_id = remna_user.get('telegramId')
                if not telegram_id:
                    continue
                
                bot_user = await db.get_user_by_telegram_id(telegram_id)
                
                if not bot_user:
                    bot_user = await db.create_user(
                        telegram_id=telegram_id,
                        username=remna_user.get('username'),
                        language='ru',
                        is_admin=telegram_id in (kwargs.get('config', {}).ADMIN_IDS if 'config' in kwargs else [])
                    )
                    created += 1
                
                if not bot_user.remnawave_uuid:
                    bot_user.remnawave_uuid = remna_user.get('uuid')
                    await db.update_user(bot_user)
                    updated += 1
                
                synced += 1
                
            except Exception as e:
                logger.error(f"Error syncing user {remna_user.get('username')}: {e}")
                errors += 1
        
        result_text = (
            f"✅ Синхронизация пользователей завершена!\n\n"
            f"📊 Результаты:\n"
            f"• Всего пользователей в RemnaWave: {total_users}\n"
            f"• Синхронизировано: {synced}\n"
            f"• Создано новых: {created}\n"
            f"• Обновлено: {updated}\n"
            f"• Ошибок: {errors}"
        )
        
        await progress_msg.edit_text(
            result_text,
            reply_markup=sync_remnawave_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "users_synced", f"Total: {total_users}, Synced: {synced}")
        
    except Exception as e:
        logger.error(f"Error in user sync: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка синхронизации: {str(e)}",
            reply_markup=sync_remnawave_keyboard(user.language)
        )

@admin_router.callback_query(F.data == "sync_subscriptions_remnawave")
async def sync_subscriptions_remnawave_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, db: Database = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not api or not db:
        await callback.answer("❌ API или база данных недоступны", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Запускаю улучшенную синхронизацию подписок...")
        
        progress_msg = await callback.message.edit_text("⏳ Синхронизация подписок...\n\nЭтап 1/4: Получение данных...")
        
        logger.info("=== STARTING SUBSCRIPTION SYNC ===")
        remna_users = await api.get_all_system_users_full()
        
        if not remna_users:
            logger.error("No users returned from RemnaWave API")
            await progress_msg.edit_text(
                "❌ Не удалось получить пользователей из RemnaWave",
                reply_markup=sync_remnawave_keyboard(user.language)
            )
            return
        
        logger.info(f"Got {len(remna_users)} total users from RemnaWave")
        
        users_with_tg = [u for u in remna_users if u.get('telegramId')]
        logger.info(f"Found {len(users_with_tg)} RemnaWave users with Telegram ID")
        
        if users_with_tg:
            first_user = users_with_tg[0]
            logger.info(f"Sample user structure: {list(first_user.keys())}")
            logger.info(f"Sample user: telegramId={first_user.get('telegramId')}, "
                       f"username={first_user.get('username')}, "
                       f"status={first_user.get('status')}, "
                       f"shortUuid={first_user.get('shortUuid')}, "
                       f"expireAt={first_user.get('expireAt')}")
        
        created_subs = 0
        updated_subs = 0
        created_users = 0
        updated_users = 0
        errors = 0
        
        await progress_msg.edit_text("⏳ Синхронизация подписок...\n\nЭтап 1/4: Создание пользователей...")
        
        for i, remna_user in enumerate(users_with_tg):
            try:
                telegram_id = remna_user['telegramId']
                logger.debug(f"Processing user {i+1}/{len(users_with_tg)}: {telegram_id}")
                
                bot_user = await db.get_user_by_telegram_id(telegram_id)
                
                if not bot_user:
                    is_admin = telegram_id in (kwargs.get('config', {}).ADMIN_IDS if 'config' in kwargs else [])
                    bot_user = await db.create_user(
                        telegram_id=telegram_id,
                        username=remna_user.get('username'),
                        first_name=remna_user.get('username'),
                        language='ru',
                        is_admin=is_admin
                    )
                    created_users += 1
                    logger.info(f"Created bot user for Telegram ID: {telegram_id}")
                
                if not bot_user.remnawave_uuid and remna_user.get('uuid'):
                    bot_user.remnawave_uuid = remna_user['uuid']
                    await db.update_user(bot_user)
                    updated_users += 1
                    logger.debug(f"Updated RemnaWave UUID for user {telegram_id}")
                
            except Exception as e:
                logger.error(f"Error creating/updating user {telegram_id}: {e}")
                errors += 1
        
        logger.info(f"User creation phase: created={created_users}, updated={updated_users}, errors={errors}")
        
        await progress_msg.edit_text("⏳ Синхронизация подписок...\n\nЭтап 2/4: Поиск подписок...")
        
        for i, remna_user in enumerate(users_with_tg):
            try:
                telegram_id = remna_user['telegramId']
                short_uuid = remna_user.get('shortUuid')
                status = remna_user.get('status')
                expire_at = remna_user.get('expireAt')
                
                logger.debug(f"Syncing subscription for user {telegram_id}: "
                           f"shortUuid={short_uuid}, status={status}, expireAt={expire_at}")
                
                bot_user = await db.get_user_by_telegram_id(telegram_id)
                if not bot_user:
                    logger.warning(f"Bot user {telegram_id} not found during subscription sync")
                    continue
                
                is_active_in_remna = status == 'ACTIVE'
                has_expiry = bool(expire_at)
                
                if not short_uuid:
                    logger.debug(f"User {telegram_id} has no shortUuid, skipping")
                    continue
                
                existing_sub = await db.get_user_subscription_by_short_uuid(telegram_id, short_uuid)
                
                if existing_sub:
                    logger.debug(f"Found existing subscription for user {telegram_id}")
                    
                    if has_expiry:
                        try:
                            if remna_user['expireAt'].endswith('Z'):
                                expire_dt = datetime.fromisoformat(remna_user['expireAt'].replace('Z', '+00:00'))
                            else:
                                expire_dt = datetime.fromisoformat(remna_user['expireAt'])
                            
                            expire_dt_naive = expire_dt.replace(tzinfo=None) if expire_dt.tzinfo else expire_dt
                            existing_sub.expires_at = expire_dt_naive
                        except Exception as date_error:
                            logger.error(f"Error parsing date for user {telegram_id}: {date_error}")
                    
                    existing_sub.is_active = is_active_in_remna
                    
                    if remna_user.get('trafficLimitBytes') is not None:
                        traffic_gb = remna_user['trafficLimitBytes'] // (1024 * 1024 * 1024) if remna_user['trafficLimitBytes'] > 0 else 0
                        existing_sub.traffic_limit_gb = traffic_gb
                    
                    await db.update_user_subscription(existing_sub)
                    updated_subs += 1
                    
                else:
                    logger.debug(f"No existing subscription found for user {telegram_id}, creating new one")
                    
                    if is_active_in_remna or has_expiry:
                        logger.info(f"Creating new subscription for user {telegram_id}")
                        
                        squad_uuid = None
                        active_squads = remna_user.get('activeInternalSquads', [])
                        
                        if active_squads:
                            first_squad = active_squads[0]
                            if isinstance(first_squad, dict):
                                squad_uuid = first_squad.get('uuid') or first_squad.get('id')
                            else:
                                squad_uuid = str(first_squad)
                        
                        if not squad_uuid:
                            internal_squads = remna_user.get('internalSquads', [])
                            if internal_squads:
                                first_squad = internal_squads[0]
                                if isinstance(first_squad, dict):
                                    squad_uuid = first_squad.get('uuid') or first_squad.get('id')
                                else:
                                    squad_uuid = str(first_squad)
                        
                        subscription_plan = None
                        
                        if squad_uuid:
                            all_plans = await db.get_all_subscriptions(include_inactive=True, exclude_trial=False)
                            for plan in all_plans:
                                if plan.squad_uuid == squad_uuid:
                                    subscription_plan = plan
                                    break
                        
                        if not subscription_plan:
                            traffic_gb = 0
                            if remna_user.get('trafficLimitBytes'):
                                traffic_gb = remna_user['trafficLimitBytes'] // (1024 * 1024 * 1024)
                            
                            plan_name = f"Imported_{remna_user.get('username', 'User')[:10]}"
                            if squad_uuid:
                                plan_name += f"_{squad_uuid[:8]}"
                            
                            subscription_plan = await db.create_subscription(
                                name=plan_name,
                                description=f"Автоматически импортированная подписка из RemnaWave",
                                price=0,
                                duration_days=30,
                                traffic_limit_gb=traffic_gb,
                                squad_uuid=squad_uuid or ''
                            )
                            logger.info(f"Created new subscription plan: {plan_name}")
                        
                        expire_dt_naive = None
                        if has_expiry:
                            try:
                                if remna_user['expireAt'].endswith('Z'):
                                    expire_dt = datetime.fromisoformat(remna_user['expireAt'].replace('Z', '+00:00'))
                                else:
                                    expire_dt = datetime.fromisoformat(remna_user['expireAt'])
                                expire_dt_naive = expire_dt.replace(tzinfo=None) if expire_dt.tzinfo else expire_dt
                            except:
                                expire_dt_naive = datetime.now() + timedelta(days=30)
                        else:
                            expire_dt_naive = datetime.now() + timedelta(days=30)
                        
                        user_subscription = await db.create_user_subscription(
                            user_id=telegram_id,
                            subscription_id=subscription_plan.id,
                            short_uuid=short_uuid,
                            expires_at=expire_dt_naive,
                            is_active=is_active_in_remna
                        )
                        
                        if user_subscription:
                            created_subs += 1
                            logger.info(f"Created subscription for user {telegram_id} with short_uuid {short_uuid}")
                        else:
                            logger.error(f"Failed to create subscription for user {telegram_id}")
                            errors += 1
                
            except Exception as e:
                logger.error(f"Error syncing subscription for user {telegram_id}: {e}")
                errors += 1
        
        await progress_msg.edit_text("⏳ Синхронизация подписок...\n\nЭтап 3/4: Проверка консистентности...")
        
        consistency_fixes = 0
        for remna_user in users_with_tg:
            try:
                telegram_id = remna_user['telegramId']
                user_subs = await db.get_user_subscriptions(telegram_id)
                
                for user_sub in user_subs:
                    if user_sub.expires_at < datetime.now() and user_sub.is_active:
                        user_sub.is_active = False
                        await db.update_user_subscription(user_sub)
                        
                        if remna_user.get('uuid'):
                            await api.update_user(remna_user['uuid'], {'status': 'EXPIRED'})
                        
                        consistency_fixes += 1
                        
            except Exception as e:
                logger.error(f"Error in consistency check for user {telegram_id}: {e}")
        
        await progress_msg.edit_text("⏳ Синхронизация подписок...\n\nЭтап 4/4: Финальная проверка...")
        
        total_bot_users = len(await db.get_all_users())
        total_bot_subs = 0
        active_bot_subs = 0
        
        all_bot_users = await db.get_all_users()
        for bot_user in all_bot_users:
            user_subs = await db.get_user_subscriptions(bot_user.telegram_id)
            total_bot_subs += len(user_subs)
            active_bot_subs += len([s for s in user_subs if s.is_active])
        
        result_text = (
            "✅ Улучшенная синхронизация подписок завершена!\n\n"
            "📊 Результаты синхронизации:\n\n"
            "👥 Пользователи:\n"
            f"• Создано в боте: {created_users}\n"
            f"• Обновлено в боте: {updated_users}\n\n"
            "📋 Подписки:\n"
            f"• Создано новых: {created_subs}\n"
            f"• Обновлено существующих: {updated_subs}\n"
            f"• Исправлено несоответствий: {consistency_fixes}\n"
            f"• Ошибок: {errors}\n\n"
            "📈 Текущее состояние бота:\n"
            f"• Всего пользователей: {total_bot_users}\n"
            f"• Всего подписок: {total_bot_subs}\n"
            f"• Активных подписок: {active_bot_subs}\n\n"
            f"🕐 Завершено: {format_datetime(datetime.now(), user.language)}"
        )
        
        await progress_msg.edit_text(
            result_text,
            reply_markup=sync_remnawave_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "improved_sync_completed", 
                       f"Created: {created_subs}, Updated: {updated_subs}, Users: {created_users}")
        
    except Exception as e:
        logger.error(f"Error in improved subscription sync: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка улучшенной синхронизации\n\nДетали: {str(e)[:200]}",
            reply_markup=sync_remnawave_keyboard(user.language)
        )

@admin_router.callback_query(F.data.startswith("reset_user_traffic_"))
async def reset_user_traffic_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        user_uuid = callback.data.replace("reset_user_traffic_", "")
        
        if not api:
            await callback.answer("❌ API недоступен", show_alert=True)
            return
        
        await callback.answer("🔄 Сбрасываю трафик пользователя...")
        
        result = await api.reset_user_traffic(user_uuid)
        
        if result:
            await callback.answer("✅ Трафик пользователя успешно сброшен", show_alert=True)
            log_user_action(user.telegram_id, "reset_user_traffic", f"UUID: {user_uuid}")
            
            try:
                updated_user = await api.get_user_by_uuid(user_uuid)
                if updated_user:
                    used_traffic = updated_user.get('usedTrafficBytes', 0)
                    await callback.message.edit_reply_markup(
                        reply_markup=callback.message.reply_markup
                    )
            except:
                pass
        else:
            await callback.answer("❌ Ошибка сброса трафика", show_alert=True)
    
    except Exception as e:
        logger.error(f"Error resetting user traffic: {e}")
        await callback.answer("❌ Ошибка операции", show_alert=True)

@admin_router.callback_query(F.data.startswith("disable_user_"))
async def disable_user_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        user_uuid = callback.data.replace("disable_user_", "")
        
        if not api:
            await callback.answer("❌ API недоступен", show_alert=True)
            return
        
        await callback.answer("🔄 Отключаю пользователя...")
        
        result = await api.disable_user(user_uuid)
        
        if result:
            await callback.answer("✅ Пользователь успешно отключен", show_alert=True)
            log_user_action(user.telegram_id, "disable_user", f"UUID: {user_uuid}")
        else:
            await callback.answer("❌ Ошибка отключения пользователя", show_alert=True)
    
    except Exception as e:
        logger.error(f"Error disabling user: {e}")
        await callback.answer("❌ Ошибка операции", show_alert=True)

@admin_router.callback_query(F.data.startswith("enable_user_"))
async def enable_user_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        user_uuid = callback.data.replace("enable_user_", "")
        
        if not api:
            await callback.answer("❌ API недоступен", show_alert=True)
            return
        
        await callback.answer("🔄 Включаю пользователя...")
        
        result = await api.enable_user(user_uuid)
        
        if result:
            await callback.answer("✅ Пользователь успешно включен", show_alert=True)
            log_user_action(user.telegram_id, "enable_user", f"UUID: {user_uuid}")
        else:
            await callback.answer("❌ Ошибка включения пользователя", show_alert=True)
    
    except Exception as e:
        logger.error(f"Error enabling user: {e}")
        await callback.answer("❌ Ошибка операции", show_alert=True)

@admin_router.callback_query(F.data == "sync_status_remnawave")
async def sync_status_remnawave_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, db: Database = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not api or not db:
        await callback.answer("❌ API или база данных недоступны", show_alert=True)
        return
    
    try:
        await callback.answer("📊 Проверяю статус синхронизации...")
        
        remna_users = await api.get_all_system_users_full()
        bot_users = await db.get_all_users()
        
        remna_with_tg = len([u for u in remna_users if u.get('telegramId')])
        remna_without_tg = len(remna_users) - remna_with_tg
        
        bot_with_uuid = len([u for u in bot_users if u.remnawave_uuid])
        bot_without_uuid = len(bot_users) - bot_with_uuid
        
        total_bot_subs = 0
        synced_subs = 0
        
        for bot_user in bot_users:
            user_subs = await db.get_user_subscriptions(bot_user.telegram_id)
            total_bot_subs += len(user_subs)
            
            for user_sub in user_subs:
                for remna_user in remna_users:
                    if remna_user.get('shortUuid') == user_sub.short_uuid:
                        synced_subs += 1
                        break
        
        text = "📊 **Статус синхронизации**\n\n"
        
        text += "RemnaWave:\n"
        text += f"• Всего пользователей: {len(remna_users)}\n"
        text += f"• С Telegram ID: {remna_with_tg}\n"
        text += f"• Без Telegram ID: {remna_without_tg}\n\n"
        
        text += "Бот:\n"
        text += f"• Всего пользователей: {len(bot_users)}\n"
        text += f"• С RemnaWave UUID: {bot_with_uuid}\n"
        text += f"• Без RemnaWave UUID: {bot_without_uuid}\n\n"
        
        text += "Подписки:\n"
        text += f"• Всего в боте: {total_bot_subs}\n"
        text += f"• Синхронизировано: {synced_subs}\n"
        text += f"• Не синхронизировано: {total_bot_subs - synced_subs}\n\n"
        
        if bot_without_uuid > 0 or remna_without_tg > 0 or (total_bot_subs - synced_subs) > 0:
            text += "⚠️ Рекомендации:\n"
            if bot_without_uuid > 0:
                text += f"• {bot_without_uuid} пользователей бота не связаны с RemnaWave\n"
            if remna_without_tg > 0:
                text += f"• {remna_without_tg} пользователей RemnaWave не имеют Telegram ID\n"
            if (total_bot_subs - synced_subs) > 0:
                text += f"• {total_bot_subs - synced_subs} подписок не синхронизированы\n"
            text += "\n💡 Рекомендуется выполнить полную синхронизацию\n"
        else:
            text += "✅ Все данные синхронизированы\n"
        
        text += f"\n🕐 _Проверено: {format_datetime(datetime.now(), user.language)}_"
        
        await callback.message.edit_text(
            text,
            reply_markup=sync_remnawave_keyboard(user.language)
        )
        
    except Exception as e:
        logger.error(f"Error getting sync status: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка получения статуса\n\n{str(e)[:200]}",
            reply_markup=sync_remnawave_keyboard(user.language)
        )

@admin_router.callback_query(F.data == "filter_users_active")
async def filter_users_active_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not api:
        await callback.answer("❌ API недоступен", show_alert=True)
        return
    
    try:
        await callback.answer("🔍 Фильтрую активных пользователей...")
        
        all_users = await api.get_all_system_users_full()
        active_users = [u for u in all_users if u.get('status') == 'ACTIVE']
        
        if not active_users:
            await callback.message.edit_text(
                "❌ Активные пользователи не найдены",
                reply_markup=system_users_keyboard(user.language)
            )
            return
        
        text = f"✅ Активные пользователи ({len(active_users)})\n\n"
        
        for i, sys_user in enumerate(active_users[:10], 1):
            username = sys_user.get('username', 'N/A')
            username = username.replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
            
            telegram_id = sys_user.get('telegramId', 'N/A')
            short_uuid = sys_user.get('shortUuid', '')[:8] + "..."
            
            text += f"{i}. {username}\n"
            if telegram_id != 'N/A':
                text += f"   📱 TG: {telegram_id}\n"
            text += f"   🔗 {short_uuid}\n"
            
            if sys_user.get('expireAt'):
                expire_date = sys_user['expireAt'][:10]
                text += f"   ⏰ До {expire_date}\n"
            text += "\n"
        
        if len(active_users) > 10:
            text += f"... и еще {len(active_users) - 10} активных пользователей"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Сбросить фильтр", callback_data="list_all_system_users")],
            [InlineKeyboardButton(text="📱 С Telegram", callback_data="filter_users_telegram")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="system_users")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error filtering active users: {e}")
        await callback.answer("❌ Ошибка фильтрации", show_alert=True)

@admin_router.callback_query(F.data == "filter_users_telegram")
async def filter_users_telegram_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not api:
        await callback.answer("❌ API недоступен", show_alert=True)
        return
    
    try:
        await callback.answer("🔍 Фильтрую пользователей с Telegram...")
        
        all_users = await api.get_all_system_users_full()
        tg_users = [u for u in all_users if u.get('telegramId')]
        
        if not tg_users:
            await callback.message.edit_text(
                "❌ Пользователи с Telegram ID не найдены",
                reply_markup=system_users_keyboard(user.language)
            )
            return
        
        text = f"📱 **Пользователи с Telegram ID** ({len(tg_users)})\n\n"
        
        for i, sys_user in enumerate(tg_users[:10], 1):
            username = sys_user.get('username', 'N/A')
            telegram_id = sys_user.get('telegramId')
            status = sys_user.get('status', 'UNKNOWN')
            status_emoji = "🟢" if status == 'ACTIVE' else "🔴"
            
            text += f"{i}. {status_emoji} **{username}**\n"
            text += f"   📱 TG: `{telegram_id}`\n"
            
            if sys_user.get('shortUuid'):
                text += f"   🔗 {sys_user['shortUuid'][:8]}...\n"
            
            if sys_user.get('expireAt'):
                expire_date = sys_user['expireAt'][:10]
                text += f"   ⏰ До {expire_date}\n"
            text += "\n"
        
        if len(tg_users) > 10:
            text += f"_... и еще {len(tg_users) - 10} пользователей с Telegram_"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Сбросить фильтр", callback_data="list_all_system_users")],
            [InlineKeyboardButton(text="✅ Только активные", callback_data="filter_users_active")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="system_users")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error filtering telegram users: {e}")
        await callback.answer("❌ Ошибка фильтрации", show_alert=True)

@admin_router.callback_query(F.data == "show_all_nodes")
async def show_all_nodes_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, state: FSMContext = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not api:
        await callback.answer("❌ API недоступен", show_alert=True)
        return
    
    if state:
        await state.clear()
        await state.update_data(nodes_page=0)
    
    await show_nodes_paginated(callback, user, api, state, page=0)

async def show_nodes_paginated(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, 
                               state: FSMContext = None, page: int = 0):
    try:
        nodes = await api.get_all_nodes()
        if not nodes:
            await callback.message.edit_text(
                "❌ Ноды не найдены",
                reply_markup=admin_system_keyboard(user.language)
            )
            return
        
        nodes.sort(key=lambda x: (
            0 if x.get('status') == 'online' else 1,
            x.get('name', '')
        ))
        
        nodes_per_page = 10
        total_pages = (len(nodes) + nodes_per_page - 1) // nodes_per_page
        start_idx = page * nodes_per_page
        end_idx = min(start_idx + nodes_per_page, len(nodes))
        page_nodes = nodes[start_idx:end_idx]
        
        text = f"🖥 **Все ноды системы**\n"
        text += f"📄 Страница {page + 1} из {total_pages}\n\n"
        
        for i, node in enumerate(page_nodes, start=start_idx + 1):
            status = node.get('status', 'unknown')
            status_emoji = {
                'online': '🟢',
                'offline': '🔴',
                'disabled': '⚫',
                'disconnected': '🔴',
                'xray_stopped': '🟡'
            }.get(status, '⚪')
            
            name = node.get('name', f'Node-{i}')
            text += f"{i}. {status_emoji} **{name}**\n"
            
            if node.get('address'):
                text += f"   📍 {node['address'][:30]}...\n"
            
            if node.get('cpuUsage') or node.get('memUsage'):
                text += f"   💻 CPU: {node.get('cpuUsage', 0):.0f}% | RAM: {node.get('memUsage', 0):.0f}%\n"
            
            if node.get('usersCount'):
                text += f"   👥 Пользователей: {node['usersCount']}\n"
            
            text += "\n"
        
        buttons = []
        
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"nodes_page_{page - 1}"))
            nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"nodes_page_{page + 1}"))
            buttons.append(nav_row)
        
        buttons.append([
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_nodes_page_{page}")
        ])
        
        buttons.append([
            InlineKeyboardButton(text="🔙 Назад", callback_data="nodes_management")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error showing nodes page: {e}")
        await callback.answer("❌ Ошибка отображения", show_alert=True)

@admin_router.callback_query(F.data.startswith("nodes_page_"))
async def nodes_page_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, state: FSMContext = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        page = int(callback.data.split("_")[-1])
        await show_nodes_paginated(callback, user, api, state, page)
    except Exception as e:
        logger.error(f"Error in nodes pagination: {e}")
        await callback.answer("❌ Ошибка навигации", show_alert=True)

@admin_router.callback_query(F.data == "sync_full_remnawave")
async def sync_full_remnawave_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, db: Database = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not api or not db:
        await callback.answer("❌ API или база данных недоступны", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Запускаю полную синхронизацию...")
        
        progress_msg = await callback.message.edit_text(
            "⏳ Полная синхронизация RemnaWave\n\n"
            "Этап 1/5: Получение данных..."
        )
        
        remna_users = await api.get_all_system_users_full()
        users_with_tg = [u for u in remna_users if u.get('telegramId')]
        
        logger.info(f"Starting full sync for {len(users_with_tg)} users with Telegram ID")
        
        users_created = 0
        users_updated = 0
        subs_created = 0
        subs_updated = 0
        plans_created = 0
        statuses_updated = 0
        errors = 0
        
        await progress_msg.edit_text(
            "⏳ Полная синхронизация RemnaWave\n\n"
            "Этап 1/5: Синхронизация пользователей..."
        )
        
        for remna_user in users_with_tg:
            try:
                telegram_id = remna_user['telegramId']
                bot_user = await db.get_user_by_telegram_id(telegram_id)
                
                if not bot_user:
                    is_admin = telegram_id in (kwargs.get('config', {}).ADMIN_IDS if 'config' in kwargs else [])
                    bot_user = await db.create_user(
                        telegram_id=telegram_id,
                        username=remna_user.get('username'),
                        first_name=remna_user.get('username'),
                        language='ru',
                        is_admin=is_admin
                    )
                    users_created += 1
                    logger.info(f"Created user {telegram_id}")
                
                if not bot_user.remnawave_uuid and remna_user.get('uuid'):
                    bot_user.remnawave_uuid = remna_user['uuid']
                    await db.update_user(bot_user)
                    users_updated += 1
                
            except Exception as e:
                logger.error(f"Error syncing user {telegram_id}: {e}")
                errors += 1
        
        await progress_msg.edit_text(
            "⏳ Полная синхронизация RemnaWave\n\n"
            "Этап 2/5: Создание планов подписок..."
        )
        
        unique_squads = set()
        for remna_user in users_with_tg:
            active_squads = remna_user.get('activeInternalSquads', [])
            internal_squads = remna_user.get('internalSquads', [])
            
            for squad_list in [active_squads, internal_squads]:
                for squad in squad_list:
                    if isinstance(squad, dict):
                        squad_uuid = squad.get('uuid') or squad.get('id')
                    else:
                        squad_uuid = str(squad)
                    
                    if squad_uuid:
                        unique_squads.add(squad_uuid)
        
        logger.info(f"Found {len(unique_squads)} unique squads")
        
        existing_plans = await db.get_all_subscriptions(include_inactive=True, exclude_trial=False)
        existing_squad_uuids = {plan.squad_uuid for plan in existing_plans if plan.squad_uuid}
        
        for squad_uuid in unique_squads:
            if squad_uuid not in existing_squad_uuids:
                try:
                    plan_name = f"Auto_Squad_{squad_uuid[:8]}"
                    new_plan = await db.create_subscription(
                        name=plan_name,
                        description=f"Автоматически созданный план для squad {squad_uuid}",
                        price=0,
                        duration_days=30,
                        traffic_limit_gb=0,
                        squad_uuid=squad_uuid
                    )
                    plans_created += 1
                    logger.info(f"Created subscription plan for squad {squad_uuid}")
                except Exception as e:
                    logger.error(f"Error creating plan for squad {squad_uuid}: {e}")
                    errors += 1
        
        await progress_msg.edit_text(
            "⏳ Полная синхронизация RemnaWave\n\n"
            "Этап 3/5: Синхронизация подписок..."
        )
        
        for remna_user in users_with_tg:
            try:
                telegram_id = remna_user['telegramId']
                short_uuid = remna_user.get('shortUuid')
                
                if not short_uuid:
                    continue
                
                existing_sub = await db.get_user_subscription_by_short_uuid(telegram_id, short_uuid)
                
                if existing_sub:
                    if remna_user.get('expireAt'):
                        try:
                            expire_dt = datetime.fromisoformat(remna_user['expireAt'].replace('Z', '+00:00'))
                            existing_sub.expires_at = expire_dt.replace(tzinfo=None)
                        except:
                            pass
                    
                    existing_sub.is_active = remna_user.get('status') == 'ACTIVE'
                    await db.update_user_subscription(existing_sub)
                    subs_updated += 1
                    
                else:
                    if remna_user.get('status') == 'ACTIVE' or remna_user.get('expireAt'):
                        squad_uuid = None
                        active_squads = remna_user.get('activeInternalSquads', [])
                        
                        if active_squads:
                            first_squad = active_squads[0]
                            if isinstance(first_squad, dict):
                                squad_uuid = first_squad.get('uuid') or first_squad.get('id')
                            else:
                                squad_uuid = str(first_squad)
                        
                        subscription_plan = None
                        all_plans = await db.get_all_subscriptions(include_inactive=True, exclude_trial=False)
                        
                        for plan in all_plans:
                            if plan.squad_uuid == squad_uuid:
                                subscription_plan = plan
                                break
                        
                        if subscription_plan:
                            expire_dt = None
                            if remna_user.get('expireAt'):
                                try:
                                    expire_dt = datetime.fromisoformat(remna_user['expireAt'].replace('Z', '+00:00'))
                                    expire_dt = expire_dt.replace(tzinfo=None)
                                except:
                                    expire_dt = datetime.now() + timedelta(days=30)
                            else:
                                expire_dt = datetime.now() + timedelta(days=30)
                            
                            user_sub = await db.create_user_subscription(
                                user_id=telegram_id,
                                subscription_id=subscription_plan.id,
                                short_uuid=short_uuid,
                                expires_at=expire_dt,
                                is_active=remna_user.get('status') == 'ACTIVE'
                            )
                            
                            if user_sub:
                                subs_created += 1
                                logger.info(f"Created subscription for user {telegram_id}")
                
            except Exception as e:
                logger.error(f"Error syncing subscription for user {telegram_id}: {e}")
                errors += 1
        
        await progress_msg.edit_text(
            "⏳ Полная синхронизация RemnaWave\n\n"
            "Этап 4/5: Обновление статусов..."
        )
        
        all_bot_users = await db.get_all_users()
        for bot_user in all_bot_users:
            user_subs = await db.get_user_subscriptions(bot_user.telegram_id)
            
            for user_sub in user_subs:
                if user_sub.expires_at < datetime.now() and user_sub.is_active:
                    user_sub.is_active = False
                    await db.update_user_subscription(user_sub)
                    statuses_updated += 1
                    
                    if bot_user.remnawave_uuid:
                        try:
                            await api.update_user(bot_user.remnawave_uuid, {'status': 'EXPIRED'})
                        except:
                            pass
        
        await progress_msg.edit_text(
            "⏳ Полная синхронизация RemnaWave\n\n"
            "Этап 5/5: Подсчет результатов..."
        )
        
        total_bot_users = len(await db.get_all_users())
        total_subscriptions = 0
        active_subscriptions = 0
        
        for bot_user in all_bot_users:
            user_subs = await db.get_user_subscriptions(bot_user.telegram_id)
            total_subscriptions += len(user_subs)
            active_subscriptions += len([s for s in user_subs if s.is_active])
        
        result_text = (
            "✅ Полная синхронизация завершена!\n\n"
            "📊 Результаты операции:\n\n"
            "👥 Пользователи:\n"
            f"• Создано: {users_created}\n"
            f"• Обновлено: {users_updated}\n\n"
            "📋 Планы подписок:\n"
            f"• Создано новых планов: {plans_created}\n\n"
            "🎫 Подписки:\n"
            f"• Создано: {subs_created}\n"
            f"• Обновлено: {subs_updated}\n\n"
            "🔄 Статусы:\n"
            f"• Обновлено: {statuses_updated}\n"
            f"• Ошибок: {errors}\n\n"
            "📈 Текущее состояние:\n"
            f"• Пользователей в боте: {total_bot_users}\n"
            f"• Всего подписок: {total_subscriptions}\n"
            f"• Активных подписок: {active_subscriptions}\n\n"
            f"🕐 Завершено: {format_datetime(datetime.now(), user.language)}"
        )
        
        await progress_msg.edit_text(
            result_text,
            reply_markup=sync_remnawave_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "full_sync_improved_completed", 
                       f"Users: {users_created}/{users_updated}, Subs: {subs_created}/{subs_updated}, Plans: {plans_created}")
        
    except Exception as e:
        logger.error(f"Error in improved full sync: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка полной синхронизации\n\nДетали: {str(e)[:200]}",
            reply_markup=sync_remnawave_keyboard(user.language)
        )

@admin_router.callback_query(F.data == "sync_single_user")
async def sync_single_user_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "👤 Синхронизация конкретного пользователя\n\n"
        "Введите Telegram ID пользователя для синхронизации:",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_sync_single_user)

@admin_router.message(StateFilter(BotStates.admin_sync_single_user))
async def handle_sync_single_user(message: Message, state: FSMContext, user: User, 
                                 api: RemnaWaveAPI = None, db: Database = None, **kwargs):
    if not api or not db:
        await message.answer("❌ API или база данных недоступны")
        await state.clear()
        return
    
    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный формат Telegram ID")
        return
    
    try:
        progress_msg = await message.answer("🔄 Синхронизирую пользователя...")
        
        remna_user_result = await api.get_user_by_telegram_id(telegram_id)
        
        logger.info(f"API result type: {type(remna_user_result)}")
        logger.info(f"API result: {remna_user_result}")
        
        remna_user = None
        
        if isinstance(remna_user_result, dict):
            remna_user = remna_user_result
        elif isinstance(remna_user_result, list):
            if remna_user_result:
                remna_user = remna_user_result[0]
            else:
                remna_user = None
        else:
            remna_user = None
        
        if not remna_user or not isinstance(remna_user, dict):
            await progress_msg.edit_text(
                f"❌ Пользователь с Telegram ID {telegram_id} не найден в RemnaWave\n\n"
                f"Тип ответа API: {type(remna_user_result)}\n"
                f"Содержимое: {str(remna_user_result)[:100]}...",
                reply_markup=admin_menu_keyboard(user.language)
            )
            await state.clear()
            return
        
        result_details = []
        
        bot_user = await db.get_user_by_telegram_id(telegram_id)
        
        if not bot_user:
            is_admin = telegram_id in (kwargs.get('config', {}).ADMIN_IDS if 'config' in kwargs else [])
            bot_user = await db.create_user(
                telegram_id=telegram_id,
                username=remna_user.get('username'),
                first_name=remna_user.get('username'),
                language='ru',
                is_admin=is_admin
            )
            result_details.append("✅ Создан пользователь в боте")
        else:
            result_details.append("ℹ️ Пользователь уже существует в боте")
        
        if not bot_user.remnawave_uuid and remna_user.get('uuid'):
            bot_user.remnawave_uuid = remna_user['uuid']
            await db.update_user(bot_user)
            result_details.append("✅ Обновлен RemnaWave UUID")
        
        short_uuid = remna_user.get('shortUuid')
        
        if short_uuid:
            existing_sub = await db.get_user_subscription_by_short_uuid(telegram_id, short_uuid)
            
            if existing_sub:
                if remna_user.get('expireAt'):
                    try:
                        expire_str = remna_user['expireAt']
                        if expire_str.endswith('Z'):
                            expire_dt = datetime.fromisoformat(expire_str.replace('Z', '+00:00'))
                        else:
                            expire_dt = datetime.fromisoformat(expire_str)
                        
                        existing_sub.expires_at = expire_dt.replace(tzinfo=None)
                        existing_sub.is_active = remna_user.get('status') == 'ACTIVE'
                        await db.update_user_subscription(existing_sub)
                        result_details.append("✅ Обновлена существующая подписка")
                    except Exception as e:
                        result_details.append(f"❌ Ошибка обновления подписки: {str(e)[:50]}")
                        logger.error(f"Error updating subscription: {e}")
            else:
                if remna_user.get('status') == 'ACTIVE' or remna_user.get('expireAt'):
                    squad_uuid = None
                    
                    active_squads = remna_user.get('activeInternalSquads', [])
                    if active_squads and isinstance(active_squads, list):
                        first_squad = active_squads[0]
                        if isinstance(first_squad, dict):
                            squad_uuid = first_squad.get('uuid') or first_squad.get('id')
                        elif isinstance(first_squad, str):
                            squad_uuid = first_squad
                    
                    if not squad_uuid:
                        internal_squads = remna_user.get('internalSquads', [])
                        if internal_squads and isinstance(internal_squads, list):
                            first_squad = internal_squads[0]
                            if isinstance(first_squad, dict):
                                squad_uuid = first_squad.get('uuid') or first_squad.get('id')
                            elif isinstance(first_squad, str):
                                squad_uuid = first_squad
                    
                    subscription_plan = None
                    if squad_uuid:
                        all_plans = await db.get_all_subscriptions(include_inactive=True, exclude_trial=False)
                        for plan in all_plans:
                            if plan.squad_uuid == squad_uuid:
                                subscription_plan = plan
                                break
                    
                    if not subscription_plan and squad_uuid:
                        traffic_gb = 0
                        if remna_user.get('trafficLimitBytes'):
                            traffic_gb = remna_user['trafficLimitBytes'] // (1024 * 1024 * 1024)
                        
                        subscription_plan = await db.create_subscription(
                            name=f"Auto_{remna_user.get('username', 'User')[:10]}",
                            description=f"Автоматически созданный план для {remna_user.get('username')}",
                            price=0,
                            duration_days=30,
                            traffic_limit_gb=traffic_gb,
                            squad_uuid=squad_uuid
                        )
                        result_details.append("✅ Создан новый план подписки")
                    
                    if subscription_plan:
                        expire_dt = datetime.now() + timedelta(days=30)
                        if remna_user.get('expireAt'):
                            try:
                                expire_str = remna_user['expireAt']
                                if expire_str.endswith('Z'):
                                    expire_dt = datetime.fromisoformat(expire_str.replace('Z', '+00:00'))
                                else:
                                    expire_dt = datetime.fromisoformat(expire_str)
                                expire_dt = expire_dt.replace(tzinfo=None)
                            except Exception as date_error:
                                logger.error(f"Error parsing date {remna_user.get('expireAt')}: {date_error}")
                        
                        user_sub = await db.create_user_subscription(
                            user_id=telegram_id,
                            subscription_id=subscription_plan.id,
                            short_uuid=short_uuid,
                            expires_at=expire_dt,
                            is_active=remna_user.get('status') == 'ACTIVE'
                        )
                        
                        if user_sub:
                            result_details.append("✅ Создана новая подписка")
                        else:
                            result_details.append("❌ Ошибка создания подписки")
                    else:
                        result_details.append(f"❌ Не удалось найти или создать план подписки (squad_uuid: {squad_uuid})")
                else:
                    result_details.append("ℹ️ Пользователь неактивен или нет срока действия")
        else:
            result_details.append("ℹ️ У пользователя нет short_uuid")
        
        status_emoji = "🟢" if remna_user.get('status') == 'ACTIVE' else "🔴"
        username = remna_user.get('username', 'N/A')
        
        report_text = f"👤 Синхронизация пользователя завершена\n\n"
        report_text += f"Пользователь: {status_emoji} {username}\n"
        report_text += f"Telegram ID: {telegram_id}\n"
        report_text += f"Статус в RemnaWave: {remna_user.get('status', 'N/A')}\n"
        report_text += f"UUID: {remna_user.get('uuid', 'N/A')[:20]}...\n"
        report_text += f"Short UUID: {remna_user.get('shortUuid', 'N/A')}\n"
        
        if remna_user.get('expireAt'):
            expire_date = remna_user['expireAt'][:10]
            report_text += f"Действует до: {expire_date}\n"
        
        active_squads = remna_user.get('activeInternalSquads', [])
        if active_squads:
            report_text += f"Активных squad: {len(active_squads)}\n"
        
        report_text += f"\n📋 Выполненные действия:\n"
        for detail in result_details:
            report_text += f"• {detail}\n"
        
        await progress_msg.edit_text(
            report_text,
            reply_markup=admin_menu_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "single_user_synced", f"User: {telegram_id}")
        
    except Exception as e:
        logger.error(f"Error syncing single user: {e}", exc_info=True)
        await message.answer(
            f"❌ Ошибка синхронизации\n\nДетали: {str(e)[:100]}",
            reply_markup=admin_menu_keyboard(user.language)
        )
    
    await state.clear()

@admin_router.callback_query(F.data == "import_all_by_telegram")
async def import_all_by_telegram_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, db: Database = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not api or not db:
        await callback.answer("❌ API или база данных недоступны", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Запускаю массовый импорт всех подписок...")
        
        progress_msg = await callback.message.edit_text(
            "⏳ Массовый импорт подписок по Telegram ID\n\n"
            "Этап 1/5: Получение всех записей из RemnaWave..."
        )
        
        all_remna_records = await api.get_all_system_users_full()
        
        if not all_remna_records:
            await progress_msg.edit_text(
                "❌ Не удалось получить записи из RemnaWave",
                reply_markup=sync_remnawave_keyboard(user.language)
            )
            return
        
        logger.info(f"Got {len(all_remna_records)} total records from RemnaWave")
        
        records_with_telegram = [r for r in all_remna_records if r.get('telegramId')]
        
        logger.info(f"Found {len(records_with_telegram)} records with Telegram ID")
        
        users_by_telegram = {}
        for record in records_with_telegram:
            tg_id = record['telegramId']
            if tg_id not in users_by_telegram:
                users_by_telegram[tg_id] = []
            users_by_telegram[tg_id].append(record)
        
        logger.info(f"Found {len(users_by_telegram)} unique Telegram users with {len(records_with_telegram)} total subscriptions")
        
        bot_users_created = 0
        bot_users_updated = 0
        plans_created = 0
        subscriptions_imported = 0
        subscriptions_updated = 0
        errors = 0
        skipped_no_shortuid = 0
        
        await progress_msg.edit_text(
            "⏳ Массовый импорт подписок по Telegram ID\n\n"
            "Этап 1/5: Создание пользователей бота..."
        )
        
        for telegram_id, user_records in users_by_telegram.items():
            try:
                logger.info(f"Processing Telegram user {telegram_id} with {len(user_records)} subscriptions")
                
                latest_record = max(user_records, key=lambda x: x.get('updatedAt', x.get('createdAt', '')))
                
                bot_user = await db.get_user_by_telegram_id(telegram_id)
                
                if not bot_user:
                    is_admin = telegram_id in (kwargs.get('config', {}).ADMIN_IDS if 'config' in kwargs else [])
                    
                    best_username = None
                    for record in user_records:
                        username = record.get('username', '')
                        if username and not username.startswith('user_'):
                            best_username = username
                            break
                    
                    if not best_username:
                        best_username = latest_record.get('username', f"User_{telegram_id}")
                    
                    bot_user = await db.create_user(
                        telegram_id=telegram_id,
                        username=best_username,
                        first_name=best_username,
                        language='ru',
                        is_admin=is_admin
                    )
                    bot_users_created += 1
                    logger.info(f"Created bot user for TG {telegram_id} with username {best_username}")
                
                if latest_record.get('uuid') and bot_user.remnawave_uuid != latest_record['uuid']:
                    bot_user.remnawave_uuid = latest_record['uuid']
                    await db.update_user(bot_user)
                    bot_users_updated += 1
                
            except Exception as e:
                logger.error(f"Error processing Telegram user {telegram_id}: {e}")
                errors += 1
        
        await progress_msg.edit_text(
            "⏳ Массовый импорт подписок по Telegram ID\n\n"
            "Этап 2/5: Анализ squad'ов..."
        )
        
        all_squads = set()
        squad_names = {}
        
        for i, record in enumerate(records_with_telegram):
            logger.debug(f"Analyzing record {i+1}/{len(records_with_telegram)}: {record.get('username')}")
            
            active_squads = record.get('activeInternalSquads', [])
            if active_squads and isinstance(active_squads, list):
                for squad in active_squads:
                    if isinstance(squad, dict):
                        squad_uuid = squad.get('uuid')
                        squad_name = squad.get('name', 'Unknown Squad')
                        if squad_uuid:
                            all_squads.add(squad_uuid)
                            squad_names[squad_uuid] = squad_name
                            logger.debug(f"Found squad: {squad_uuid} ({squad_name})")
        
        logger.info(f"Found {len(all_squads)} unique squad UUIDs: {list(all_squads)}")
        
        await progress_msg.edit_text(
            "⏳ Массовый импорт подписок по Telegram ID\n\n"
            "Этап 3/5: Создание планов подписок..."
        )
        
        existing_plans = await db.get_all_subscriptions_admin()
        existing_squad_uuids = {plan.squad_uuid for plan in existing_plans if plan.squad_uuid}
        
        logger.info(f"Existing squad UUIDs in DB: {existing_squad_uuids}")
        
        for squad_uuid in all_squads:
            if squad_uuid not in existing_squad_uuids:
                try:
                    squad_name = squad_names.get(squad_uuid, "Unknown Squad")
                    plan_name = f"Import_{squad_name[:15]}_{squad_uuid[:8]}"
                    
                    logger.info(f"Creating plan for squad {squad_uuid}: {plan_name}")
                    
                    new_plan = await db.create_subscription(
                        name="Старая подписка",
                        description=f"Импортированная подписка из RemnaWave (squad: {squad_name})",
                        price=0,
                        duration_days=30,
                        traffic_limit_gb=0,
                        squad_uuid=squad_uuid,
                        is_imported=True
                    )
                    plans_created += 1
                    logger.info(f"✅ Created plan for squad {squad_uuid}: {plan_name}")
                    
                except Exception as e:
                    logger.error(f"❌ Error creating plan for squad {squad_uuid}: {e}")
                    errors += 1
            else:
                logger.info(f"Plan for squad {squad_uuid} already exists")
        
        await progress_msg.edit_text(
            "⏳ Массовый импорт подписок по Telegram ID\n\n"
            "Этап 4/5: Импорт подписок..."
        )
        
        for i, record in enumerate(records_with_telegram):
            try:
                telegram_id = record['telegramId']
                short_uuid = record.get('shortUuid')
                status = record.get('status', 'UNKNOWN')
                expire_at = record.get('expireAt')
                username = record.get('username')
                
                logger.info(f"=== IMPORTING SUBSCRIPTION {i+1}/{len(records_with_telegram)} ===")
                logger.info(f"TG={telegram_id}, Username={username}, shortUuid={short_uuid}, status={status}")
                
                if not short_uuid:
                    skipped_no_shortuid += 1
                    logger.warning(f"❌ Skipping record: no shortUuid")
                    continue
                
                existing_sub = await db.get_user_subscription_by_short_uuid(telegram_id, short_uuid)
                
                if existing_sub:
                    existing_plan = await db.get_subscription_by_id(existing_sub.subscription_id)
                    
                    if existing_plan:
                        logger.info(f"Updating existing subscription for TG {telegram_id}, shortUuid {short_uuid}")
                        
                        if expire_at:
                            try:
                                if expire_at.endswith('Z'):
                                    expire_dt = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
                                else:
                                    expire_dt = datetime.fromisoformat(expire_at)
                                existing_sub.expires_at = expire_dt.replace(tzinfo=None)
                            except Exception as date_error:
                                logger.error(f"Error parsing date: {date_error}")
                        
                        existing_sub.is_active = (status == 'ACTIVE')
                        
                        if record.get('trafficLimitBytes') is not None:
                            traffic_gb = record['trafficLimitBytes'] // (1024 * 1024 * 1024) if record['trafficLimitBytes'] > 0 else 0
                            existing_sub.traffic_limit_gb = traffic_gb
                        
                        await db.update_user_subscription(existing_sub)
                        subscriptions_updated += 1
                    else:
                        logger.warning(f"Found orphaned subscription {existing_sub.id} for user {telegram_id}, deleting...")
                        await db.delete_user_subscription(existing_sub.id)
                        
                        logger.info(f"Creating new subscription after cleaning orphaned one")
                        existing_sub = None 
                
                if not existing_sub:
                    logger.info(f"Creating new subscription for TG {telegram_id}, shortUuid {short_uuid}")
                    
                    squad_uuid = None
                    active_squads = record.get('activeInternalSquads', [])
                    
                    if active_squads and isinstance(active_squads, list) and len(active_squads) > 0:
                        first_squad = active_squads[0]
                        if isinstance(first_squad, dict):
                            squad_uuid = first_squad.get('uuid')
                            logger.info(f"Extracted squad_uuid: {squad_uuid}")
                    
                    if not squad_uuid:
                        logger.warning(f"❌ No squad_uuid found for record {username}")
                        errors += 1
                        continue
                    
                    all_plans = await db.get_all_subscriptions_admin()
                    subscription_plan = None
                    
                    for plan in all_plans:
                        if plan.squad_uuid == squad_uuid:
                            subscription_plan = plan
                            logger.info(f"✅ Found matching plan: {plan.name}")
                            break
                    
                    if not subscription_plan:
                        logger.error(f"❌ No subscription plan found for squad {squad_uuid}")
                        errors += 1
                        continue
                    
                    expire_dt_naive = datetime.now() + timedelta(days=30)  
                    if expire_at:
                        try:
                            if expire_at.endswith('Z'):
                                expire_dt = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
                            else:
                                expire_dt = datetime.fromisoformat(expire_at)
                            expire_dt_naive = expire_dt.replace(tzinfo=None)
                        except Exception as date_error:
                            logger.error(f"Error parsing expiry date: {date_error}")
                    
                    traffic_gb = 0
                    if record.get('trafficLimitBytes'):
                        traffic_gb = record['trafficLimitBytes'] // (1024 * 1024 * 1024)
                    
                    user_subscription = await db.create_user_subscription(
                        user_id=telegram_id,
                        subscription_id=subscription_plan.id,
                        short_uuid=short_uuid,
                        expires_at=expire_dt_naive,
                        is_active=(status == 'ACTIVE'),
                        traffic_limit_gb=traffic_gb
                    )
                    
                    if user_subscription:
                        subscriptions_imported += 1
                        logger.info(f"✅ Successfully imported subscription: TG={telegram_id}, shortUuid={short_uuid}")
                    else:
                        logger.error(f"❌ Failed to create subscription for TG {telegram_id}")
                        errors += 1
                
            except Exception as e:
                logger.error(f"❌ Error importing subscription for record {i+1}: {e}")
                errors += 1
        
        await progress_msg.edit_text(
            "⏳ Массовый импорт подписок по Telegram ID\n\n"
            "Этап 5/5: Подсчет результатов..."
        )
        
        final_bot_users = len(await db.get_all_users())
        final_subscriptions = 0
        final_active_subs = 0
        
        all_bot_users = await db.get_all_users()
        for bot_user in all_bot_users:
            user_subs = await db.get_user_subscriptions(bot_user.telegram_id)
            final_subscriptions += len(user_subs)
            final_active_subs += len([s for s in user_subs if s.is_active])
        
        result_text = (
            "✅ Массовый импорт подписок завершен!\n\n"
            "📊 Результаты импорта:\n\n"
            "👥 Пользователи Telegram:\n"
            f"• Уникальных пользователей: {len(users_by_telegram)}\n"
            f"• Создано в боте: {bot_users_created}\n"
            f"• Обновлено UUID: {bot_users_updated}\n\n"
            "📋 Планы подписок:\n"
            f"• Создано новых планов: {plans_created}\n\n"
            "🎫 Подписки:\n"
            f"• Всего записей обработано: {len(records_with_telegram)}\n"
            f"• Импортировано новых: {subscriptions_imported}\n"
            f"• Обновлено существующих: {subscriptions_updated}\n"
            f"• Пропущено (нет shortUuid): {skipped_no_shortuid}\n"
            f"• Ошибок: {errors}\n\n"
            "📈 Итоговая статистика бота:\n"
            f"• Пользователей в боте: {final_bot_users}\n"
            f"• Всего подписок: {final_subscriptions}\n"
            f"• Активных подписок: {final_active_subs}\n\n"
            f"🕐 Завершено: {format_datetime(datetime.now(), user.language)}"
        )
        
        await progress_msg.edit_text(
            result_text,
            reply_markup=sync_remnawave_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "bulk_import_completed", 
                       f"Records: {len(records_with_telegram)}, Imported: {subscriptions_imported}, Updated: {subscriptions_updated}")
        
    except Exception as e:
        logger.error(f"Error in bulk import: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка массового импорта\n\nДетали: {str(e)[:200]}",
            reply_markup=sync_remnawave_keyboard(user.language)
        )

@admin_router.message(StateFilter(BotStates.admin_debug_user_structure))
async def handle_debug_user_structure(message: Message, state: FSMContext, user: User, api: RemnaWaveAPI = None, **kwargs):
    if not api:
        await message.answer("❌ API недоступен")
        await state.clear()
        return
    
    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный формат Telegram ID")
        return
    
    try:
        remna_user = await api.get_user_by_telegram_id(telegram_id)
        
        if not remna_user:
            await message.answer(
                f"❌ Пользователь с Telegram ID {telegram_id} не найден",
                reply_markup=admin_menu_keyboard(user.language)
            )
            await state.clear()
            return
        
        analysis = f"🔍 Структура пользователя {telegram_id}\n\n"
        
        analysis += "📋 Основные поля:\n"
        for key in ['uuid', 'username', 'shortUuid', 'status', 'expireAt', 'telegramId']:
            value = remna_user.get(key, 'N/A')
            analysis += f"• {key}: {value}\n"
        
        analysis += "\n"
        
        analysis += "🏷 Squad поля:\n"
        squad_fields = ['activeInternalSquads', 'internalSquads', 'squads', 'squad', 'squadUuid', 'squadId']
        
        for field in squad_fields:
            if field in remna_user:
                value = remna_user[field]
                analysis += f"• {field}: {value}\n"
                
                if isinstance(value, list) and value:
                    for i, item in enumerate(value):
                        analysis += f"  [{i}]: {item}\n"
                        if isinstance(item, dict):
                            for sub_key, sub_value in item.items():
                                analysis += f"    {sub_key}: {sub_value}\n"
            else:
                analysis += f"• {field}: ОТСУТСТВУЕТ\n"
        
        analysis += "\n"
        
        analysis += "📝 Все поля пользователя:\n"
        for key, value in remna_user.items():
            if key not in ['uuid', 'username', 'shortUuid', 'status', 'expireAt', 'telegramId'] + squad_fields:
                if isinstance(value, str) and len(value) > 50:
                    value = value[:47] + "..."
                analysis += f"• {key}: {value}\n"
        
        if len(analysis) > 4000:
            parts = [analysis[i:i+4000] for i in range(0, len(analysis), 4000)]
            for i, part in enumerate(parts):
                if i == 0:
                    await message.answer(part)
                else:
                    await message.answer(f"Часть {i+1}:\n{part}")
        else:
            await message.answer(analysis)
        
        await message.answer(
            "✅ Анализ завершен",
            reply_markup=admin_menu_keyboard(user.language)
        )
        
    except Exception as e:
        logger.error(f"Error debugging user structure: {e}")
        await message.answer(
            f"❌ Ошибка анализа: {str(e)[:100]}",
            reply_markup=admin_menu_keyboard(user.language)
        )
    
    await state.clear()

@admin_router.callback_query(F.data == "rename_imported_plans")
async def rename_imported_plans_callback(callback: CallbackQuery, user: User, db: Database = None, state: FSMContext = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Переименовываю импортированные планы...")
        
        progress_msg = await callback.message.edit_text(
            "⏳ Поиск и переименование импортированных планов..."
        )
        
        all_plans = await db.get_all_subscriptions_admin()
        
        imported_plans = []
        
        for plan in all_plans:
            if plan.name == "Старая подписка":
                continue
            
            if getattr(plan, 'is_trial', False):
                logger.debug(f"Skipping trial plan: {plan.name}")
                continue
            
            is_imported_plan = False
            
            if getattr(plan, 'is_imported', False):
                is_imported_plan = True
                logger.debug(f"Plan {plan.name} marked as imported")
            
            elif plan.name.startswith(('Import_', 'Auto_', 'Imported_')):
                is_imported_plan = True
                logger.debug(f"Plan {plan.name} has import prefix")
            
            elif plan.name.startswith('Trial_') and not getattr(plan, 'is_trial', False):
                is_imported_plan = True
                logger.debug(f"Plan {plan.name} looks like imported trial")
            
            elif (plan.price == 0 and 
                  any(keyword in plan.name.lower() for keyword in ['user_', 'default', 'squad']) and
                  not getattr(plan, 'is_trial', False)):
                is_imported_plan = True
                logger.debug(f"Plan {plan.name} has suspicious import characteristics")
            
            elif (plan.description and 
                  'squad' in plan.description.lower() and 
                  not getattr(plan, 'is_trial', False)):
                is_imported_plan = True
                logger.debug(f"Plan {plan.name} has squad in description")
            
            if is_imported_plan:
                imported_plans.append(plan)
                logger.info(f"Found imported plan: {plan.name} (is_trial: {getattr(plan, 'is_trial', False)})")
        
        logger.info(f"Found {len(imported_plans)} plans that look imported")
        
        if not imported_plans:
            await progress_msg.edit_text(
                "ℹ️ Планы для переименования не найдены\n\n"
                "Все импортированные планы уже имеют название 'Старая подписка'",
                reply_markup=sync_remnawave_keyboard(user.language)
            )
            return
        
        plans_list = []
        for plan in imported_plans[:10]:
            squad_short = plan.squad_uuid[:8] + "..." if plan.squad_uuid else "No Squad"
            plans_list.append(f"• {plan.name} ({squad_short})")
        
        if len(imported_plans) > 10:
            plans_list.append(f"... и еще {len(imported_plans) - 10}")
        
        confirmation_text = (
            f"🔍 Найдено {len(imported_plans)} планов для переименования:\n\n" +
            "\n".join(plans_list) +
            f"\n\n⚠️ Все эти планы будут переименованы в 'Старая подписка'"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Переименовать", callback_data="confirm_rename_plans"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="view_imported_plans")
            ]
        ])
        
        if state:
            plan_ids = [plan.id for plan in imported_plans]
            await state.update_data(plans_to_rename=plan_ids)
            await state.set_state(BotStates.admin_rename_plans_confirm)
        
        await progress_msg.edit_text(confirmation_text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error finding imported plans: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка поиска планов\n\n{str(e)[:200]}",
            reply_markup=sync_remnawave_keyboard(user.language)
        )

@admin_router.callback_query(F.data == "confirm_rename_plans", StateFilter(BotStates.admin_rename_plans_confirm))
async def confirm_rename_plans_callback(callback: CallbackQuery, user: User, db: Database = None, state: FSMContext = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not db or not state:
        await callback.answer("❌ База данных или состояние недоступны", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Переименовываю планы...")
        
        progress_msg = await callback.message.edit_text("⏳ Переименование планов...")
        
        state_data = await state.get_data()
        plan_ids = state_data.get('plans_to_rename', [])
        
        if not plan_ids:
            await progress_msg.edit_text(
                "❌ Список планов для переименования потерян",
                reply_markup=sync_remnawave_keyboard(user.language)
            )
            await state.clear()
            return
        
        renamed_count = 0
        errors = 0
        renamed_plans = []
        
        for plan_id in plan_ids:
            try:
                plan = await db.get_subscription_by_id(plan_id)
                if not plan:
                    continue
                
                old_name = plan.name
                
                plan.name = "Старая подписка"
                plan.description = f"Импортированная подписка из RemnaWave (было: {old_name})"
                plan.is_imported = True
                
                await db.update_subscription(plan)
                renamed_count += 1
                renamed_plans.append(f"'{old_name}' -> 'Старая подписка'")
                logger.info(f"Renamed plan: '{old_name}' -> 'Старая подписка'")
                
            except Exception as e:
                logger.error(f"Error renaming plan {plan_id}: {e}")
                errors += 1
        
        await state.clear()
        
        result_text = (
            f"✅ Переименование завершено!\n\n"
            f"📊 Результаты:\n"
            f"• Переименовано планов: {renamed_count}\n"
            f"• Ошибок: {errors}\n\n"
            f"🏷 Все планы теперь называются: 'Старая подписка'\n\n"
            f"🕐 Завершено: {format_datetime(datetime.now(), user.language)}"
        )
        
        if renamed_count <= 5 and renamed_plans:
            result_text += f"\n📋 Переименованные планы:\n" + "\n".join(f"• {plan}" for plan in renamed_plans)
        
        await progress_msg.edit_text(
            result_text,
            reply_markup=sync_remnawave_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "renamed_imported_plans", f"Renamed: {renamed_count}")
        
    except Exception as e:
        logger.error(f"Error confirming rename plans: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка переименования планов\n\n{str(e)[:200]}",
            reply_markup=sync_remnawave_keyboard(user.language)
        )
        await state.clear()

@admin_router.callback_query(F.data == "view_imported_plans", StateFilter(BotStates.admin_rename_plans_confirm))
async def cancel_rename_plans(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    """Cancel rename operation"""
    await state.clear()
    await view_imported_plans_callback(callback, user, **kwargs)

@admin_router.callback_query(F.data == "main_menu", StateFilter(BotStates.admin_rename_plans_confirm))
async def cancel_rename_to_main(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    await state.clear()
    await callback.message.edit_text(
        t('main_menu', user.language),
        reply_markup=main_menu_keyboard(user.language, user.is_admin)
    )

@admin_router.callback_query(F.data == "view_imported_plans")
async def view_imported_plans_callback(callback: CallbackQuery, user: User, db: Database = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        all_plans = await db.get_all_subscriptions_admin()
        
        regular_plans = []
        imported_plans = []
        suspicious_plans = []
        
        for plan in all_plans:
            if getattr(plan, 'is_imported', False):
                imported_plans.append(plan)
            elif plan.is_trial:
                continue 
            elif (plan.name.startswith(('Import_', 'Auto_', 'Imported_')) or 
                  (plan.price == 0 and any(keyword in plan.name.lower() for keyword in 
                      ['импорт', 'default', 'squad', 'user_']))):
                suspicious_plans.append(plan)
            else:
                regular_plans.append(plan)
        
        text = f"📋 Анализ планов подписок\n\n"
        
        text += f"🛒 Обычные планы (для покупки): {len(regular_plans)}\n"
        if regular_plans:
            for plan in regular_plans[:3]:
                status = "🟢" if plan.is_active else "🔴"
                text += f"{status} {plan.name} - {plan.price}₽\n"
            if len(regular_plans) > 3:
                text += f"... и еще {len(regular_plans) - 3}\n"
        text += "\n"
        
        text += f"📦 Импортированные планы: {len(imported_plans)}\n"
        if imported_plans:
            for plan in imported_plans[:3]:
                status = "🟢" if plan.is_active else "🔴"
                squad_short = plan.squad_uuid[:8] + "..." if plan.squad_uuid else "No Squad"
                text += f"{status} {plan.name} ({squad_short})\n"
            if len(imported_plans) > 3:
                text += f"... и еще {len(imported_plans) - 3}\n"
        text += "\n"
        
        if suspicious_plans:
            text += f"⚠️ Возможно импортированные: {len(suspicious_plans)}\n"
            for plan in suspicious_plans[:3]:
                status = "🟢" if plan.is_active else "🔴"
                squad_short = plan.squad_uuid[:8] + "..." if plan.squad_uuid else "No Squad"
                text += f"{status} {plan.name} ({squad_short})\n"
            if len(suspicious_plans) > 3:
                text += f"... и еще {len(suspicious_plans) - 3}\n"
            text += "\n"
        
        text += f"📊 Итого:\n"
        text += f"• Всего планов: {len(all_plans)}\n"
        text += f"• Обычных: {len(regular_plans)}\n"
        text += f"• Импортированных: {len(imported_plans)}\n"
        if suspicious_plans:
            text += f"• Нужно проверить: {len(suspicious_plans)}\n"
        
        buttons = []
        
        if suspicious_plans or any(plan.name != "Старая подписка" for plan in imported_plans):
            buttons.append([InlineKeyboardButton(text="🏷 Переименовать импортированные", callback_data="rename_imported_plans")])
        
        if imported_plans or suspicious_plans:
            buttons.append([InlineKeyboardButton(text="🗑 Удалить импортированные", callback_data="delete_imported_plans")])
        
        buttons.extend([
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="view_imported_plans")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="sync_remnawave")]
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error viewing imported plans: {e}")
        await callback.answer("❌ Ошибка загрузки планов", show_alert=True)

@admin_router.callback_query(F.data == "delete_imported_plans")
async def delete_imported_plans_callback(callback: CallbackQuery, user: User, db: Database = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить ВСЕ", callback_data="confirm_delete_imported"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="view_imported_plans")
        ]
    ])
    
    await callback.message.edit_text(
        "⚠️ ВНИМАНИЕ!\n\n"
        "Вы уверены, что хотите удалить ВСЕ импортированные планы?\n\n"
        "Это приведет к удалению:\n"
        "• Всех скрытых планов подписок\n"
        "• Связанных пользовательских подписок\n\n"
        "❗️ ДАННОЕ ДЕЙСТВИЕ НЕЛЬЗЯ ОТМЕНИТЬ!",
        reply_markup=keyboard
    )

@admin_router.callback_query(F.data == "confirm_delete_imported")
async def confirm_delete_imported_callback(callback: CallbackQuery, user: User, db: Database = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        await callback.answer("🗑 Удаляю импортированные планы...")
        
        progress_msg = await callback.message.edit_text("⏳ Удаление импортированных планов и связанных подписок...")
        
        all_plans = await db.get_all_subscriptions_admin()
        imported_plans = [plan for plan in all_plans if getattr(plan, 'is_imported', False)]
        
        for plan in all_plans:
            if (plan.name == "Старая подписка" and 
                plan not in imported_plans):
                imported_plans.append(plan)
        
        deleted_plans = 0
        deleted_user_subscriptions = 0
        errors = 0
        
        for plan in imported_plans:
            try:
                user_subscriptions = await db.get_user_subscriptions_by_plan_id(plan.id)
                
                for user_sub in user_subscriptions:
                    try:
                        success = await db.delete_user_subscription(user_sub.id)
                        if success:
                            deleted_user_subscriptions += 1
                            logger.info(f"Deleted user subscription {user_sub.id} (shortUuid: {user_sub.short_uuid})")
                    except Exception as e:
                        logger.error(f"Error deleting user subscription {user_sub.id}: {e}")
                        errors += 1
                
                success = await db.delete_subscription(plan.id)
                if success:
                    deleted_plans += 1
                    logger.info(f"Deleted imported plan: {plan.name} (ID: {plan.id})")
                else:
                    errors += 1
                    
            except Exception as e:
                logger.error(f"Error deleting imported plan {plan.id}: {e}")
                errors += 1
        
        result_text = (
            f"✅ Удаление импортированных данных завершено!\n\n"
            f"📊 Результаты:\n"
            f"• Удалено планов: {deleted_plans}\n"
            f"• Удалено пользовательских подписок: {deleted_user_subscriptions}\n"
            f"• Ошибок: {errors}\n\n"
            f"🔄 Теперь импорт можно запустить заново\n\n"
            f"🕐 Завершено: {format_datetime(datetime.now(), user.language)}"
        )
        
        await progress_msg.edit_text(
            result_text,
            reply_markup=sync_remnawave_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "deleted_imported_all", f"Plans: {deleted_plans}, UserSubs: {deleted_user_subscriptions}")
        
    except Exception as e:
        logger.error(f"Error deleting imported plans: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка удаления планов\n\n{str(e)[:200]}",
            reply_markup=sync_remnawave_keyboard(user.language)
        )

@admin_router.callback_query(F.data == "debug_all_plans")
async def debug_all_plans_callback(callback: CallbackQuery, user: User, db: Database = None, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        await callback.answer("🔍 Анализирую все планы...")
        
        all_plans = await db.get_all_subscriptions_admin()
        
        if not all_plans:
            await callback.message.edit_text(
                "❌ Планы не найдены в базе данных",
                reply_markup=sync_remnawave_keyboard(user.language)
            )
            return
        
        analysis = f"🔍 Анализ всех планов ({len(all_plans)} шт.)\n\n"
        
        for i, plan in enumerate(all_plans, 1):
            analysis += f"=== ПЛАН {i} ===\n"
            analysis += f"ID: {plan.id}\n"
            analysis += f"Название: {plan.name}\n"
            analysis += f"Цена: {plan.price}₽\n"
            analysis += f"Активен: {'Да' if plan.is_active else 'Нет'}\n"
            analysis += f"Триальный: {'Да' if getattr(plan, 'is_trial', False) else 'Нет'}\n"
            analysis += f"Импортированный: {'Да' if getattr(plan, 'is_imported', False) else 'Нет'}\n"
            
            if plan.squad_uuid:
                analysis += f"Squad UUID: {plan.squad_uuid[:20]}...\n"
            else:
                analysis += f"Squad UUID: НЕТ\n"
            
            if plan.description:
                desc_short = plan.description[:50] + "..." if len(plan.description) > 50 else plan.description
                analysis += f"Описание: {desc_short}\n"
            
            looks_imported = (
                getattr(plan, 'is_imported', False) or
                plan.name.startswith(('Import_', 'Auto_', 'Imported_', 'Trial_')) or
                (plan.price == 0 and any(keyword in plan.name.lower() for keyword in 
                    ['импорт', 'default', 'squad', 'user_', 'trial']))
            )
            
            analysis += f"Создан: {plan.created_at.strftime('%Y-%m-%d %H:%M') if plan.created_at else 'N/A'}\n"
            analysis += "\n"
        
        max_length = 4000
        if len(analysis) > max_length:
            parts = []
            current_part = ""
            
            for line in analysis.split('\n'):
                if len(current_part + line + '\n') > max_length:
                    if current_part:
                        parts.append(current_part.strip())
                        current_part = ""
                current_part += line + '\n'
            
            if current_part:
                parts.append(current_part.strip())
            
            for i, part in enumerate(parts):
                if i == 0:
                    await callback.message.edit_text(part)
                else:
                    await callback.message.answer(f"Часть {i+1}:\n\n{part}")
        else:
            await callback.message.edit_text(analysis)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏷 Переименовать Trial_", callback_data="rename_imported_plans")],
            [InlineKeyboardButton(text="📋 Просмотр планов", callback_data="view_imported_plans")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="sync_remnawave")]
        ])
        
        await callback.message.answer(
            "✅ Анализ завершен",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error debugging all plans: {e}")
        await callback.answer("❌ Ошибка анализа планов", show_alert=True)

@admin_router.callback_query(F.data == "admin_referrals")
async def admin_referrals_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "👥 Управление реферальной программой",
        reply_markup=admin_referrals_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "referral_statistics")
async def referral_statistics_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        async with db.session_factory() as session:
            from sqlalchemy import select, func, and_
            
            from database import ReferralProgram, ReferralEarning
            
            total_referrals = await session.execute(
                select(func.count(ReferralProgram.id)).where(
                    and_(
                        ReferralProgram.referred_id < 900000000,
                        ReferralProgram.referred_id > 0
                    )
                )
            )
            total_referrals = total_referrals.scalar() or 0
            
            active_referrals = await session.execute(
                select(func.count(ReferralProgram.id)).where(
                    and_(
                        ReferralProgram.first_reward_paid == True,
                        ReferralProgram.referred_id < 900000000,
                        ReferralProgram.referred_id > 0
                    )
                )
            )
            active_referrals = active_referrals.scalar() or 0
            
            total_paid = await session.execute(
                select(func.sum(ReferralEarning.amount))
            )
            total_paid = total_paid.scalar() or 0.0
            
            top_referrers = await session.execute(
                select(
                    ReferralProgram.referrer_id, 
                    func.count(ReferralProgram.id).label('count')
                ).where(
                    and_(
                        ReferralProgram.referred_id < 900000000,
                        ReferralProgram.referred_id > 0
                    )
                ).group_by(ReferralProgram.referrer_id)
                .order_by(func.count(ReferralProgram.id).desc())
                .limit(5)
            )
            top_referrers = list(top_referrers.fetchall())
        
        text = "📊 Статистика реферальной программы\n\n"
        text += f"👥 Всего рефералов: {total_referrals}\n"
        text += f"✅ Активных рефералов: {active_referrals}\n"
        text += f"💰 Выплачено всего: {total_paid:.2f}₽\n"
        
        if total_referrals > 0:
            conversion = (active_referrals / total_referrals * 100)
            text += f"📈 Конверсия: {conversion:.1f}%\n"
        else:
            text += f"📈 Конверсия: 0%\n"
        
        if top_referrers:
            text += f"\n🏆 Топ рефереров:\n"
            for i, (referrer_id, count) in enumerate(top_referrers, 1):
                try:
                    referrer = await db.get_user_by_telegram_id(referrer_id)
                    if referrer:
                        display_name = ""
                        if referrer.first_name:
                            display_name = referrer.first_name
                        if referrer.username:
                            display_name += f" (@{referrer.username})" if display_name else f"@{referrer.username}"
                        if not display_name:
                            display_name = f"Пользователь {referrer_id}"
                        
                        text += f"{i}. {display_name}: {count} рефералов\n"
                    else:
                        text += f"{i}. ID:{referrer_id}: {count} рефералов\n"
                except Exception as e:
                    logger.error(f"Error getting referrer info for {referrer_id}: {e}")
                    text += f"{i}. ID:{referrer_id}: {count} рефералов\n"
        
        try:
            async with db.session_factory() as session:
                first_rewards = await session.execute(
                    select(func.count(ReferralEarning.id), func.sum(ReferralEarning.amount))
                    .where(ReferralEarning.earning_type == 'first_reward')
                )
                first_rewards_data = first_rewards.fetchone()
                
                percentage_rewards = await session.execute(
                    select(func.count(ReferralEarning.id), func.sum(ReferralEarning.amount))
                    .where(ReferralEarning.earning_type == 'percentage')
                )
                percentage_rewards_data = percentage_rewards.fetchone()
                
                text += f"\n💸 Детализация выплат:\n"
                if first_rewards_data and first_rewards_data[0]:
                    text += f"• Первые награды: {first_rewards_data[0]} шт. ({first_rewards_data[1]:.2f}₽)\n"
                if percentage_rewards_data and percentage_rewards_data[0]:
                    text += f"• Процентные: {percentage_rewards_data[0]} шт. ({percentage_rewards_data[1]:.2f}₽)\n"
        except Exception as e:
            logger.error(f"Error getting payment stats: {e}")
        
        await callback.message.edit_text(
            text,
            reply_markup=back_keyboard("admin_referrals", user.language)
        )
        
    except Exception as e:
        logger.error(f"Error getting referral statistics: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения статистики",
            reply_markup=back_keyboard("admin_referrals", user.language)
        )

@admin_router.callback_query(F.data == "list_referrers")
async def list_referrers_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        async with db.session_factory() as session:
            from sqlalchemy import select, func, and_, case
            
            from database import ReferralProgram
            
            top_referrers = await session.execute(
                select(
                    ReferralProgram.referrer_id,
                    func.count(ReferralProgram.id).label('total_referrals'),
                    func.count(case((ReferralProgram.first_reward_paid == True, 1))).label('active_referrals'),
                    func.sum(ReferralProgram.total_earned).label('total_earned')
                ).where(
                    and_(
                        ReferralProgram.referred_id < 900000000,
                        ReferralProgram.referred_id > 0
                    )
                ).group_by(ReferralProgram.referrer_id)
                .order_by(func.count(ReferralProgram.id).desc())
                .limit(10)
            )
            referrers_data = list(top_referrers.fetchall())
        
        if not referrers_data:
            await callback.message.edit_text(
                "📊 Список рефереров пуст\n\nПока никто не пригласил пользователей.",
                reply_markup=back_keyboard("admin_referrals", user.language)
            )
            return
        
        text = f"👥 Топ-{len(referrers_data)} рефереров:\n\n"
        
        for i, (referrer_id, total_refs, active_refs, total_earned) in enumerate(referrers_data, 1):
            try:
                referrer = await db.get_user_by_telegram_id(referrer_id)
                
                if referrer:
                    display_name = ""
                    if referrer.first_name:
                        display_name = referrer.first_name[:15]
                    if referrer.username:
                        username_part = f"@{referrer.username}"
                        if display_name:
                            display_name += f" ({username_part})"
                        else:
                            display_name = username_part
                    if not display_name:
                        display_name = f"Пользователь {referrer_id}"
                else:
                    display_name = f"ID:{referrer_id}"
                
                text += f"{i}. {display_name}\n"
                text += f"   👥 Всего: {total_refs} | ✅ Активных: {active_refs or 0}\n"
                text += f"   💰 Заработано: {total_earned or 0:.2f}₽\n\n"
                
            except Exception as e:
                logger.error(f"Error processing referrer {referrer_id}: {e}")
                text += f"{i}. ID:{referrer_id}\n"
                text += f"   👥 Всего: {total_refs} | ✅ Активных: {active_refs or 0}\n"
                text += f"   💰 Заработано: {total_earned or 0:.2f}₽\n\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="list_referrers")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_referrals")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error listing referrers: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения списка рефереров",
            reply_markup=back_keyboard("admin_referrals", user.language)
        )

@admin_router.callback_query(F.data == "referral_payments")
async def referral_payments_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        async with db.session_factory() as session:
            from sqlalchemy import select, desc
            
            from database import ReferralEarning
            
            recent_earnings = await session.execute(
                select(ReferralEarning)
                .order_by(desc(ReferralEarning.created_at))
                .limit(15)
            )
            earnings = list(recent_earnings.scalars().all())
        
        if not earnings:
            await callback.message.edit_text(
                "💰 История выплат пуста\n\nРеферальных выплат пока не было.",
                reply_markup=back_keyboard("admin_referrals", user.language)
            )
            return
        
        text = f"💰 Последние {len(earnings)} выплат:\n\n"
        
        for earning in earnings:
            try:
                referrer = await db.get_user_by_telegram_id(earning.referrer_id)
                referred = await db.get_user_by_telegram_id(earning.referred_id)
                
                referrer_name = "Unknown"
                if referrer:
                    if referrer.username:
                        referrer_name = f"@{referrer.username}"
                    elif referrer.first_name:
                        referrer_name = referrer.first_name[:10]
                    else:
                        referrer_name = f"ID:{earning.referrer_id}"
                
                referred_name = "Unknown"
                if referred:
                    if referred.username:
                        referred_name = f"@{referred.username}"
                    elif referred.first_name:
                        referred_name = referred.first_name[:10]
                    else:
                        referred_name = f"ID:{earning.referred_id}"
                
                earning_type_emoji = "🎁" if earning.earning_type == "first_reward" else "💵"
                earning_type_name = "Первая награда" if earning.earning_type == "first_reward" else "Процент"
                
                date_str = earning.created_at.strftime("%d.%m %H:%M")
                
                text += f"{earning_type_emoji} {earning.amount:.2f}₽ - {earning_type_name}\n"
                text += f"   От: {referrer_name} ← {referred_name}\n"
                text += f"   📅 {date_str}\n\n"
                
            except Exception as e:
                logger.error(f"Error processing earning {earning.id}: {e}")
                text += f"💰 {earning.amount:.2f}₽ - {earning.earning_type}\n"
                text += f"   ID: {earning.referrer_id} ← {earning.referred_id}\n\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="referral_payments")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="referral_statistics")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_referrals")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error getting referral payments: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения истории выплат",
            reply_markup=back_keyboard("admin_referrals", user.language)
        )

@admin_router.callback_query(F.data == "referral_settings")
async def referral_settings_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        import os
        
        first_reward = float(os.getenv('REFERRAL_FIRST_REWARD', '150.0'))
        referred_bonus = float(os.getenv('REFERRAL_REFERRED_BONUS', '150.0'))
        threshold = float(os.getenv('REFERRAL_THRESHOLD', '300.0'))
        percentage = float(os.getenv('REFERRAL_PERCENTAGE', '0.25'))
        
        text = "⚙️ Настройки реферальной программы\n\n"
        text += "📋 Текущие параметры:\n\n"
        text += f"💰 Первая награда рефереру: {first_reward:.0f}₽\n"
        text += f"🎁 Бонус приглашенному: {referred_bonus:.0f}₽\n"
        text += f"💳 Порог активации: {threshold:.0f}₽\n"
        text += f"📊 Процент с платежей: {percentage*100:.0f}%\n\n"
        
        text += "ℹ️ Как это работает:\n"
        text += f"1. Пользователь регистрируется по ссылке\n"
        text += f"2. Пополняет баланс на {threshold:.0f}₽ или больше\n"
        text += f"3. Реферер получает {first_reward:.0f}₽, новичок {referred_bonus:.0f}₽\n"
        text += f"4. С каждого платежа новичка реферер получает {percentage*100:.0f}%\n\n"
        
        text += "⚠️ Для изменения настроек отредактируйте .env файл:\n"
        text += "• REFERRAL_FIRST_REWARD\n"
        text += "• REFERRAL_REFERRED_BONUS\n"
        text += "• REFERRAL_THRESHOLD\n"
        text += "• REFERRAL_PERCENTAGE"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="referral_statistics")],
            [InlineKeyboardButton(text="👥 Рефереры", callback_data="list_referrers")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_referrals")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error showing referral settings: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения настроек",
            reply_markup=back_keyboard("admin_referrals", user.language)
        )

@admin_router.callback_query(F.data == "admin_stars_payments")
async def admin_stars_payments_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    """Управление платежами через Stars"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "⭐ Управление Telegram Stars платежами",
        reply_markup=admin_stars_keyboard(user.language)
    )

def admin_stars_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура управления Stars"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика Stars", callback_data="admin_stars_stats")],
        [InlineKeyboardButton(text="📋 Последние платежи", callback_data="admin_stars_recent")],
        [InlineKeyboardButton(text="⚙️ Настройки курсов", callback_data="admin_stars_settings")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_balance")]
    ])
    return keyboard

@admin_router.callback_query(F.data == "admin_stars_stats")
async def admin_stars_stats_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    """Статистика Stars платежей"""
    if not await check_admin_access(callback, user):
        return
    
    try:
        async with db.session_factory() as session:
            from sqlalchemy import text
            
            stats_query = await session.execute(text("""
                SELECT 
                    COUNT(*) as total_payments,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed_payments,
                    COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending_payments,
                    COUNT(CASE WHEN status = 'cancelled' THEN 1 END) as cancelled_payments,
                    SUM(CASE WHEN status = 'completed' THEN stars_amount ELSE 0 END) as total_stars,
                    SUM(CASE WHEN status = 'completed' THEN rub_amount ELSE 0 END) as total_rubles
                FROM star_payments
            """))
            
            stats = stats_query.fetchone()
            
            daily_query = await session.execute(text("""
                SELECT 
                    DATE(created_at) as payment_date,
                    COUNT(*) as daily_count,
                    SUM(CASE WHEN status = 'completed' THEN rub_amount ELSE 0 END) as daily_amount
                FROM star_payments 
                WHERE created_at >= (CURRENT_DATE - INTERVAL '7 days')
                GROUP BY DATE(created_at)
                ORDER BY payment_date DESC
            """))
            
            daily_stats = daily_query.fetchall()
        
        text = "📊 Статистика Telegram Stars\n\n"
        
        if stats:
            text += "💫 Общая статистика:\n"
            text += f"• Всего платежей: {stats.total_payments or 0}\n"
            text += f"• Завершенных: {stats.completed_payments or 0}\n"
            text += f"• В ожидании: {stats.pending_payments or 0}\n"
            text += f"• Отмененных: {stats.cancelled_payments or 0}\n\n"
            
            text += f"💰 Финансовая статистика:\n"
            text += f"• Всего звезд получено: {stats.total_stars or 0} ⭐\n"
            text += f"• Общая сумма: {stats.total_rubles or 0:.0f}₽\n\n"
            
            if stats.total_payments and stats.total_payments > 0:
                conversion = (stats.completed_payments or 0) / stats.total_payments * 100
                text += f"📈 Конверсия: {conversion:.1f}%\n\n"
        
        if daily_stats:
            text += "📅 За последние 7 дней:\n"
            for day in daily_stats:
                date_str = day.payment_date.strftime('%d.%m')
                text += f"• {date_str}: {day.daily_count} платежей на {day.daily_amount:.0f}₽\n"
        else:
            text += "📅 За последние 7 дней: нет данных\n"
        
        text += f"\n🕐 Обновлено: {format_datetime(datetime.now(), user.language)}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stars_stats")],
            [InlineKeyboardButton(text="📋 Последние платежи", callback_data="admin_stars_recent")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_stars_payments")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error getting stars stats: {e}")
        try:
            await callback.message.edit_text(
                "❌ Ошибка получения статистики",
                reply_markup=admin_stars_keyboard(user.language)
            )
        except Exception as edit_error:
            await callback.answer("❌ Ошибка получения статистики", show_alert=True)


@admin_router.callback_query(F.data == "admin_stars_recent")
async def admin_stars_recent_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    """Последние Stars платежи"""
    if not await check_admin_access(callback, user):
        return
    
    try:
        async with db.session_factory() as session:
            from sqlalchemy import select, desc
            from database import StarPayment, User
            
            query = select(
                StarPayment,
                User.username,
                User.first_name
            ).outerjoin(
                User, StarPayment.user_id == User.telegram_id
            ).order_by(
                desc(StarPayment.created_at)
            ).limit(15)
            
            result = await session.execute(query)
            payments_data = result.fetchall()
        
        if not payments_data:
            text = "📋 История Stars платежей пуста"
        else:
            text = f"📋 Последние Stars платежи ({len(payments_data)}):\n\n"
            
            for row in payments_data:
                payment = row[0]  
                username = row[1]  
                first_name = row[2] 
                
                if payment.status == 'completed':
                    status_emoji = "✅"
                elif payment.status == 'pending':
                    status_emoji = "⏳"
                elif payment.status == 'cancelled':
                    status_emoji = "❌"
                else:
                    status_emoji = "❓"
                
                user_name = "Unknown"
                if first_name:
                    user_name = first_name.replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
                if username:
                    clean_username = username.replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
                    if user_name != "Unknown":
                        user_name += f" (@{clean_username})"
                    else:
                        user_name = f"@{clean_username}"
                
                payment_date = payment.completed_at if payment.completed_at else payment.created_at
                date_str = payment_date.strftime('%d.%m %H:%M')
                
                text += f"{status_emoji} {payment.stars_amount} ⭐ → {payment.rub_amount:.0f}₽\n"
                text += f"   👤 {user_name} (ID: {payment.user_id})\n"
                text += f"   📅 {date_str}\n"
                
                if payment.telegram_payment_charge_id:
                    charge_short = payment.telegram_payment_charge_id[:20] + "..."
                    text += f"   🧾 {charge_short}\n"
                
                text += "\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stars_recent")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stars_stats")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_stars_payments")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error getting recent stars payments: {e}")
        try:
            await callback.message.edit_text(
                "❌ Ошибка получения платежей",
                reply_markup=admin_stars_keyboard(user.language)
            )
        except Exception as edit_error:
            await callback.answer("❌ Ошибка получения платежей", show_alert=True)

@admin_router.callback_query(F.data == "admin_stars_settings")
async def admin_stars_settings_callback(callback: CallbackQuery, user: User, **kwargs):
    """Настройки курсов Stars"""
    if not await check_admin_access(callback, user):
        return
    
    config = kwargs.get('config')
    
    text = "⚙️ Настройки Telegram Stars\n\n"
    
    if config and config.STARS_ENABLED:
        text += "✅ Статус: Включено\n\n"
        
        if config.STARS_RATES:
            text += "💱 Текущие курсы:\n"
            sorted_rates = sorted(config.STARS_RATES.items())
            for stars, rubles in sorted_rates:
                rate_per_star = rubles / stars
                text += f"• {stars} ⭐ = {rubles:.0f}₽ (курс: {rate_per_star:.2f}₽/⭐)\n"
            
            text += "\n📈 Анализ выгодности:\n"
            base_rate = sorted_rates[0][1] / sorted_rates[0][0] if sorted_rates else 0
            for stars, rubles in sorted_rates:
                current_rate = rubles / stars
                if current_rate < base_rate:
                    savings = (base_rate - current_rate) / base_rate * 100
                    text += f"• {stars} ⭐: выгода {savings:.1f}%\n"
        else:
            text += "❌ Курсы не настроены\n"
    else:
        text += "❌ Статус: Отключено\n"
    
    text += "\n⚙️ Настройка через .env файл:\n"
    text += "\nSTARS_ENABLED=true\n"
    text += "STARS_100_RATE=150\n"
    text += "STARS_150_RATE=220\n"
    text += "STARS_250_RATE=400\n"
    text += "STARS_350_RATE=500\n"
    text += "STARS_500_RATE=800\n"
    text += "STARS_750_RATE=1150\n"
    text += "STARS_1000_RATE=1500\n"
    
    text += "\n💡 Рекомендации:\n"
    text += "• Большие пакеты должны быть выгоднее\n"
    text += "• Курс должен покрывать комиссии Telegram\n"
    text += "• Регулярно анализируйте конверсию\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stars_stats")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_stars_payments")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=keyboard
    )

@admin_router.callback_query(F.data == "admin_rules")
async def admin_rules_callback(callback: CallbackQuery, user: User, **kwargs):
    """Главное меню управления правилами"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "📜 Управление правилами сервиса\n\n"
        "Здесь вы можете создавать, редактировать и управлять страницами правил сервиса, "
        "которые видят пользователи в главном меню.",
        reply_markup=admin_rules_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "admin_rules_list")
async def admin_rules_list_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        rules = await db.get_all_service_rules(active_only=False)
        
        if not rules:
            await callback.message.edit_text(
                "📜 Правила сервиса не созданы\n\n"
                "Создайте первую страницу правил для пользователей.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Создать первую страницу", callback_data="admin_rules_create")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_rules")]
                ])
            )
            return
        
        text = f"📜 Список правил сервиса ({len(rules)} страниц)\n\n"
        
        for rule in rules:
            status = "🟢 Активна" if rule.is_active else "🔴 Отключена"
            text += f"{rule.page_order}. **{rule.title}**\n"
            text += f"   {status}\n"
            text += f"   Создано: {rule.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=admin_rules_list_keyboard(rules, user.language),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error listing service rules: {e}")
        await callback.answer("❌ Ошибка загрузки правил")

@admin_router.callback_query(F.data == "admin_rules_create")
async def admin_rules_create_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "📝 Создание новой страницы правил\n\n"
        "Введите заголовок страницы (например: 'Общие положения', 'Правила использования'):",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.waiting_rule_title)

@admin_router.message(StateFilter(BotStates.waiting_rule_title))
async def handle_rule_title(message: Message, state: FSMContext, user: User, **kwargs):
    title = message.text.strip()
    
    if len(title) < 3 or len(title) > 200:
        await message.answer("❌ Заголовок должен быть от 3 до 200 символов")
        return
    
    await state.update_data(rule_title=title)
    await message.answer(
        f"✅ Заголовок установлен: **{title}**\n\n"
        "📝 Теперь введите содержимое страницы правил:\n\n"
        "💡 Вы можете использовать форматирование Markdown:\n"
        "• **жирный текст**\n"
        "• *курсив*\n"
        "• `код`\n"
        "• [ссылка](url)\n\n"
        "Максимальная длина: 3500 символов",
        reply_markup=cancel_keyboard(user.language),
        parse_mode='Markdown'
    )
    await state.set_state(BotStates.waiting_rule_content)

@admin_router.message(StateFilter(BotStates.waiting_rule_content))
async def handle_rule_content(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    content = message.text.strip()
    
    if len(content) < 10:
        await message.answer("❌ Содержимое должно быть не менее 10 символов")
        return
    
    if len(content) > 3500:
        await message.answer("❌ Содержимое слишком длинное. Максимум 3500 символов.")
        return
    
    try:
        data = await state.get_data()
        title = data.get('rule_title')
        
        rule = await db.create_service_rule(title=title, content=content)
        
        await message.answer(
            f"✅ Страница правил создана!\n\n"
            f"📋 Заголовок: {title}\n"
            f"📄 Порядок: {rule.page_order}\n"
            f"📊 Статус: {'🟢 Активна' if rule.is_active else '🔴 Отключена'}\n\n"
            f"Пользователи смогут увидеть эту страницу в меню 'Правила сервиса'.",
            reply_markup=admin_menu_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "service_rule_created", f"Title: {title}")
        
    except Exception as e:
        logger.error(f"Error creating service rule: {e}")
        await message.answer(
            "❌ Ошибка создания страницы правил",
            reply_markup=admin_menu_keyboard(user.language)
        )
    
    await state.clear()


@admin_router.callback_query(F.data.startswith("admin_rule_view_"))
async def admin_rule_view_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        rule_id = int(callback.data.split("_")[-1])
        rule = await db.get_service_rule_by_id(rule_id)
        
        if not rule:
            await callback.answer("❌ Правило не найдено")
            return
        
        safe_title = rule.title.replace('*', '\\*').replace('_', '\\_').replace('[', '\\[').replace(']', '\\]').replace('`', '\\`')
        
        text = f"📜 **{safe_title}**\n\n"
        text += f"📄 Порядок: {rule.page_order}\n"
        text += f"📊 Статус: {'🟢 Активна' if rule.is_active else '🔴 Отключена'}\n"
        
        created_date = rule.created_at.strftime('%d.%m.%Y %H:%M') if rule.created_at else 'N/A'
        updated_date = rule.updated_at.strftime('%d.%m.%Y %H:%M') if rule.updated_at else 'N/A'
        
        text += f"📅 Создано: {created_date}\n"
        text += f"📝 Изменено: {updated_date}\n\n"
        
        content_preview = rule.content[:200]
        safe_preview = (content_preview
                       .replace('*', '')
                       .replace('_', '')
                       .replace('[', '')
                       .replace(']', '')
                       .replace('`', '')
                       .replace('#', ''))
        
        if len(rule.content) > 200:
            safe_preview += "..."
        
        text += f"**Превью содержимого:**\n{safe_preview}"
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=admin_rule_edit_keyboard(rule_id, user.language),
                parse_mode='Markdown'
            )
        except Exception as markdown_error:
            logger.warning(f"Markdown parsing failed, sending without formatting: {markdown_error}")
            
            simple_text = f"📜 {rule.title}\n\n"
            simple_text += f"📄 Порядок: {rule.page_order}\n"
            simple_text += f"📊 Статус: {'🟢 Активна' if rule.is_active else '🔴 Отключена'}\n"
            simple_text += f"📅 Создано: {created_date}\n"
            simple_text += f"📝 Изменено: {updated_date}\n\n"
            simple_text += f"Превью содержимого:\n{safe_preview}"
            
            await callback.message.edit_text(
                simple_text,
                reply_markup=admin_rule_edit_keyboard(rule_id, user.language)
            )
        
    except Exception as e:
        logger.error(f"Error viewing service rule: {e}")
        await callback.answer("❌ Ошибка загрузки правила")

@admin_router.callback_query(F.data.startswith("admin_rule_edit_title_"))
async def admin_rule_edit_title_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    rule_id = int(callback.data.split("_")[-1])
    await state.update_data(edit_rule_id=rule_id)
    
    await callback.message.edit_text(
        "✏️ Редактирование заголовка\n\n"
        "Введите новый заголовок страницы (3-200 символов):",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.waiting_rule_edit_title)

@admin_router.message(StateFilter(BotStates.waiting_rule_edit_title))
async def handle_rule_edit_title(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    new_title = message.text.strip()
    
    if len(new_title) < 3 or len(new_title) > 200:
        await message.answer("❌ Заголовок должен быть от 3 до 200 символов")
        return
    
    try:
        data = await state.get_data()
        rule_id = data.get('edit_rule_id')
        
        rule = await db.get_service_rule_by_id(rule_id)
        if not rule:
            await message.answer("❌ Правило не найдено")
            await state.clear()
            return
        
        old_title = rule.title
        rule.title = new_title
        success = await db.update_service_rule(rule)
        
        if success:
            await message.answer(
                f"✅ Заголовок обновлен!\n\n"
                f"Было: {old_title}\n"
                f"Стало: {new_title}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📜 К правилу", callback_data=f"admin_rule_view_{rule_id}")],
                    [InlineKeyboardButton(text="📋 К списку", callback_data="admin_rules_list")]
                ])
            )
            
            log_user_action(user.telegram_id, "service_rule_title_edited", 
                          f"ID: {rule_id}, New: {new_title}")
        else:
            await message.answer("❌ Ошибка обновления заголовка")
        
    except Exception as e:
        logger.error(f"Error updating rule title: {e}")
        await message.answer("❌ Ошибка обновления")
    
    await state.clear()

@admin_router.callback_query(F.data.startswith("admin_rule_edit_content_"))
async def admin_rule_edit_content_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    rule_id = int(callback.data.split("_")[-1])
    await state.update_data(edit_rule_id=rule_id)
    
    await callback.message.edit_text(
        "📝 Редактирование содержимого\n\n"
        "Введите новое содержимое страницы правил:\n\n"
        "💡 Поддерживается Markdown форматирование\n"
        "Максимальная длина: 3500 символов",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.waiting_rule_edit_content)

@admin_router.message(StateFilter(BotStates.waiting_rule_edit_content))
async def handle_rule_edit_content(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    new_content = message.text.strip()
    
    if len(new_content) < 10:
        await message.answer("❌ Содержимое должно быть не менее 10 символов")
        return
    
    if len(new_content) > 3500:
        await message.answer("❌ Содержимое слишком длинное. Максимум 3500 символов.")
        return
    
    try:
        data = await state.get_data()
        rule_id = data.get('edit_rule_id')
        
        rule = await db.get_service_rule_by_id(rule_id)
        if not rule:
            await message.answer("❌ Правило не найдено")
            await state.clear()
            return
        
        rule.content = new_content
        success = await db.update_service_rule(rule)
        
        if success:
            await message.answer(
                f"✅ Содержимое обновлено!\n\n"
                f"📜 Правило: {rule.title}\n"
                f"📝 Новый размер: {len(new_content)} символов",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📜 К правилу", callback_data=f"admin_rule_view_{rule_id}")],
                    [InlineKeyboardButton(text="📋 К списку", callback_data="admin_rules_list")]
                ])
            )
            
            log_user_action(user.telegram_id, "service_rule_content_edited", 
                          f"ID: {rule_id}, Length: {len(new_content)}")
        else:
            await message.answer("❌ Ошибка обновления содержимого")
        
    except Exception as e:
        logger.error(f"Error updating rule content: {e}")
        await message.answer("❌ Ошибка обновления")
    
    await state.clear()

@admin_router.callback_query(F.data.startswith("admin_rule_edit_order_"))
async def admin_rule_edit_order_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    rule_id = int(callback.data.split("_")[-1])
    await state.update_data(edit_rule_id=rule_id)
    
    await callback.message.edit_text(
        "🔄 Изменение порядка страницы\n\n"
        "Введите новый номер позиции страницы (число от 1 до 100):\n\n"
        "💡 Страницы с меньшим номером показываются раньше",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.waiting_rule_edit_order)

@admin_router.message(StateFilter(BotStates.waiting_rule_edit_order))
async def handle_rule_edit_order(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    try:
        new_order = int(message.text.strip())
        
        if new_order < 1 or new_order > 100:
            await message.answer("❌ Порядок должен быть от 1 до 100")
            return
        
        data = await state.get_data()
        rule_id = data.get('edit_rule_id')
        
        rule = await db.get_service_rule_by_id(rule_id)
        if not rule:
            await message.answer("❌ Правило не найдено")
            await state.clear()
            return
        
        old_order = rule.page_order
        rule.page_order = new_order
        success = await db.update_service_rule(rule)
        
        if success:
            await message.answer(
                f"✅ Порядок страницы изменен!\n\n"
                f"📜 Правило: {rule.title}\n"
                f"📄 Было: {old_order}\n"
                f"📄 Стало: {new_order}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📜 К правилу", callback_data=f"admin_rule_view_{rule_id}")],
                    [InlineKeyboardButton(text="📋 К списку", callback_data="admin_rules_list")]
                ])
            )
            
            log_user_action(user.telegram_id, "service_rule_order_changed", 
                          f"ID: {rule_id}, Order: {old_order}->{new_order}")
        else:
            await message.answer("❌ Ошибка изменения порядка")
        
    except ValueError:
        await message.answer("❌ Введите корректное число")
    except Exception as e:
        logger.error(f"Error updating rule order: {e}")
        await message.answer("❌ Ошибка обновления")
    
    await state.clear()

@admin_router.callback_query(F.data.startswith("admin_rule_toggle_"))
async def admin_rule_toggle_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        rule_id = int(callback.data.split("_")[-1])
        rule = await db.get_service_rule_by_id(rule_id)
        
        if not rule:
            await callback.answer("❌ Правило не найдено")
            return
        
        rule.is_active = not rule.is_active
        success = await db.update_service_rule(rule)
        
        if success:
            status_text = "активирована" if rule.is_active else "отключена"
            await callback.answer(f"✅ Страница '{rule.title}' {status_text}")
            
            await admin_rule_view_callback(callback, user, db, **kwargs)
            
            log_user_action(user.telegram_id, "service_rule_toggled", 
                          f"ID: {rule_id}, Active: {rule.is_active}")
        else:
            await callback.answer("❌ Ошибка обновления статуса")
        
    except Exception as e:
        logger.error(f"Error toggling service rule: {e}")
        await callback.answer("❌ Ошибка изменения статуса")

@admin_router.callback_query(F.data.startswith("admin_rule_delete_"))
async def admin_rule_delete_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        rule_id = int(callback.data.split("_")[-1])
        rule = await db.get_service_rule_by_id(rule_id)
        
        if not rule:
            await callback.answer("❌ Правило не найдено")
            return
        
        await callback.message.edit_text(
            f"⚠️ Удаление страницы правил\n\n"
            f"📜 Заголовок: **{rule.title}**\n"
            f"📄 Порядок: {rule.page_order}\n\n"
            f"❗️ Это действие нельзя отменить!\n"
            f"Пользователи больше не увидят эту страницу.",
            reply_markup=admin_rule_delete_confirm_keyboard(rule_id, user.language),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing delete confirmation: {e}")
        await callback.answer("❌ Ошибка")

@admin_router.callback_query(F.data.startswith("admin_rule_confirm_delete_"))
async def admin_rule_confirm_delete_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        rule_id = int(callback.data.split("_")[-1])
        rule = await db.get_service_rule_by_id(rule_id)
        
        if not rule:
            await callback.answer("❌ Правило не найдено")
            return
        
        rule_title = rule.title
        success = await db.delete_service_rule(rule_id)
        
        if success:
            await callback.message.edit_text(
                f"✅ Страница правил удалена\n\n"
                f"📜 Была удалена: {rule_title}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 К списку правил", callback_data="admin_rules_list")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                ])
            )
            
            log_user_action(user.telegram_id, "service_rule_deleted", f"Title: {rule_title}")
        else:
            await callback.answer("❌ Ошибка удаления")
        
    except Exception as e:
        logger.error(f"Error deleting service rule: {e}")
        await callback.answer("❌ Ошибка удаления")

@admin_router.callback_query(F.data == "admin_autopay")
async def admin_autopay_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "🔄 Управление автоплатежами\n\n"
        "Здесь вы можете просматривать статистику и управлять сервисом автоматических платежей.",
        reply_markup=admin_autopay_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "autopay_status")
async def autopay_status_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    autopay_service = kwargs.get('autopay_service')
    db = kwargs.get('db')
    
    if not autopay_service:
        await callback.message.edit_text(
            "❌ Сервис автоплатежей недоступен",
            reply_markup=back_keyboard("admin_autopay", user.language)
        )
        return
    
    try:
        status = await autopay_service.get_service_status()
        
        subscriptions_with_autopay = await db.get_subscriptions_for_autopay()
        
        text = "🔄 **Статус сервиса автоплатежей**\n\n"
        
        if status['is_running']:
            text += "✅ **Статус:** Работает\n"
        else:
            text += "❌ **Статус:** Остановлен\n"
        
        text += f"⚙️ **Настройки:**\n"
        text += f"• Интервал проверки: {status['check_interval']//60} мин\n"
        text += f"• API подключен: {'✅' if status['has_api'] else '❌'}\n"
        text += f"• Бот подключен: {'✅' if status['has_bot'] else '❌'}\n\n"
        
        text += f"📊 **Статистика:**\n"
        text += f"• Подписок с автоплатежом: {len(subscriptions_with_autopay)}\n"
        
        days_stats = {}
        for sub in subscriptions_with_autopay:
            days = sub.auto_pay_days_before
            days_stats[days] = days_stats.get(days, 0) + 1
        
        if days_stats:
            text += f"• Распределение по дням:\n"
            for days in sorted(days_stats.keys()):
                text += f"  - За {days} дн.: {days_stats[days]} подписок\n"
        
        text += f"\n🕐 Обновлено: {format_datetime(datetime.now(), user.language)}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="autopay_status")],
            [InlineKeyboardButton(text="🚀 Принудительная проверка", callback_data="autopay_force_check")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_autopay")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error getting autopay status: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения статуса",
            reply_markup=back_keyboard("admin_autopay", user.language)
        )

@admin_router.callback_query(F.data == "autopay_force_check")
async def autopay_force_check_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    autopay_service = kwargs.get('autopay_service')
    
    if not autopay_service:
        await callback.answer("❌ Сервис автоплатежей недоступен")
        return
    
    try:
        await callback.answer("⏳ Запускаю проверку автоплатежей...")
        
        stats = await autopay_service.process_autopayments()
        
        text = "✅ Принудительная проверка автоплатежей завершена!\n\n"
        text += f"📊 Результаты:\n"
        text += f"• Обработано: {stats['processed']}\n"
        text += f"• Успешно: {stats['successful']}\n"
        text += f"• Недостаточно средств: {stats['insufficient_balance']}\n"
        text += f"• Ошибки: {stats['failed']}\n"
        
        if stats['errors']:
            text += f"\n❌ Детали ошибок:\n"
            for error in stats['errors'][:5]:
                text += f"• {error}\n"
            if len(stats['errors']) > 5:
                text += f"... и еще {len(stats['errors']) - 5}\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=back_keyboard("admin_autopay", user.language)
        )
        
        log_user_action(user.telegram_id, "autopay_force_check", 
                       f"Processed: {stats['processed']}, Successful: {stats['successful']}")
        
    except Exception as e:
        logger.error(f"Error in force autopay check: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при выполнении проверки",
            reply_markup=back_keyboard("admin_autopay", user.language)
        )

def admin_autopay_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статус сервиса", callback_data="autopay_status")],
        [InlineKeyboardButton(text="🚀 Принудительная проверка", callback_data="autopay_force_check")],
        [InlineKeyboardButton(text="📈 Статистика автоплатежей", callback_data="autopay_statistics")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

@admin_router.callback_query(F.data == "autopay_statistics") 
async def autopay_statistics_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        await callback.answer("📊 Собираю статистику автоплатежей...")
        
        stats = await db.get_autopay_statistics()
        
        insufficient_balance_users = await db.get_users_with_insufficient_autopay_balance()
        
        autopay_history = await db.get_autopay_history(10)
        
        text = "📈 **Статистика автоплатежей**\n\n"
        
        text += "📊 **Общая информация:**\n"
        text += f"• Всего подписок с автоплатежом: {stats['total_autopay_subscriptions']}\n"
        text += f"• Активных: {stats['active_autopay_subscriptions']}\n"
        text += f"• Просроченных: {stats['expired_autopay_subscriptions']}\n\n"
        
        if stats['ready_for_autopay']:
            text += "🔄 **Готовы к автоплатежу:**\n"
            total_ready = 0
            for ready_info in stats['ready_for_autopay']:
                count = ready_info['count']
                days = ready_info['days']
                total_ready += count
                if count > 0:
                    text += f"• За {days} дн.: {count} подписок\n"
            
            if total_ready == 0:
                text += "• Нет подписок, готовых к продлению\n"
            text += "\n"
        
        if insufficient_balance_users:
            text += f"⚠️ **Недостаточно средств ({len(insufficient_balance_users)}):**\n"
            for user_info in insufficient_balance_users[:5]:
                username = user_info.get('username', 'N/A')
                needed = user_info['needed_amount']
                days = user_info['expires_in_days']
                text += f"• @{username}: нужно {needed:.0f}₽ (через {days}д)\n"
            
            if len(insufficient_balance_users) > 5:
                text += f"• ... и еще {len(insufficient_balance_users) - 5}\n"
            text += "\n"
        
        if autopay_history:
            text += f"💳 **Последние автоплатежи:**\n"
            for payment in autopay_history[:5]:
                username = payment.get('username', 'N/A')
                amount = abs(payment['amount']) 
                date_str = payment['created_at'].strftime('%d.%m %H:%M')
                status_emoji = "✅" if payment['status'] == 'completed' else "❌"
                text += f"• {status_emoji} @{username}: {amount:.0f}₽ ({date_str})\n"
            text += "\n"
        
        autopay_service = kwargs.get('autopay_service')
        if autopay_service:
            service_status = await autopay_service.get_service_status()
            status_emoji = "✅" if service_status['is_running'] else "❌"
            text += f"🔧 **Статус сервиса:** {status_emoji}\n"
            text += f"• Интервал проверки: {service_status['check_interval']//60} мин\n"
        else:
            text += f"🔧 **Статус сервиса:** ❌ Недоступен\n"
        
        text += f"\n🕐 Обновлено: {format_datetime(datetime.now(), user.language)}"
        
        await callback.message.edit_text(
            text,
            reply_markup=autopay_statistics_keyboard(user.language),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error getting detailed autopay statistics: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения статистики",
            reply_markup=back_keyboard("admin_autopay", user.language)
        )

@admin_router.callback_query(F.data == "autopay_insufficient_balance_users")
async def autopay_insufficient_balance_users_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        insufficient_users = await db.get_users_with_insufficient_autopay_balance()
        
        if not insufficient_users:
            text = "✅ **Все пользователи с автоплатежом имеют достаточный баланс**\n\n"
            text += "Проблемных автоплатежей не обнаружено."
        else:
            text = f"⚠️ **Пользователи с недостаточным балансом ({len(insufficient_users)})**\n\n"
            
            insufficient_users.sort(key=lambda x: x['expires_in_days'])
            
            for user_info in insufficient_users:
                username = user_info.get('username', 'N/A')
                first_name = user_info.get('first_name', 'N/A')
                current_balance = user_info['current_balance']
                needed = user_info['needed_amount']
                price = user_info['subscription_price']
                days = user_info['expires_in_days']
                sub_name = user_info['subscription_name']
                
                display_name = first_name
                if username != 'N/A':
                    display_name += f" (@{username})"
                
                urgency_emoji = "🔴" if days <= 1 else "🟡" if days <= 3 else "🟠"
                
                text += f"{urgency_emoji} **{display_name}**\n"
                text += f"   💳 Баланс: {current_balance:.2f}₽ / {price:.2f}₽\n"
                text += f"   💸 Нужно: {needed:.2f}₽\n"
                text += f"   📋 {sub_name}\n"
                text += f"   ⏰ Истекает через: {days} дн.\n\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="autopay_insufficient_balance_users")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="autopay_statistics")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_autopay")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error getting insufficient balance users: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения списка пользователей",
            reply_markup=back_keyboard("admin_autopay", user.language)
        )

@admin_router.callback_query(F.data == "autopay_subscriptions_list")
async def autopay_subscriptions_list_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        subscriptions_with_autopay = await db.get_subscriptions_for_autopay()
        
        subscriptions_data = []
        
        from datetime import datetime
        current_time = datetime.utcnow()
        
        for user_sub in subscriptions_with_autopay:
            try:
                user_obj = await db.get_user_by_telegram_id(user_sub.user_id)
                username = user_obj.username if user_obj else 'N/A'
                
                expires_in_days = (user_sub.expires_at - current_time).days
                
                subscriptions_data.append({
                    'user_id': user_sub.user_id,
                    'username': username,
                    'auto_pay_days_before': user_sub.auto_pay_days_before,
                    'expires_in_days': expires_in_days,
                    'subscription_id': user_sub.id
                })
                
            except Exception as e:
                logger.warning(f"Error processing subscription {user_sub.id}: {e}")
                continue
        
        subscriptions_data.sort(key=lambda x: x['expires_in_days'])
        
        text = f"📋 Подписки с автоплатежом ({len(subscriptions_data)})\n\n"
        
        if subscriptions_data:
            expired = [s for s in subscriptions_data if s['expires_in_days'] <= 0]
            due_soon = [s for s in subscriptions_data if 0 < s['expires_in_days'] <= s['auto_pay_days_before']]
            normal = [s for s in subscriptions_data if s['expires_in_days'] > s['auto_pay_days_before']]
            
            text += f"📊 Статус:\n"
            text += f"• ❌ Истекли: {len(expired)}\n"
            text += f"• ⚠️ Скоро продление: {len(due_soon)}\n"
            text += f"• ✅ Нормальные: {len(normal)}\n\n"
            
            text += "👥 Нажмите на пользователя для подробностей:"
        else:
            text += "📭 Нет подписок с включенным автоплатежом"
        
        await callback.message.edit_text(
            text,
            reply_markup=autopay_subscriptions_keyboard(subscriptions_data, user.language)
        )
        
    except Exception as e:
        logger.error(f"Error getting autopay subscriptions: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения списка подписок",
            reply_markup=back_keyboard("admin_autopay", user.language)
        )

@admin_router.callback_query(F.data.startswith("autopay_user_detail_"))
async def autopay_user_detail_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split("_")[-1])
        
        target_user = await db.get_user_by_telegram_id(user_id)
        if not target_user:
            await callback.answer("❌ Пользователь не найден")
            return
        
        user_subs = await db.get_user_subscriptions(user_id)
        autopay_subs = [sub for sub in user_subs if sub.auto_pay_enabled]
        
        from datetime import datetime
        current_time = datetime.utcnow()
        
        text = f"👤 Пользователь с автоплатежом\n\n"
        
        display_name = target_user.first_name or "N/A"
        if target_user.username:
            display_name += f" (@{target_user.username})"
        
        text += f"📛 Имя: {display_name}\n"
        text += f"🆔 ID: {user_id}\n"
        text += f"💰 Баланс: {target_user.balance:.2f}₽\n\n"
        
        text += f"🔄 Подписки с автоплатежом ({len(autopay_subs)}):\n\n"
        
        for sub in autopay_subs:
            subscription = await db.get_subscription_by_id(sub.subscription_id)
            if not subscription:
                continue
            
            days_left = (sub.expires_at - current_time).days
            
            if days_left <= 0:
                status = "❌ Истекла"
            elif days_left <= sub.auto_pay_days_before:
                status = "⚠️ Скоро продление"
            else:
                status = "✅ Активна"
            
            text += f"📋 {subscription.name}\n"
            text += f"   {status} (через {days_left} дн.)\n"
            text += f"   💰 Цена продления: {subscription.price}₽\n"
            text += f"   📅 Продлять за: {sub.auto_pay_days_before} дн.\n"
            
            if target_user.balance < subscription.price:
                needed = subscription.price - target_user.balance
                text += f"   ⚠️ Нужно еще {needed:.2f}₽\n"
            else:
                text += f"   ✅ Средств достаточно\n"
            
            text += "\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=autopay_user_detail_keyboard(user_id, user.language)
        )
        
    except Exception as e:
        logger.error(f"Error showing autopay user detail: {e}")
        await callback.answer("❌ Ошибка получения информации")

@admin_router.callback_query(F.data == "admin_user_subscriptions_all")
async def admin_user_subscriptions_all_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    await show_user_subscriptions_admin(callback, user, page=0, filter_type="all", **kwargs)

@admin_router.callback_query(F.data == "admin_user_subscriptions_filters")
async def admin_user_subscriptions_filters_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        stats = await db.get_user_subscriptions_stats_admin()
        
        text = f"🔍 Фильтры подписок пользователей\n\n"
        text += f"📊 Статистика:\n"
        text += f"• Всего подписок: {stats['total_subscriptions']}\n"
        text += f"• 🟢 Активных: {stats['active_subscriptions']}\n"
        text += f"• 🔴 Истекших: {stats['expired_subscriptions']}\n"
        text += f"• ⏰ Истекают скоро: {stats['expiring_subscriptions']}\n"
        text += f"• 🔄 С автоплатежом: {stats['autopay_subscriptions']}\n"
        text += f"• 🆓 Триальных: {stats['trial_subscriptions']}\n"
        text += f"• 📦 Импортированных: {stats['imported_subscriptions']}\n\n"
        text += f"Выберите фильтр для просмотра:"
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=admin_user_subscriptions_filters_keyboard(user.language)
            )
        except Exception as edit_error:
            if "message is not modified" in str(edit_error).lower():
                await callback.answer("✅ Фильтры обновлены", show_alert=False)
            else:
                logger.error(f"Error editing filters message: {edit_error}")
                await callback.answer("❌ Ошибка отображения фильтров", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error showing subscriptions filters: {e}")
        await callback.answer("❌ Ошибка загрузки фильтров", show_alert=True)

@admin_router.callback_query(F.data.startswith("filter_subs_"))
async def filter_subscriptions_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    filter_type = callback.data.replace("filter_subs_", "")
    await show_user_subscriptions_admin(callback, user, page=0, filter_type=filter_type, **kwargs)

@admin_router.callback_query(F.data.startswith("user_subs_page_"))
async def user_subscriptions_page_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        parts = callback.data.split("_")
        page = int(parts[3])
        filter_type = parts[4] if len(parts) > 4 else "all"
        
        await show_user_subscriptions_admin(callback, user, page=page, filter_type=filter_type, **kwargs)
        
    except Exception as e:
        logger.error(f"Error in user subscriptions pagination: {e}")
        await callback.answer("❌ Ошибка навигации", show_alert=True)

@admin_router.callback_query(F.data.startswith("refresh_user_subs_"))
async def refresh_user_subscriptions_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    filter_type = callback.data.replace("refresh_user_subs_", "")
    await callback.answer("🔄 Обновляю список...")
    await show_user_subscriptions_admin(callback, user, page=0, filter_type=filter_type, **kwargs)

@admin_router.callback_query(F.data.startswith("admin_user_sub_detail_"))
async def admin_user_subscription_detail_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        subscription_id = int(callback.data.replace("admin_user_sub_detail_", ""))
        
        subscription_detail = await db.get_user_subscription_detail_admin(subscription_id)
        if not subscription_detail:
            await callback.answer("❌ Подписка не найдена", show_alert=True)
            return
        
        def clean_text(text):
            if not text:
                return "N/A"
            return str(text).replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
        
        user_first_name = clean_text(subscription_detail['user_first_name'])
        user_username = clean_text(subscription_detail['user_username'])
        subscription_name = clean_text(subscription_detail['subscription_name'])
        short_uuid = clean_text(subscription_detail['short_uuid'])
        
        text = f"📋 Детали подписки пользователя\n\n"
        
        text += f"👤 Пользователь:\n"
        text += f"├ Имя: {user_first_name}\n"
        text += f"├ Username: @{user_username}\n"
        text += f"├ Telegram ID: {subscription_detail['user_id']}\n"
        text += f"└ Баланс: {subscription_detail['user_balance']:.2f}₽\n\n"
        
        text += f"📦 Подписка:\n"
        text += f"├ Название: {subscription_name}\n"
        text += f"├ Цена: {subscription_detail['subscription_price']}₽\n"
        text += f"├ Длительность: {subscription_detail['subscription_duration']} дн.\n"
        text += f"└ Short UUID: {short_uuid}\n\n"
        
        status_emoji = subscription_detail['status_emoji']
        text += f"🔘 Статус: {status_emoji} "
        
        if subscription_detail['status'] == "active":
            text += f"Активна (осталось {subscription_detail['days_left']} дн.)\n"
        elif subscription_detail['status'] == "expiring_soon":
            text += f"Истекает через {subscription_detail['days_left']} дн.\n"
        elif subscription_detail['status'] == "expired":
            text += "Истекла\n"
        elif subscription_detail['status'] == "inactive":
            text += "Приостановлена\n"
        
        text += f"📅 Временные рамки:\n"
        text += f"├ Создана: {format_datetime(subscription_detail['created_at'], user.language)}\n"
        text += f"├ Истекает: {format_datetime(subscription_detail['expires_at'], user.language)}\n"
        if subscription_detail['updated_at']:
            text += f"└ Обновлена: {format_datetime(subscription_detail['updated_at'], user.language)}\n"
        else:
            text += f"└ Обновлена: Никогда\n"
        
        text += f"\n🔄 Автоплатеж:\n"
        if subscription_detail['auto_pay_enabled']:
            text += f"├ Статус: ✅ Включен\n"
            text += f"└ Продлять за: {subscription_detail['auto_pay_days_before']} дн. до истечения\n"
            
            if subscription_detail['user_balance'] < subscription_detail['subscription_price']:
                needed = subscription_detail['subscription_price'] - subscription_detail['user_balance']
                text += f"⚠️ Недостаточно средств! Нужно еще {needed:.2f}₽\n"
        else:
            text += f"└ Статус: ❌ Отключен\n"
        
        if subscription_detail['is_trial']:
            text += f"\n🆓 Тип: Триальная подписка\n"
        elif subscription_detail['is_imported']:
            text += f"\n📦 Тип: Импортированная подписка\n"
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=admin_user_subscription_detail_keyboard(
                    subscription_id, subscription_detail['user_id'], user.language
                )
            )
        except Exception as edit_error:
            if "message is not modified" in str(edit_error).lower():
                await callback.answer("✅ Информация актуальна", show_alert=False)
            else:
                logger.error(f"Error editing detail message: {edit_error}")
                await callback.answer("❌ Ошибка отображения деталей", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error showing user subscription detail: {e}")
        await callback.answer("❌ Ошибка загрузки деталей", show_alert=True)


async def show_user_subscriptions_admin(callback: CallbackQuery, user: User, page: int = 0, 
                                      filter_type: str = "all", **kwargs):
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        page_size = 10
        offset = page * page_size
        
        subscriptions_data, total_count = await db.get_all_user_subscriptions_admin(
            offset=offset, limit=page_size, filter_type=filter_type
        )
        
        if not subscriptions_data and page == 0:
            filter_names = {
                "all": "подписок",
                "active": "активных подписок",
                "expired": "истекших подписок",
                "expiring": "истекающих подписок",
                "autopay": "подписок с автоплатежом",
                "trial": "триальных подписок",
                "imported": "импортированных подписок"
            }
            
            await callback.message.edit_text(
                f"📋 Список {filter_names.get(filter_type, 'подписок')} пуст",
                reply_markup=admin_user_subscriptions_filters_keyboard(user.language)
            )
            return
        
        if not subscriptions_data and page > 0:
            await show_user_subscriptions_admin(callback, user, page - 1, filter_type, **kwargs)
            return
        
        filter_titles = {
            "all": "Все подписки пользователей",
            "active": "Активные подписки",
            "expired": "Истекшие подписки", 
            "expiring": "Истекающие подписки",
            "autopay": "Подписки с автоплатежом",
            "trial": "Триальные подписки",
            "imported": "Импортированные подписки"
        }
        
        total_pages = (total_count + page_size - 1) // page_size
        
        text = f"📋 {filter_titles.get(filter_type, 'Подписки')}\n"
        text += f"📄 Страница {page + 1} из {total_pages} • Всего: {total_count}\n\n"
        
        for i, sub_data in enumerate(subscriptions_data, start=offset + 1):
            status_emojis = {
                "active": "🟢",
                "expiring": "🟡", 
                "expiring_soon": "🚨",
                "expired": "❌",
                "inactive": "⏸"
            }
            status_emoji = status_emojis.get(sub_data['status'], "⚪")
            
            user_display = sub_data['user_first_name'] or "Unknown"
            user_display = user_display.replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
            
            if sub_data['user_username'] != 'N/A':
                clean_username = sub_data['user_username'].replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
                user_display += f" (@{clean_username})"
            
            subscription_name = sub_data['subscription_name'].replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
            
            text += f"{i}. {status_emoji} {user_display}\n"
            text += f"   📋 {subscription_name} — {sub_data['subscription_price']}₽\n"
            text += f"   📅 Создана: {format_datetime(sub_data['created_at'], user.language)}\n"
            
            if sub_data['status'] == "active":
                text += f"   ⏰ Истекает через {sub_data['days_left']} дн.\n"
            elif sub_data['status'] in ["expiring", "expiring_soon"]:
                text += f"   ⚠️ Истекает через {sub_data['days_left']} дн.\n"
            elif sub_data['status'] == "expired":
                text += f"   ❌ Истекла\n"
            elif sub_data['status'] == "inactive":
                text += f"   ⏸ Приостановлена\n"
            
            if sub_data['auto_pay_enabled']:
                text += f"   🔄 Автоплатеж: за {sub_data['auto_pay_days_before']} дн.\n"
            
            labels = []
            if sub_data['is_trial']:
                labels.append("🆓 Trial")
            if sub_data['is_imported']:
                labels.append("📦 Import")
            
            if labels:
                text += f"   🏷 {' • '.join(labels)}\n"
            
            text += "\n"
        
        additional_buttons = []
        if len(subscriptions_data) <= 5:
            for sub_data in subscriptions_data:
                user_name = (sub_data['user_first_name'] or "User")[:10]
                user_name = user_name.replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
                if len(user_name) > 10:
                    user_name = user_name[:7] + "..."
                
                additional_buttons.append([
                    InlineKeyboardButton(
                        text=f"👤 {user_name}",
                        callback_data=f"admin_user_sub_detail_{sub_data['id']}"
                    )
                ])
            
            if additional_buttons:
                text += "👆 Нажмите на кнопку для просмотра деталей:"
        
        keyboard = user_subscriptions_pagination_keyboard(page, total_pages, filter_type, user.language)
        
        if additional_buttons:
            nav_buttons = keyboard.inline_keyboard[0] if keyboard.inline_keyboard else []
            other_buttons = keyboard.inline_keyboard[1:] if len(keyboard.inline_keyboard) > 1 else []
            
            new_keyboard_buttons = []
            if nav_buttons:
                new_keyboard_buttons.append(nav_buttons)
            
            for i in range(0, len(additional_buttons), 2):
                row = []
                for j in range(2):
                    if i + j < len(additional_buttons):
                        row.extend(additional_buttons[i + j])
                if row:
                    new_keyboard_buttons.append(row)
            
            new_keyboard_buttons.extend(other_buttons)
            keyboard = InlineKeyboardMarkup(inline_keyboard=new_keyboard_buttons)
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=keyboard
            )
        except Exception as edit_error:
            if "message is not modified" in str(edit_error).lower():
                await callback.answer("✅ Список обновлен", show_alert=False)
            else:
                logger.error(f"Error editing message: {edit_error}")
                try:
                    await callback.message.answer(
                        text,
                        reply_markup=keyboard
                    )
                except Exception as send_error:
                    logger.error(f"Error sending new message: {send_error}")
                    await callback.answer("❌ Ошибка отображения", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error showing user subscriptions admin: {e}")
        try:
            await callback.message.edit_text(
                "❌ Ошибка загрузки подписок",
                reply_markup=admin_user_subscriptions_filters_keyboard(user.language)
            )
        except:
            await callback.answer("❌ Ошибка загрузки подписок", show_alert=True)


@admin_router.callback_query(F.data.startswith("edit_user_sub_"))
async def edit_user_subscription_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    db = kwargs.get('db')
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        subscription_id = int(callback.data.replace("edit_user_sub_", ""))
        
        subscription_detail = await db.get_user_subscription_detail_admin(subscription_id)
        if not subscription_detail:
            await callback.answer("❌ Подписка не найдена", show_alert=True)
            return
        
        text = f"✏️ **Редактирование подписки**\n\n"
        text += f"👤 Пользователь: {subscription_detail['user_first_name']}\n"
        text += f"📋 Подписка: {subscription_detail['subscription_name']}\n\n"
        text += f"Что вы хотите изменить?"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Срок действия", callback_data=f"edit_sub_expiry_{subscription_id}"),
                InlineKeyboardButton(text="🔘 Статус", callback_data=f"toggle_sub_status_{subscription_id}")
            ],
            [
                InlineKeyboardButton(text="🔄 Автоплатеж", callback_data=f"edit_sub_autopay_{subscription_id}"),
                InlineKeyboardButton(text="📊 Трафик", callback_data=f"edit_sub_traffic_{subscription_id}")
            ],
            [InlineKeyboardButton(text="🔙 К деталям", callback_data=f"admin_user_sub_detail_{subscription_id}")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error editing user subscription: {e}")
        await callback.answer("❌ Ошибка редактирования", show_alert=True)

@admin_router.callback_query(F.data.startswith("refresh_user_sub_"))
async def refresh_user_subscription_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    subscription_id = callback.data.replace("refresh_user_sub_", "")
    await callback.answer("🔄 Обновляю информацию...")
    
    new_callback_data = f"admin_user_sub_detail_{subscription_id}"
    callback.data = new_callback_data
    await admin_user_subscription_detail_callback(callback, user, **kwargs)

@admin_router.callback_query(F.data.startswith("edit_sub_traffic_"))
async def edit_sub_traffic_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        subscription_id = int(callback.data.split("_")[-1]) 
        
        db = kwargs.get('db')
        if not db:
            await callback.answer("❌ База данных недоступна", show_alert=True)
            return
        
        subscription_detail = await db.get_user_subscription_detail_admin(subscription_id)
        if not subscription_detail:
            await callback.answer("❌ Подписка не найдена", show_alert=True)
            return
        
        text = f"📊 **Изменение лимита трафика**\n\n"
        text += f"👤 Пользователь: {subscription_detail['user_first_name']}\n"
        text += f"📋 Подписка: {subscription_detail['subscription_name']}\n\n"
        text += f"Введите новый лимит трафика в ГБ (0 = безлимит):"
        
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К деталям", callback_data=f"admin_user_sub_detail_{subscription_id}")]
            ]),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error editing subscription traffic: {e}")
        await callback.answer("❌ Ошибка редактирования", show_alert=True)

@admin_router.callback_query(F.data.startswith("edit_sub_expiry_"))
async def edit_sub_expiry_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        subscription_id = int(callback.data.split("_")[-1])
        
        db = kwargs.get('db')
        if not db:
            await callback.answer("❌ База данных недоступна", show_alert=True)
            return
        
        subscription_detail = await db.get_user_subscription_detail_admin(subscription_id)
        if not subscription_detail:
            await callback.answer("❌ Подписка не найдена", show_alert=True)
            return
        
        text = f"📅 **Изменение срока действия**\n\n"
        text += f"👤 Пользователь: {subscription_detail['user_first_name']}\n"
        text += f"📋 Подписка: {subscription_detail['subscription_name']}\n\n"
        text += f"Введите новую дату истечения (YYYY-MM-DD) или количество дней:"
        
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К деталям", callback_data=f"admin_user_sub_detail_{subscription_id}")]
            ]),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error editing subscription expiry: {e}")
        await callback.answer("❌ Ошибка редактирования", show_alert=True)

@admin_router.callback_query(F.data.startswith("toggle_sub_status_"))
async def toggle_subscription_status_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        subscription_id = int(callback.data.split("_")[-1])
        
        db = kwargs.get('db')
        if not db:
            await callback.answer("❌ База данных недоступна", show_alert=True)
            return
        
        async with db.session_factory() as session:
            from sqlalchemy import select, update
            from database import UserSubscription
            
            result = await session.execute(
                select(UserSubscription).where(UserSubscription.id == subscription_id)
            )
            user_subscription = result.scalar_one_or_none()
            
            if not user_subscription:
                await callback.answer("❌ Подписка не найдена", show_alert=True)
                return
            
            new_status = not user_subscription.is_active
            
            await session.execute(
                update(UserSubscription)
                .where(UserSubscription.id == subscription_id)
                .values(is_active=new_status)
            )
            await session.commit()
            
            status_text = "активирована" if new_status else "деактивирована"
            await callback.answer(f"✅ Подписка {status_text}")
            
            log_user_action(user.telegram_id, "subscription_status_toggled", 
                          f"SubID: {subscription_id}, Active: {new_status}")
            
            await admin_user_subscription_detail_callback(callback, user, **kwargs)
        
    except Exception as e:
        logger.error(f"Error toggling subscription status: {e}")
        await callback.answer("❌ Ошибка изменения статуса", show_alert=True)

@admin_router.callback_query(F.data.startswith("edit_sub_autopay_"))
async def edit_sub_autopay_callback(callback: CallbackQuery, user: User, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        subscription_id = int(callback.data.split("_")[-1]) 
        
        db = kwargs.get('db')
        if not db:
            await callback.answer("❌ База данных недоступна", show_alert=True)
            return
        
        subscription_detail = await db.get_user_subscription_detail_admin(subscription_id)
        if not subscription_detail:
            await callback.answer("❌ Подписка не найдена", show_alert=True)
            return
        
        current_autopay = subscription_detail['auto_pay_enabled']
        autopay_days = subscription_detail['auto_pay_days_before']
        
        text = f"🔄 **Настройки автоплатежа**\n\n"
        text += f"👤 Пользователь: {subscription_detail['user_first_name']}\n"
        text += f"📋 Подписка: {subscription_detail['subscription_name']}\n\n"
        text += f"Текущее состояние: {'✅ Включен' if current_autopay else '❌ Отключен'}\n"
        if current_autopay:
            text += f"Продлевать за: {autopay_days} дней до истечения\n\n"
        
        buttons = []
        if current_autopay:
            buttons.append([InlineKeyboardButton(text="❌ Отключить автоплатеж", callback_data=f"disable_autopay_{subscription_id}")])
            buttons.append([InlineKeyboardButton(text="📅 Изменить дни", callback_data=f"change_autopay_days_{subscription_id}")])
        else:
            buttons.append([InlineKeyboardButton(text="✅ Включить автоплатеж", callback_data=f"enable_autopay_{subscription_id}")])
        
        buttons.append([InlineKeyboardButton(text="🔙 К деталям", callback_data=f"admin_user_sub_detail_{subscription_id}")])
        
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error editing subscription autopay: {e}")
        await callback.answer("❌ Ошибка редактирования", show_alert=True)

@admin_router.callback_query(F.data == "lucky_game_admin_details")
async def lucky_game_admin_details_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    if not await check_admin_access(callback, user):
        return
    
    try:
        lucky_stats = await db.get_lucky_game_admin_stats()
        top_players = await db.get_lucky_game_top_players(5)
        
        text = "🎰 **Детальная статистика игры в удачу**\n\n"
        
        if lucky_stats and lucky_stats.get('total_games', 0) > 0:
            text += "📊 **Общая статистика:**\n"
            text += f"🎲 Всего игр сыграно: {lucky_stats['total_games']}\n"
            text += f"🏆 Всего выигрышей: {lucky_stats['total_wins']}\n"
            text += f"📈 Процент побед: {lucky_stats['win_rate']:.2f}%\n"
            text += f"👥 Уникальных игроков: {lucky_stats['unique_players']}\n"
            text += f"💎 Общая сумма выплат: {lucky_stats['total_rewards']:.0f}₽\n"
            
            if lucky_stats.get('avg_reward', 0) > 0:
                text += f"💰 Средняя выплата: {lucky_stats['avg_reward']:.1f}₽\n"
            text += "\n"
            
            text += "📅 **За сегодня:**\n"
            text += f"🎯 Игр: {lucky_stats.get('games_today', 0)}\n"
            text += f"🎉 Выигрышей: {lucky_stats.get('wins_today', 0)}\n"
            if lucky_stats.get('games_today', 0) > 0:
                text += f"📊 Процент побед: {lucky_stats.get('win_rate_today', 0):.1f}%\n"
            text += "\n"
            
            if top_players:
                text += "🏆 **Топ-5 игроков:**\n"
                for i, player in enumerate(top_players, 1):
                    name = player.get('first_name', 'Unknown')
                    if not name or name == 'Unknown':
                        name = player.get('username', 'N/A')
                    
                    text += f"{i}. {name}\n"
                    text += f"   💰 Выиграл: {player.get('total_won', 0):.0f}₽\n"
                    text += f"   🎯 Игр: {player.get('games_played', 0)} | "
                    text += f"Побед: {player.get('wins', 0)} ({player.get('win_rate', 0):.1f}%)\n"
                    
                    if player.get('last_game'):
                        try:
                            if isinstance(player['last_game'], str):
                                last_game_dt = datetime.fromisoformat(player['last_game']).replace(tzinfo=None)
                            else:
                                last_game_dt = player['last_game']
                            
                            last_game = format_datetime(last_game_dt, user.language)
                            text += f"   🕐 Последняя игра: {last_game}\n"
                        except Exception as e:
                            logger.warning(f"Error formatting last_game: {e}")
                            text += f"   🕐 Последняя игра: {str(player['last_game'])[:16]}\n"
                    text += "\n"
            
            first_game = lucky_stats.get('first_game')
            last_game = lucky_stats.get('last_game')
            
            if first_game and last_game:
                text += f"🕐 **Временные рамки:**\n"
                try:
                    if isinstance(first_game, str):
                        first_game_dt = datetime.fromisoformat(first_game).replace(tzinfo=None)
                    else:
                        first_game_dt = first_game
                    
                    if isinstance(last_game, str):
                        last_game_dt = datetime.fromisoformat(last_game).replace(tzinfo=None)
                    else:
                        last_game_dt = last_game
                    
                    first_game_str = format_datetime(first_game_dt, user.language)
                    last_game_str = format_datetime(last_game_dt, user.language)
                    
                    text += f"🥇 Первая игра: {first_game_str}\n"
                    text += f"🕐 Последняя игра: {last_game_str}\n\n"
                except Exception as e:
                    logger.warning(f"Error formatting game times: {e}")
                    text += f"🥇 Первая игра: {str(first_game)[:16]}\n"
                    text += f"🕐 Последняя игра: {str(last_game)[:16]}\n\n"
            
        else:
            text += "🎯 В игру в удачу еще никто не играл.\n\n"
            text += "Игроки смогут играть после активации функции в боте.\n\n"
        
        current_time = datetime.now()
        text += f"🕕 _Обновлено: {format_datetime(current_time, user.language)}_"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="lucky_game_admin_details")],
            [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error getting lucky game admin details: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения детальной статистики игры",
            reply_markup=back_keyboard("admin_stats", user.language)
        )
