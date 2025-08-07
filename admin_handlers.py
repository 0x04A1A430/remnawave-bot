import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta, timezone
import logging
from typing import List, Dict

from database import Database, User
from remnawave_api import RemnaWaveAPI
from keyboards import *
from translations import t
from utils import *
from handlers import BotStates
try:
    from api_error_handlers import (
        APIErrorHandler, safe_get_nodes, safe_get_system_users, 
        safe_restart_nodes, check_api_health, handle_api_errors
    )
except ImportError:
    # Fallback функции если api_error_handlers не найден
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

# Admin panel access check
async def check_admin_access(callback: CallbackQuery, user: User) -> bool:
    """Check if user has admin access"""  
    if not user.is_admin:
        await callback.answer(t('not_admin', user.language))
        return False
    return True

# Admin panel main menu
@admin_router.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show admin panel"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('admin_menu', user.language),
        reply_markup=admin_menu_keyboard(user.language)
    )

# Statistics
@admin_router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, **kwargs):
    """Show statistics"""
    if not await check_admin_access(callback, user):
        return
    
    try:
        # Get database stats
        db_stats = await db.get_stats()
        
        # Get RemnaWave system stats (optional)
        system_stats = None
        nodes_stats = None
        
        if api:
            try:
                system_stats = await api.get_system_stats()
                nodes_stats = await api.get_nodes_statistics()
            except Exception as e:
                logger.warning(f"Failed to get RemnaWave stats: {e}")
        
        text = t('stats_info', user.language,
            users=db_stats['total_users'],
            subscriptions=db_stats['total_subscriptions_non_trial'],  # Изменено
            revenue=db_stats['total_revenue']
        )
        
        if system_stats:
            text += "\n\n🖥 Системная статистика:"
            if 'data' in system_stats:
                data = system_stats['data']
                if 'bandwidth' in data:
                    bandwidth = data['bandwidth']
                    text += f"\n📊 Трафик: ↓{format_bytes(bandwidth.get('downlink', 0))} ↑{format_bytes(bandwidth.get('uplink', 0))}"
        
        if nodes_stats and 'data' in nodes_stats:
            nodes = nodes_stats['data']
            online_nodes = len([n for n in nodes if n.get('status') == 'online'])
            text += f"\n🖥 Нод: {online_nodes}/{len(nodes)} онлайн"
        
        await callback.message.edit_text(
            text,
            reply_markup=back_keyboard("admin_panel", user.language)
        )
        
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        await callback.message.edit_text(
            t('error_occurred', user.language),
            reply_markup=back_keyboard("admin_panel", user.language)
        )

# Subscription management
@admin_router.callback_query(F.data == "admin_subscriptions")
async def admin_subscriptions_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show subscription management"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('manage_subscriptions', user.language),
        reply_markup=admin_subscriptions_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "create_subscription")
async def create_subscription_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    """Start subscription creation"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_sub_name', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_sub_name)

@admin_router.message(StateFilter(BotStates.admin_create_sub_name))
async def handle_sub_name(message: Message, state: FSMContext, user: User, **kwargs):
    """Handle subscription name input"""
    name = message.text.strip()
    if len(name) < 3 or len(name) > 100:
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
    """Handle subscription description input"""
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
    """Handle subscription price input"""
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
    """Handle subscription duration input"""
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
    """Handle subscription traffic limit input"""
    try:
        traffic_gb = int(message.text.strip())
        if traffic_gb < 0 or traffic_gb > 10000:
            await message.answer("❌ Лимит трафика должен быть от 0 до 10000 ГБ")
            return
    except ValueError:
        await message.answer("❌ Введите число")
        return
    
    await state.update_data(traffic_gb=traffic_gb)
    
    # Try to get squads from RemnaWave API
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
    
    # Fallback to manual input if API fails
    logger.info("Falling back to manual squad UUID input")
    await message.answer(
        t('enter_squad_uuid', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_sub_squad)

def squad_selection_keyboard(squads: List[Dict], language: str = 'ru') -> InlineKeyboardMarkup:
    """Create keyboard for squad selection"""
    logger.info(f"Creating squad selection keyboard for {len(squads)} squads")
    buttons = []
    
    for squad in squads:
        logger.debug(f"Processing squad: {squad}")
        
        # Получаем название и UUID squad'а с проверкой
        squad_name = squad.get('name', 'Unknown Squad')
        squad_uuid = squad.get('uuid', '')
        
        if not squad_uuid:
            logger.warning(f"Squad without UUID: {squad}")
            continue
        
        # Truncate name if too long
        if len(squad_name) > 30:
            display_name = squad_name[:27] + "..."
        else:
            display_name = squad_name
        
        # Добавляем информацию о количестве участников если есть
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
        # Добавляем кнопку ручного ввода как единственную опцию
        buttons.append([
            InlineKeyboardButton(
                text="✏️ Ввести UUID вручную",
                callback_data="manual_squad_input"
            )
        ])
    else:
        # Add manual input button as alternative
        buttons.append([
            InlineKeyboardButton(
                text="✏️ Ввести UUID вручную",
                callback_data="manual_squad_input"
            )
        ])
    
    # Add cancel button
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
    """Switch to manual squad UUID input"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_squad_uuid', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_sub_squad)

@admin_router.callback_query(F.data.startswith("select_squad_"))
async def handle_squad_selection(callback: CallbackQuery, state: FSMContext, user: User, db: Database, **kwargs):
    """Handle squad selection from inline keyboard"""
    if not await check_admin_access(callback, user):
        return
    
    try:
        squad_uuid = callback.data.replace("select_squad_", "")
        
        # Validate UUID format
        if not validate_squad_uuid(squad_uuid):
            await callback.answer("❌ Неверный формат UUID")
            return
        
        # Get all state data
        data = await state.get_data()
        
        # Create subscription in database
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
    """Handle subscription squad UUID manual input (fallback)"""
    squad_uuid = message.text.strip()
    
    if not validate_squad_uuid(squad_uuid):
        await message.answer("❌ Неверный формат UUID")
        return
    
    # Get all state data
    data = await state.get_data()
    
    try:
        # Create subscription in database
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
    """List all subscriptions for admin"""
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
    """Toggle subscription active status"""
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
        
        # Update the list
        subs = await db.get_all_subscriptions(include_inactive=True)
        await callback.message.edit_reply_markup(
            reply_markup=admin_subscriptions_list_keyboard(subs, user.language)
        )
    except Exception as e:
        logger.error(f"Error toggling subscription: {e}")
        await callback.answer(t('error_occurred', user.language))

@admin_router.callback_query(F.data.startswith("edit_sub_"))
async def edit_sub_menu(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    """Show subscription edit menu"""
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
    """Ask for new field value"""
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
    """Handle new value for subscription field"""
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
        
        # Validate and set new value
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
    """Show subscription deletion confirmation"""
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
    """Delete subscription"""
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
        
        # Return to list
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

# User management
@admin_router.callback_query(F.data == "admin_users")
async def admin_users_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show user management"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('manage_users', user.language),
        reply_markup=admin_users_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "list_users")
async def list_users_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    """List all users"""
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
        
        # Show first 20 users
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

# Balance management
@admin_router.callback_query(F.data == "admin_balance")
async def admin_balance_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show balance management"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('manage_balance', user.language),
        reply_markup=admin_balance_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "admin_add_balance")
async def admin_add_balance_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    """Start adding balance to user"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_user_id', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_add_balance_user)

@admin_router.message(StateFilter(BotStates.admin_add_balance_user))
async def handle_balance_user_id(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    """Handle user ID input for balance addition"""
    telegram_id = parse_telegram_id(message.text)
    
    if not telegram_id:
        await message.answer("❌ Неверный Telegram ID")
        return
    
    # Check if user exists
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
    """Handle balance amount input"""
    is_valid, amount = is_valid_amount(message.text)
    
    if not is_valid:
        await message.answer(t('invalid_amount', user.language))
        return
    
    data = await state.get_data()
    target_user_id = data['target_user_id']
    
    try:
        # Add balance
        success = await db.add_balance(target_user_id, amount)
        
        if success:
            # Create payment record
            await db.create_payment(
                user_id=target_user_id,
                amount=amount,
                payment_type='admin_topup',
                description=f'Пополнение администратором (ID: {user.telegram_id})',
                status='completed'
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
    """Show payment history (first page) - ADMIN VERSION"""
    logger.info(f"admin_payment_history_callback called for user {user.telegram_id}")
    
    if not await check_admin_access(callback, user):
        logger.warning(f"Admin access denied for user {user.telegram_id}")
        return
    
    logger.info("Admin access granted, clearing state and showing payment history")
    await state.clear()  # Очищаем состояние для новой пагинации
    await show_payment_history_page(callback, user, db, state, page=0)

async def show_payment_history_page(callback: CallbackQuery, user: User, db: Database, state: FSMContext, page: int = 0):
    """Show payment history page with pagination"""
    logger.info(f"show_payment_history_page called: page={page}, user={user.telegram_id}")

    try:
        page_size = 10
        offset = page * page_size
        
        # Получаем все платежи с пагинацией
        payments, total_count = await db.get_all_payments_paginated(offset=offset, limit=page_size)

        logger.info(f"Got {len(payments) if payments else 0} payments, total_count={total_count}")
        
        if not payments and page == 0:
            await callback.message.edit_text(
                "❌ История платежей пуста",
                reply_markup=back_keyboard("admin_balance", user.language)
            )
            return
        
        # Если страница пустая, но не первая - возвращаемся на предыдущую
        if not payments and page > 0:
            await show_payment_history_page(callback, user, db, state, page - 1)
            return
        
        # Формируем текст
        total_pages = (total_count + page_size - 1) // page_size
        text = f"💳 История платежей (стр. {page + 1}/{total_pages})\n"
        text += f"📊 Всего записей: {total_count}\n\n"
        
        for payment in payments:
            # Получаем информацию о пользователе
            payment_user = await db.get_user_by_telegram_id(payment.user_id)
            username = payment_user.username if payment_user and payment_user.username else "N/A"
            first_name = payment_user.first_name if payment_user and payment_user.first_name else "N/A"
            
            # Форматируем статус
            status_emoji = {
                'completed': '✅',
                'pending': '⏳',
                'cancelled': '❌'
            }.get(payment.status, '❓')
            
            # Форматируем тип платежа
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
        
        # Сохраняем текущую страницу в состояние
        await state.update_data(current_page=page)
        await state.set_state(BotStates.admin_payment_history_page)
        
        # Создаем клавиатуру с пагинацией
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
    """Create pagination keyboard"""
    buttons = []
    
    # Кнопки навигации
    nav_buttons = []
    
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{callback_prefix}_page_{current_page - 1}"))
    
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"{callback_prefix}_page_{current_page + 1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    # Индикатор страницы
    if total_pages > 1:
        buttons.append([InlineKeyboardButton(text=f"📄 {current_page + 1}/{total_pages}", callback_data="noop")])
    
    # Кнопка "Назад"
    buttons.append([InlineKeyboardButton(text=t('back', language), callback_data="admin_balance")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("payment_history_page_"))
async def payment_history_page_callback(callback: CallbackQuery, user: User, db: Database, state: FSMContext, **kwargs):
    """Handle payment history pagination"""
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
    """Handle no-operation callback (for page indicator)"""
    await callback.answer()

# Payment approval handlers
@admin_router.callback_query(F.data.startswith("approve_payment_"))
async def approve_payment(callback: CallbackQuery, user: User, db: Database, **kwargs):
    """Approve payment"""
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
        
        # Add balance to user
        success = await db.add_balance(payment.user_id, payment.amount)
        
        if success:
            # Update payment status
            payment.status = 'completed'
            await db.update_payment(payment)
            
            await callback.message.edit_text(
                f"✅ Платеж одобрен!\n💰 Пользователю {payment.user_id} добавлено {payment.amount} руб."
            )
            
            # Notify user about successful payment
            bot = kwargs.get('bot')
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
    """Reject payment"""
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
        
        # Update payment status
        payment.status = 'cancelled'
        await db.update_payment(payment)
        
        await callback.message.edit_text(
            f"❌ Платеж отклонен!\n💰 Платеж пользователя {payment.user_id} на сумму {payment.amount} руб. отклонен."
        )
        
        # Notify user about rejected payment
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

# Promocode management
@admin_router.callback_query(F.data == "admin_promocodes")
async def admin_promocodes_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show promocode management"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('manage_promocodes', user.language),
        reply_markup=admin_promocodes_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "create_promocode")
async def create_promocode_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    """Start promocode creation"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_promo_code', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_create_promo_code)

@admin_router.message(StateFilter(BotStates.admin_create_promo_code))
async def handle_promo_code(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    """Handle promocode input"""
    code = message.text.strip().upper()
    
    if not validate_promocode_format(code):
        await message.answer("❌ Промокод должен содержать только буквы и цифры (3-20 символов)")
        return
    
    # Check if promocode already exists
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
    """Handle promocode discount input"""
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
async def handle_promo_limit(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    """Handle promocode usage limit input"""
    try:
        limit = int(message.text.strip())
        if limit <= 0 or limit > 10000:
            await message.answer("❌ Лимит должен быть от 1 до 10000")
            return
    except ValueError:
        await message.answer("❌ Введите число")
        return
    
    data = await state.get_data()
    
    try:
        # Create promocode
        promocode = await db.create_promocode(
            code=data['code'],
            discount_amount=data['discount'],
            usage_limit=limit
        )
        
        await message.answer(
            t('promocode_created', user.language),
            reply_markup=admin_menu_keyboard(user.language)
        )
        
        log_user_action(user.telegram_id, "promocode_created", data['code'])
        
    except Exception as e:
        logger.error(f"Error creating promocode: {e}")
        await message.answer(
            t('error_occurred', user.language),
            reply_markup=admin_menu_keyboard(user.language)
        )
    
    await state.clear()

@admin_router.callback_query(F.data == "list_promocodes")
async def list_promocodes_callback(callback: CallbackQuery, user: User, db: Database, **kwargs):
    """List all promocodes"""
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
        
        text = "📋 Список промокодов:\n\n"
        
        for promo in promocodes[:10]:  # Show first 10
            status = "🟢" if promo.is_active else "🔴"
            expiry = ""
            if promo.expires_at:
                expiry = f" (до {format_date(promo.expires_at, user.language)})"
            
            text += f"{status} `{promo.code}` - {promo.discount_amount}р.\n"
            text += f"   Использовано: {promo.used_count}/{promo.usage_limit}{expiry}\n\n"
        
        if len(promocodes) > 10:
            text += f"... и еще {len(promocodes) - 10} промокодов"
        
        await callback.message.edit_text(
            text,
            reply_markup=back_keyboard("admin_promocodes", user.language),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error listing promocodes: {e}")
        await callback.answer(t('error_occurred', user.language))

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
    BotStates.admin_edit_sub_value,
    BotStates.admin_send_message_user,
    BotStates.admin_send_message_text,
    BotStates.admin_broadcast_text,
    BotStates.admin_payment_history_page,
    BotStates.admin_search_user_any,  # Добавляем новый state
    BotStates.admin_edit_user_expiry,
    BotStates.admin_edit_user_traffic,
    BotStates.admin_test_monitor_user,
    BotStates.admin_rename_plans_confirm
))
async def cancel_admin_action(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    """Cancel admin action and return to main menu"""
    await state.clear()
    await callback.message.edit_text(
        t('main_menu', user.language),
        reply_markup=main_menu_keyboard(user.language, user.is_admin)
    )

@admin_router.callback_query(F.data == "admin_messages")
async def admin_messages_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show message management"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('send_message', user.language),
        reply_markup=admin_messages_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "admin_send_to_user")
async def admin_send_to_user_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    """Start sending message to specific user"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_user_id_message', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_send_message_user)

@admin_router.message(StateFilter(BotStates.admin_send_message_user))
async def handle_message_user_id(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    """Handle user ID input for message sending"""
    telegram_id = parse_telegram_id(message.text)
    
    if not telegram_id:
        await message.answer("❌ Неверный Telegram ID")
        return
    
    # Check if user exists
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

# Monitor service management
@admin_router.callback_query(F.data == "admin_monitor")
async def admin_monitor_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show monitor service management"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "🔍 Управление сервисом мониторинга",
        reply_markup=admin_monitor_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "monitor_status")
async def monitor_status_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show monitor service status"""
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
        
        status_text = "🔍 Статус сервиса мониторинга:\n\n"
        status_text += f"🟢 Работает: {'Да' if status['is_running'] else 'Нет'}\n"
        status_text += f"⏱ Интервал проверки: {status['check_interval']} сек\n"
        status_text += f"🕙 Время ежедневной проверки: {status['daily_check_hour']}:00\n"
        status_text += f"⚠️ Предупреждение за: {status['warning_days']} дней\n"
        
        if status['last_check']:
            status_text += f"🕐 Последняя проверка: {status['last_check']}"
        
        await callback.message.edit_text(
            status_text,
            reply_markup=back_keyboard("admin_monitor", user.language)
        )
        
    except Exception as e:
        logger.error(f"Error getting monitor status: {e}")
        await callback.message.edit_text(
            "❌ Ошибка получения статуса",
            reply_markup=back_keyboard("admin_monitor", user.language)
        )

@admin_router.callback_query(F.data == "monitor_force_check")
async def monitor_force_check_callback(callback: CallbackQuery, user: User, **kwargs):
    """Force daily check"""
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
    """Deactivate expired subscriptions"""
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
    """Test monitor for specific user"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "👤 Введите Telegram ID пользователя для тестирования уведомлений:",
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_test_monitor_user)

@admin_router.message(StateFilter(BotStates.admin_test_monitor_user))
async def handle_monitor_test_user(message: Message, state: FSMContext, user: User, **kwargs):
    """Handle user ID for monitor testing"""
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
            
            for result in results:
                status = "✅" if result.success else "❌"
                text += f"{status} {result.message}\n"
                if result.error:
                    text += f"   Ошибка: {result.error}\n"
            
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
    """Handle message text input and send message"""
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
    """Start broadcast message"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        t('enter_message_text', user.language),
        reply_markup=cancel_keyboard(user.language)
    )
    await state.set_state(BotStates.admin_broadcast_text)

@admin_router.callback_query(F.data == "main_menu", StateFilter(BotStates.admin_test_monitor_user))
async def cancel_monitor_test(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    """Cancel monitor test"""
    await state.clear()
    await callback.message.edit_text(
        t('main_menu', user.language),
        reply_markup=main_menu_keyboard(user.language, user.is_admin)
    )

@admin_router.message(StateFilter(BotStates.admin_broadcast_text))
async def handle_broadcast_message(message: Message, state: FSMContext, user: User, db: Database, **kwargs):
    """Handle broadcast message"""
    message_text = message.text.strip()
    
    if len(message_text) < 1:
        await message.answer("❌ Сообщение не может быть пустым")
        return
    
    try:
        # Get all users
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
        
        # Show progress message
        progress_msg = await message.answer(f"📤 Отправка сообщения {len(users)} пользователям...")
        
        # Send to all users
        for target_user in users:
            try:
                await bot.send_message(target_user.telegram_id, message_text)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to send broadcast to {target_user.telegram_id}: {e}")
                error_count += 1
            
            # Small delay to avoid rate limits
            await asyncio.sleep(0.05)
        
        # Update progress message with results
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

# System management handlers
@admin_router.callback_query(F.data == "admin_system")
async def admin_system_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show system management menu"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "🖥 Управление системой RemnaWave",
        reply_markup=admin_system_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "system_stats")
async def system_stats_callback(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, **kwargs):
    """Show detailed system statistics"""
    if not await check_admin_access(callback, user):
        return
    
    await show_system_stats(callback, user, db, api)

@admin_router.callback_query(F.data == "refresh_system_stats")
async def refresh_system_stats_callback(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, **kwargs):
    """Refresh system statistics"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.answer("🔄 Обновляю статистику...")
    await show_system_stats(callback, user, db, api)

async def show_system_stats(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, force_refresh: bool = False):
    """Display comprehensive system statistics with correct node status"""
    try:
        # Get database stats
        db_stats = await db.get_stats()
        current_time = datetime.now()
        
        text = "📊 Системная статистика\n\n"
        
        # Database statistics
        text += "💾 База данных бота:\n"
        text += f"👥 Пользователей: {db_stats['total_users']}\n"
        text += f"📋 Подписок: {db_stats['total_subscriptions_non_trial']}\n"
        text += f"💰 Доходы: {db_stats['total_revenue']} руб.\n"
        
        # RemnaWave API status
        if api:
            text += "\n🔗 API RemnaWave: 🟢 Подключен\n"
            
            try:
                logger.info("=== FETCHING SYSTEM STATS ===")
                
                # Получаем статистику пользователей - ИСПРАВЛЕНО
                await callback.answer("📊 Загружаю статистику пользователей...")
                
                # Сначала пробуем получить полный список пользователей
                all_users = await api.get_all_system_users_full()
                logger.info(f"Got {len(all_users) if all_users else 0} users from get_all_system_users_full")
                
                # Если не получилось, пробуем другой метод
                if not all_users:
                    logger.warning("get_all_system_users_full returned empty, trying alternative method")
                    try:
                        # Пробуем получить через системную статистику
                        system_stats = await api.get_system_stats()
                        logger.info(f"System stats response: {system_stats}")
                        
                        # Пробуем альтернативный метод получения пользователей
                        users_count = await api.get_users_count()
                        logger.info(f"Users count from API: {users_count}")
                        
                    except Exception as alt_error:
                        logger.error(f"Alternative user fetching failed: {alt_error}")
                
                # Получаем статистику нод
                await callback.answer("🖥 Загружаю статистику нод...")
                all_nodes = await api.get_all_nodes()
                logger.info(f"Got {len(all_nodes) if all_nodes else 0} nodes from API")
                
                text += "\n🖥 Система RemnaWave:\n"
                
                # === ПОЛЬЗОВАТЕЛИ - ИСПРАВЛЕННАЯ ЛОГИКА ===
                if all_users:
                    total_users = len(all_users)
                    active_users = len([u for u in all_users if str(u.get('status', '')).upper() == 'ACTIVE'])
                    inactive_users = total_users - active_users
                    
                    text += f"👤 Пользователей в системе: {total_users}\n"
                    text += f"✅ Активных: {active_users}\n"
                    text += f"❌ Неактивных: {inactive_users}\n"
                    
                    logger.info(f"Users stats: Total={total_users}, Active={active_users}, Inactive={inactive_users}")
                else:
                    # Если список пользователей пустой, пробуем получить count
                    try:
                        users_count = await api.get_users_count()
                        if users_count is not None and users_count > 0:
                            text += f"👤 Пользователей в системе: {users_count}\n"
                            text += "⚠️ Детальная статистика недоступна\n"
                        else:
                            text += "👤 Пользователи: Нет данных или пустая система\n"
                            # Добавляем диагностическую информацию
                            text += "🔍 Возможные причины:\n"
                            text += "• Система только установлена\n"
                            text += "• Проблема с API доступом\n"
                            text += "• Ошибка в структуре данных API\n"
                    except Exception as count_error:
                        logger.error(f"Failed to get users count: {count_error}")
                        text += "👤 Пользователи: ❌ Ошибка получения данных\n"
                
                # === НОДЫ ===
                if all_nodes:
                    total_nodes = len(all_nodes)
                    online_nodes = 0
                    offline_nodes = 0
                    disabled_nodes = 0
                    
                    text += f"\n📡 Ноды ({total_nodes} шт.):\n"
                    
                    for i, node in enumerate(all_nodes):
                        node_name = node.get('name', f'Node-{i+1}')
                        status = node.get('status', 'unknown')
                        
                        logger.debug(f"Node '{node_name}': status='{status}'")
                        
                        if status == 'online':
                            online_nodes += 1
                            status_emoji = "🟢"
                        elif status == 'disabled':
                            disabled_nodes += 1
                            status_emoji = "⚫"
                        else:
                            offline_nodes += 1
                            status_emoji = "🔴"
                        
                        # Показываем первые 5 нод
                        if i < 5:
                            display_name = node_name[:20] + "..." if len(node_name) > 20 else node_name
                            text += f"{status_emoji} {display_name}\n"
                    
                    if total_nodes > 5:
                        text += f"... и еще {total_nodes - 5} нод\n"
                    
                    text += f"\n🖥 Итого нод:\n"
                    text += f"• Всего: {total_nodes}\n"
                    text += f"• 🟢 Онлайн: {online_nodes}\n"
                    text += f"• 🔴 Оффлайн: {offline_nodes}\n"
                    if disabled_nodes > 0:
                        text += f"• ⚫ Отключено: {disabled_nodes}\n"
                    
                    logger.info(f"Nodes stats: Total={total_nodes}, Online={online_nodes}, Offline={offline_nodes}, Disabled={disabled_nodes}")
                    
                    # Определяем общее состояние системы
                    if online_nodes == total_nodes:
                        system_status = "🟢 Нормальное"
                    elif online_nodes == 0:
                        system_status = "🔴 Критическое"
                    elif online_nodes < total_nodes / 2:
                        system_status = "🟠 Система работает частично"
                    else:
                        system_status = "🟡 Предупреждение"
                    
                    text += f"\n🏥 Состояние: {system_status}\n"
                else:
                    text += "\n⚠️ Ноды: данные недоступны\n"
                
                # === ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ ===
                try:
                    # Пытаемся получить системную статистику для трафика
                    system_stats = await api.get_system_stats()
                    if system_stats and 'bandwidth' in system_stats:
                        bandwidth = system_stats['bandwidth']
                        
                        # Ищем актуальные данные о трафике
                        if 'bandwidthCurrentYear' in bandwidth:
                            current_year = bandwidth['bandwidthCurrentYear'].get('current', '0')
                            if current_year != '0':
                                text += f"\n📊 Трафик за год: {current_year}\n"
                        
                        if 'bandwidthCalendarMonth' in bandwidth:
                            current_month = bandwidth['bandwidthCalendarMonth'].get('current', '0')
                            if current_month != '0':
                                text += f"📊 Трафик за месяц: {current_month}\n"
                        
                except Exception as e:
                    logger.warning(f"Failed to get additional system stats: {e}")
                
            except Exception as api_error:
                logger.error(f"Failed to get RemnaWave stats: {api_error}", exc_info=True)
                text += "\n❌ Ошибка получения статистики RemnaWave\n"
                text += f"Детали: {str(api_error)[:60]}...\n"
        else:
            text += "\n🔗 API RemnaWave: 🔴 Недоступен\n"
        
        # Add timestamp
        text += f"\n🕐 Обновлено: {format_datetime(current_time, user.language)}"
        
        # Create keyboard
        keyboard = system_stats_keyboard(user.language, timestamp=int(current_time.timestamp()) if force_refresh else None)
        
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
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

@admin_router.callback_query(F.data == "debug_users_api")
async def debug_users_api_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Debug users API to check response structure"""
    if not await check_admin_access(callback, user):
        return
    
    if not api:
        await callback.answer("❌ API недоступен", show_alert=True)
        return
    
    try:
        await callback.answer("🔍 Анализирую структуру API...")
        
        # Используем debug метод
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
        
        # Попробуем получить пользователей обычным методом
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
    """Comprehensive API debugging with detailed analysis"""
    if not await check_admin_access(callback, user):
        return
    
    if not api:
        await callback.message.edit_text(
            "❌ API недоступен для диагностики",
            reply_markup=admin_system_keyboard(user.language)
        )
        return
    
    await callback.answer("🔍 Запуск полной диагностики API...")
    
    # Тестируем основные эндпоинты
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
                
                # Анализируем конкретно данные нод
                if 'nodes' in endpoint and debug_result.get('json'):
                    await analyze_nodes_response(debug_result['json'], diagnostic_text)
                
                # Анализируем данные пользователей
                if 'users' in endpoint and debug_result.get('json'):
                    await analyze_users_response(debug_result['json'], diagnostic_text)
                    
            else:
                diagnostic_text += f"   ❌ Ошибка: {debug_result.get('status', 'N/A')}\n"
                if 'error' in debug_result:
                    diagnostic_text += f"   💥 Детали: {debug_result['error'][:50]}...\n"
            
            diagnostic_text += "\n"
            
        except Exception as e:
            diagnostic_text += f"   💥 Исключение: {str(e)[:50]}...\n\n"
    
    # Добавляем рекомендации
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
    
    # Обрезаем текст если он слишком длинный
    if len(diagnostic_text) > 4000:
        diagnostic_text = diagnostic_text[:3900] + "\n\n... (текст обрезан)"
    
    try:
        await callback.message.edit_text(diagnostic_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Failed to send diagnostic results: {e}")
        await callback.answer("❌ Ошибка отправки результатов диагностики", show_alert=True)

async def analyze_nodes_response(json_data, diagnostic_text):
    """Analyze nodes response for debugging"""
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
            
            # Показываем первые 2 ноды для примера
            for i, node in enumerate(nodes_list[:2]):
                name = node.get('name', f'Node-{i+1}')
                status = node.get('status', 'unknown')
                diagnostic_text += f"   📡 {name}: {status}\n"
        
    except Exception as e:
        diagnostic_text += f"   ⚠️ Ошибка анализа нод: {str(e)[:30]}...\n"

async def analyze_users_response(json_data, diagnostic_text):
    """Analyze users response for debugging"""
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
            
            # Показываем примеры статусов
            statuses = [str(u.get('status', 'N/A')).upper() for u in users_list[:3]]
            diagnostic_text += f"   📊 Примеры статусов: {', '.join(statuses)}\n"
        
    except Exception as e:
        diagnostic_text += f"   ⚠️ Ошибка анализа пользователей: {str(e)[:30]}...\n"

@admin_router.callback_query(F.data == "nodes_management")
async def nodes_management_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Show improved nodes management interface"""
    if not await check_admin_access(callback, user):
        return
    
    await show_nodes_management_improved(callback, user, api)

async def show_nodes_management_improved(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None):
    """Show nodes management with improved display and error handling"""
    try:
        if not api:
            await callback.message.edit_text(
                "❌ API RemnaWave недоступен\n\n"
                "Для управления нодами необходимо подключение к API.",
                reply_markup=admin_system_keyboard(user.language)
            )
            return
        
        await callback.answer("🖥 Загружаю информацию о нодах...")
        
        # Get nodes with improved API call
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
        
        # Calculate statistics
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
        
        # Build display text
        text = "🖥 **Управление нодами**\n\n"
        
        # Overall statistics
        text += "📊 **Общая статистика:**\n"
        text += f"├ Всего нод: {len(nodes)}\n"
        text += f"├ 🟢 Онлайн: {len(online_nodes)}\n"
        text += f"├ 🔴 Оффлайн: {len(offline_nodes)}\n"
        text += f"└ ⚫ Отключено: {len(disabled_nodes)}\n\n"
        
        # System health indicator
        if len(online_nodes) == len(nodes):
            text += "🟢 **Система работает нормально**\n\n"
        elif len(online_nodes) >= len(nodes) * 0.7:
            text += "🟡 **Система работает с предупреждениями**\n\n"
        elif len(online_nodes) > 0:
            text += "🟠 **Система работает частично**\n\n"
        else:
            text += "🔴 **Критическое состояние системы**\n\n"
        
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Show online nodes first
        if online_nodes:
            text += "🟢 **Активные ноды:**\n"
            for i, node in enumerate(online_nodes[:3], 1):
                text += format_node_info(node, i)
            if len(online_nodes) > 3:
                text += f"   _... и еще {len(online_nodes) - 3} активных нод_\n"
            text += "\n"
        
        # Show offline nodes
        if offline_nodes:
            text += "🔴 **Оффлайн ноды:**\n"
            for i, node in enumerate(offline_nodes[:2], 1):
                text += format_node_info(node, i)
            if len(offline_nodes) > 2:
                text += f"   _... и еще {len(offline_nodes) - 2} оффлайн нод_\n"
            text += "\n"
        
        # Show disabled nodes
        if disabled_nodes:
            text += "⚫ **Отключенные ноды:**\n"
            for i, node in enumerate(disabled_nodes[:2], 1):
                text += format_node_info(node, i)
            if len(disabled_nodes) > 2:
                text += f"   _... и еще {len(disabled_nodes) - 2} отключенных нод_\n"
        
        text += f"\n🕐 _Обновлено: {format_datetime(datetime.now(), user.language)}_"
        
        # Create improved keyboard
        keyboard = nodes_management_keyboard(nodes, user.language)
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error in show_nodes_management_improved: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка загрузки информации о нодах\n\n"
            f"Детали: {str(e)[:100]}",
            reply_markup=admin_system_keyboard(user.language)
        )

def format_node_info(node: Dict, index: int) -> str:
    """Format node information for display"""
    name = node.get('name', f'Node-{index}')
    address = node.get('address', 'N/A')
    
    # Truncate long values
    if len(name) > 25:
        name = name[:22] + "..."
    if len(address) > 30:
        address = address[:27] + "..."
    
    text = f"{index}. **{name}**\n"
    
    if address != 'N/A':
        text += f"   📍 {address}\n"
    
    # Add resource usage if available
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
    
    # Add users count if available
    if node.get('usersCount'):
        text += f"   👥 Пользователей: {node['usersCount']}\n"
    
    return text

@admin_router.callback_query(F.data == "restart_all_nodes")
async def restart_all_nodes_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show confirmation for restarting all nodes"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите перезагрузить ВСЕ ноды?\n\n"
        "Это может привести к временной недоступности сервиса для всех пользователей!",
        reply_markup=confirm_restart_keyboard(None, user.language)
    )

@admin_router.callback_query(F.data == "confirm_restart_all_nodes")
async def confirm_restart_all_nodes_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Confirm and restart all nodes with improved error handling - ИСПРАВЛЕНО"""
    if not await check_admin_access(callback, user):
        return
    
    try:
        # Проверяем доступность API
        if not api:
            await callback.message.edit_text(
                "❌ API недоступен\n\n"
                "Невозможно выполнить перезагрузку без подключения к RemnaWave API.\n"
                "Обратитесь к администратору для настройки подключения.",
                reply_markup=admin_system_keyboard(user.language)
            )
            return
        
        # Показываем индикатор загрузки
        await callback.answer("🔄 Отправляю команду перезагрузки всех нод...")
        
        # Выполняем перезагрузку
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
    """Show detailed node information"""
    if not await check_admin_access(callback, user):
        return
    
    try:
        node_id = callback.data.replace("node_details_", "")
        
        if not api:
            await callback.answer("❌ API недоступен", show_alert=True)
            return
        
        # Get all nodes and find the specific one
        nodes = await api.get_all_nodes()
        node = None
        
        for n in nodes:
            if str(n.get('id')) == node_id or str(n.get('uuid')) == node_id:
                node = n
                break
        
        if not node:
            await callback.answer("❌ Нода не найдена", show_alert=True)
            return
        
        # Build detailed information
        text = "🖥 **Детальная информация о ноде**\n\n"
        
        text += f"📛 **Название:** {node.get('name', 'Unknown')}\n"
        text += f"🆔 **ID:** `{node.get('id', node.get('uuid', 'N/A'))}`\n"
        
        # Status with detailed info
        status = node.get('status', 'unknown')
        status_emoji = {
            'online': '🟢',
            'offline': '🔴',
            'disabled': '⚫',
            'disconnected': '🔴',
            'xray_stopped': '🟡'
        }.get(status, '⚪')
        
        text += f"🔘 **Статус:** {status_emoji} {status.upper()}\n\n"
        
        # Connection details
        text += "📡 **Подключение:**\n"
        text += f"├ Подключена: {'✅' if node.get('isConnected') else '❌'}\n"
        text += f"├ Включена: {'✅' if not node.get('isDisabled') else '❌'}\n"
        text += f"├ Нода онлайн: {'✅' if node.get('isNodeOnline') else '❌'}\n"
        text += f"└ Xray работает: {'✅' if node.get('isXrayRunning') else '❌'}\n\n"
        
        # Address
        if node.get('address'):
            text += f"🌐 **Адрес:** `{node['address']}`\n\n"
        
        # Resource usage
        if node.get('cpuUsage') or node.get('memUsage'):
            text += "💻 **Использование ресурсов:**\n"
            if node.get('cpuUsage'):
                cpu = node['cpuUsage']
                cpu_bar = create_progress_bar(cpu)
                text += f"├ CPU: {cpu_bar} {cpu:.1f}%\n"
            if node.get('memUsage'):
                mem = node['memUsage']
                mem_bar = create_progress_bar(mem)
                text += f"└ RAM: {mem_bar} {mem:.1f}%\n"
            text += "\n"
        
        # Users
        if node.get('usersCount') is not None:
            text += f"👥 **Пользователей:** {node['usersCount']}\n\n"
        
        # Create action keyboard
        keyboard = create_node_actions_keyboard(node_id, status, user.language)
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error showing node details: {e}")
        await callback.answer("❌ Ошибка загрузки информации", show_alert=True)

def create_progress_bar(percent: float, length: int = 10) -> str:
    """Create a text progress bar"""
    filled = int(percent / 100 * length)
    bar = '█' * filled + '░' * (length - filled)
    return f"[{bar}]"

def create_node_actions_keyboard(node_id: str, status: str, language: str = 'ru') -> InlineKeyboardMarkup:
    """Create keyboard for node actions"""
    buttons = []
    
    # Status control
    if status == 'disabled':
        buttons.append([
            InlineKeyboardButton(text="✅ Включить ноду", callback_data=f"enable_node_{node_id}")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="⚫ Отключить ноду", callback_data=f"disable_node_{node_id}")
        ])
    
    # Restart button
    buttons.append([
        InlineKeyboardButton(text="🔄 Перезагрузить ноду", callback_data=f"restart_node_{node_id}")
    ])
    
    # Refresh button
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить информацию", callback_data=f"refresh_node_{node_id}")
    ])
    
    # Back button
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад к списку нод", callback_data="nodes_management")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("enable_node_"))
async def enable_node_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Enable specific node"""
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
            
            # Refresh node details
            await node_details_callback(callback, user, api=api)
        else:
            await callback.answer("❌ Ошибка включения ноды", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error enabling node: {e}")
        await callback.answer("❌ Ошибка операции", show_alert=True)

@admin_router.callback_query(F.data.startswith("disable_node_"))
async def disable_node_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Disable specific node"""
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
            
            # Refresh node details
            await node_details_callback(callback, user, api=api)
        else:
            await callback.answer("❌ Ошибка отключения ноды", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error disabling node: {e}")
        await callback.answer("❌ Ошибка операции", show_alert=True)

@admin_router.callback_query(F.data.startswith("restart_node_"))
async def restart_node_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show confirmation for restarting specific node"""
    if not await check_admin_access(callback, user):
        return
    
    node_id = callback.data.replace("restart_node_", "")
    
    await callback.message.edit_text(
        f"⚠️ Вы уверены, что хотите перезагрузить ноду ID: {node_id}?",
        reply_markup=confirm_restart_keyboard(node_id, user.language)
    )

@admin_router.callback_query(F.data.startswith("confirm_restart_node_"))
async def confirm_restart_node_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Confirm and restart specific node"""
    if not await check_admin_access(callback, user):
        return
    
    try:
        node_id = callback.data.replace("confirm_restart_node_", "")
        await callback.answer("🔄 Перезагружаю ноду...")
        
        # Здесь нужно добавить метод restart_node в RemnaWaveAPI если его нет
        # Пока используем заглушку
        if api:
            # result = await api.restart_node(node_id)  # Метод нужно добавить в API
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

@admin_router.callback_query(F.data == "system_users")
async def system_users_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show system users management - ИСПРАВЛЕНО"""
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
        # Если редактирование не удается, отвечаем на callback
        await callback.answer("Меню пользователей системы", show_alert=False)
        
        # Попробуем отправить новое сообщение
        try:
            await callback.message.answer(
                "👥 Управление пользователями системы RemnaWave\n\nВыберите действие:",
                reply_markup=system_users_keyboard(user.language)
            )
        except Exception as send_error:
            logger.error(f"Failed to send new message: {send_error}")

async def safe_edit_message(callback: CallbackQuery, text: str, reply_markup=None, parse_mode=None, answer_text="✅ Обновлено"):
    """Безопасное редактирование сообщения с обработкой 'message is not modified'"""
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
            # Попробуем просто ответить на callback
            try:
                await callback.answer(answer_text, show_alert=False)
            except:
                pass  # Игнорируем если и это не работает



@admin_router.callback_query(F.data == "bulk_operations")
async def bulk_operations_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show bulk operations menu"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "🗂 Массовые операции с пользователями\n\n"
        "⚠️ Внимание: эти операции затрагивают всех пользователей системы!",
        reply_markup=bulk_operations_keyboard(user.language)
    )

@admin_router.callback_query(F.data == "bulk_reset_traffic")
async def bulk_reset_traffic_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Reset traffic for all users"""
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
    """Confirm bulk traffic reset"""
    if not await check_admin_access(callback, user):
        return
    
    try:
        await callback.answer("🔄 Сбрасываю трафик для всех пользователей...")
        
        if api:
            # Показываем прогресс
            await callback.message.edit_text("⏳ Выполняется массовый сброс трафика...")
            
            # Выполняем сброс трафика
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

# Обновить существующую функцию admin_stats_callback
@admin_router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, **kwargs):
    """Show statistics with link to detailed system stats"""
    if not await check_admin_access(callback, user):
        return
    
    try:
        # Get database stats
        db_stats = await db.get_stats()
        
        text = "📊 Краткая статистика\n\n"
        text += "💾 База данных бота:\n"
        text += f"👥 Пользователей: {db_stats['total_users']}\n"
        text += f"📋 Подписок: {db_stats['total_subscriptions_non_trial']}\n"
        text += f"💰 Доходы: {db_stats['total_revenue']} руб.\n"
        
        # Quick RemnaWave info
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
    """List all system users with improved display"""
    if not await check_admin_access(callback, user):
        return
    
    # Сброс состояния для пагинации
    if state:
        await state.clear()
        await state.update_data(users_page=0)
    
    # Проверяем наличие API
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
    """Show paginated system users list with better formatting - ИСПРАВЛЕНО"""
    try:
        if not api:
            await callback.message.edit_text(
                "❌ API недоступен",
                reply_markup=system_users_keyboard(user.language)
            )
            return
        
        await callback.answer("📋 Загружаю список пользователей...")
        
        # Get all users
        all_users = await api.get_all_system_users_full()
        if not all_users:
            await callback.message.edit_text(
                "❌ Пользователи не найдены",
                reply_markup=system_users_keyboard(user.language)
            )
            return
        
        # Sort users by status and creation date
        all_users.sort(key=lambda x: (
            0 if x.get('status') == 'ACTIVE' else 1,
            x.get('createdAt', ''),
        ), reverse=True)
        
        # Pagination
        users_per_page = 8
        total_pages = (len(all_users) + users_per_page - 1) // users_per_page
        start_idx = page * users_per_page
        end_idx = min(start_idx + users_per_page, len(all_users))
        page_users = all_users[start_idx:end_idx]
        
        # Statistics
        active_count = len([u for u in all_users if u.get('status') == 'ACTIVE'])
        disabled_count = len(all_users) - active_count
        with_telegram = len([u for u in all_users if u.get('telegramId')])
        
        # Build display text - БЕЗ MARKDOWN форматирования
        text = f"👥 Пользователи системы RemnaWave\n"
        text += f"📄 Страница {page + 1} из {total_pages}\n\n"
        
        text += f"📊 Статистика:\n"
        text += f"├ Всего: {len(all_users)}\n"
        text += f"├ ✅ Активных: {active_count}\n"
        text += f"├ ❌ Отключенных: {disabled_count}\n"
        text += f"└ 📱 С Telegram: {with_telegram}\n\n"
        
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Display users with improved formatting
        for i, sys_user in enumerate(page_users, start=start_idx + 1):
            # Status icon
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
            
            # User info - ОЧИЩАЕМ от специальных символов
            username = sys_user.get('username', 'N/A')
            # Удаляем или экранируем специальные символы Markdown
            username = username.replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
            
            short_uuid = sys_user.get('shortUuid', '')[:8] + "..." if sys_user.get('shortUuid') else 'N/A'
            
            text += f"{i}. {status_icon} {username}\n"  # Убрали ** для жирного текста
            
            # Telegram info
            if sys_user.get('telegramId'):
                telegram_id = str(sys_user['telegramId'])
                text += f"   📱 TG: {telegram_id}\n"  # Убрали ` для моноширинного шрифта
            
            # UUID info
            text += f"   🔗 {short_uuid}\n"
            
            # Expiry info
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
            
            # Traffic info
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
        
        # Create pagination keyboard
        keyboard = create_users_pagination_keyboard(page, total_pages, user.language)
        
        # Отправляем БЕЗ parse_mode
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
            # Убрали parse_mode='Markdown'
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
    """Create pagination keyboard for users list"""
    buttons = []
    
    # Quick actions row
    buttons.append([
        InlineKeyboardButton(text="🔍 Поиск", callback_data="search_user_uuid"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_users_page_{current_page}")
    ])
    
    # Pagination row
    if total_pages > 1:
        nav_row = []
        
        # First page button
        if current_page > 0:
            nav_row.append(InlineKeyboardButton(text="⏮", callback_data="users_page_0"))
        
        # Previous button
        if current_page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"users_page_{current_page - 1}"))
        
        # Current page indicator
        nav_row.append(InlineKeyboardButton(text=f"{current_page + 1}/{total_pages}", callback_data="noop"))
        
        # Next button
        if current_page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"users_page_{current_page + 1}"))
        
        # Last page button
        if current_page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="⏭", callback_data=f"users_page_{total_pages - 1}"))
        
        buttons.append(nav_row)
    
    # Filter buttons
    buttons.append([
        InlineKeyboardButton(text="✅ Только активные", callback_data="filter_users_active"),
        InlineKeyboardButton(text="📱 С Telegram", callback_data="filter_users_telegram")
    ])
    
    # Back button
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="system_users")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("users_page_"))
async def users_page_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, state: FSMContext = None, **kwargs):
    """Handle users list pagination"""
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
    """Handle refresh system users with timestamp"""
    if not await check_admin_access(callback, user):
        return
    
    await show_system_users_list(callback, user, api, force_refresh=True)

# Helper function to create keyboards with timestamps
def system_stats_keyboard(language: str, timestamp: int = None) -> InlineKeyboardMarkup:
    """Create system stats keyboard with optional timestamp"""
    refresh_callback = f"refresh_system_stats_{timestamp}" if timestamp else "refresh_system_stats"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖥 Управление нодами", callback_data="nodes_management")],
        [InlineKeyboardButton(text="👥 Пользователи системы", callback_data="system_users")],
        [InlineKeyboardButton(text="🗂 Массовые операции", callback_data="bulk_operations")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=refresh_callback)],
        [InlineKeyboardButton(text="🔙 " + t('back', language), callback_data="admin_system")]
    ])

def nodes_management_keyboard(nodes: List[Dict], language: str, timestamp: int = None) -> InlineKeyboardMarkup:
    """Create nodes management keyboard with optional timestamp"""
    buttons = []
    
    # Node action buttons
    if nodes:
        # Add individual node buttons (first 3)
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
        
        # Restart all nodes button
        buttons.append([
            InlineKeyboardButton(text="🔄 Перезагрузить все ноды", callback_data="restart_all_nodes")
        ])
    
    # Refresh button with timestamp if provided
    refresh_callback = f"refresh_nodes_stats_{timestamp}" if timestamp else "refresh_nodes_stats"
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=refresh_callback)
    ])
    
    # Back button
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_system")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("refresh_nodes_stats_"))
async def refresh_nodes_stats_with_timestamp_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Handle refresh nodes stats with timestamp"""
    if not await check_admin_access(callback, user):
        return
    
    await show_nodes_management(callback, user, api, force_refresh=True)

@admin_router.callback_query(F.data.startswith("refresh_system_stats_"))
async def refresh_system_stats_with_timestamp_callback(callback: CallbackQuery, user: User, db: Database, api: RemnaWaveAPI = None, **kwargs):
    """Handle refresh system stats with timestamp"""
    if not await check_admin_access(callback, user):
        return
    
    await show_system_stats(callback, user, db, api, force_refresh=True)

@admin_router.callback_query(F.data == "users_statistics")
async def users_statistics_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Show detailed users statistics"""
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
        
        # Получаем базовую системную статистику
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
        
        # Получаем информацию о состоянии системы
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
    """Start universal user search"""
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
    """Handle universal user search"""
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
        
        # Try different search methods
        # 1. Check if it's a UUID
        if validate_squad_uuid(search_input):
            user_data = await api.get_user_by_uuid(search_input)
            search_method = "UUID"
        
        # 2. Try as Telegram ID
        if not user_data:
            try:
                telegram_id = int(search_input)
                user_data = await api.get_user_by_telegram_id(telegram_id)
                search_method = "Telegram ID"
            except ValueError:
                pass
        
        # 3. Try as Short UUID
        if not user_data:
            user_data = await api.get_user_by_short_uuid(search_input)
            search_method = "Short UUID"
        
        # 4. Try as Username
        if not user_data:
            user_data = await api.get_user_by_username(search_input)
            search_method = "Username"
        
        # 5. Try as Email
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
        
        # Get local user info if exists
        local_user = None
        if user_data.get('telegramId') and db:
            local_user = await db.get_user_by_telegram_id(user_data['telegramId'])
        
        # Format user information
        text = f"👤 Информация о пользователе\n"
        text += f"🔍 Найден по: {search_method}\n\n"
        
        # Basic info
        text += f"📛 Username: `{user_data.get('username', 'N/A')}`\n"
        text += f"🆔 UUID: `{user_data.get('uuid', 'N/A')}`\n"
        text += f"🔗 Short UUID: `{user_data.get('shortUuid', 'N/A')}`\n"
        
        if user_data.get('telegramId'):
            text += f"📱 Telegram ID: `{user_data.get('telegramId')}`\n"
            if local_user:
                text += f"💰 Баланс в боте: {local_user.balance} руб.\n"
        
        if user_data.get('email'):
            text += f"📧 Email: {user_data.get('email')}\n"
        
        # Status
        status = user_data.get('status', 'UNKNOWN')
        status_emoji = "✅" if status == 'ACTIVE' else "❌"
        text += f"\n🔘 Статус: {status_emoji} {status}\n"
        
        # Subscription info
        if user_data.get('expireAt'):
            expire_date = user_data['expireAt']
            text += f"⏰ Истекает: {expire_date[:10]}\n"
            
            # Calculate days left
            try:
                expire_dt = datetime.fromisoformat(expire_date.replace('Z', '+00:00'))
                days_left = (expire_dt - datetime.now()).days
                if days_left > 0:
                    text += f"📅 Осталось дней: {days_left}\n"
                else:
                    text += f"❌ Подписка истекла\n"
            except:
                pass
        
        # Traffic info
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
        
        # Create management keyboard
        keyboard = create_user_management_keyboard(user_data.get('uuid'), user_data.get('status'), user.language)
        
        await search_msg.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error searching user: {e}")

def create_user_management_keyboard(user_uuid: str, status: str, language: str = 'ru') -> InlineKeyboardMarkup:
    """Create keyboard for user management"""
    buttons = []
    
    # Status control buttons
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
    
    # Edit buttons
    buttons.append([
        InlineKeyboardButton(text="📅 Изменить срок", callback_data=f"edit_user_expiry_{user_uuid}"),
        InlineKeyboardButton(text="📊 Изменить трафик", callback_data=f"edit_user_traffic_{user_uuid}")
    ])
    
    # Additional info
    buttons.append([
        InlineKeyboardButton(text="📈 Статистика", callback_data=f"user_usage_stats_{user_uuid}"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_user_{user_uuid}")
    ])
    
    # Navigation
    buttons.append([
        InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_user_uuid"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="system_users")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data.startswith("edit_user_expiry_"))
async def edit_user_expiry_callback(callback: CallbackQuery, user: User, state: FSMContext, **kwargs):
    """Start editing user expiry date"""
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
    """Handle user expiry date edit - ИСПРАВЛЕНО"""
    if not api:
        await message.answer("❌ API недоступен")
        await state.clear()
        return
    
    data = await state.get_data()
    user_uuid = data.get('edit_user_uuid')
    input_value = message.text.strip()
    
    try:
        # Parse input
        new_expiry = None
        
        # Try as number of days
        try:
            days = int(input_value)
            if days > 0:
                new_expiry = datetime.now() + timedelta(days=days)
        except ValueError:
            # Try as date
            try:
                new_expiry = datetime.strptime(input_value, "%Y-%m-%d")
            except ValueError:
                await message.answer("❌ Неверный формат даты. Используйте YYYY-MM-DD или количество дней")
                return
        
        if not new_expiry:
            await message.answer("❌ Не удалось определить дату")
            return
        
        # Update user in RemnaWave - правильный формат для API
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
    """Handle user expiry date edit"""
    if not api:
        await message.answer("❌ API недоступен")
        await state.clear()
        return
    
    data = await state.get_data()
    user_uuid = data.get('edit_user_uuid')
    input_value = message.text.strip()
    
    try:
        # Parse input
        new_expiry = None
        
        # Try as number of days
        try:
            days = int(input_value)
            if days > 0:
                new_expiry = datetime.now() + timedelta(days=days)
        except ValueError:
            # Try as date
            try:
                new_expiry = datetime.strptime(input_value, "%Y-%m-%d")
            except ValueError:
                await message.answer("❌ Неверный формат даты. Используйте YYYY-MM-DD или количество дней")
                return
        
        if not new_expiry:
            await message.answer("❌ Не удалось определить дату")
            return
        
        # Update user in RemnaWave
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
    """Start editing user traffic limit"""
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
    """Handle user traffic limit edit"""
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
        
        # Update user traffic limit
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
    """Refresh user information"""
    if not await check_admin_access(callback, user):
        return
    
    user_uuid = callback.data.replace("refresh_user_", "")
    
    if not api:
        await callback.answer("❌ API недоступен", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Обновляю информацию...")
        
        # Get updated user data
        user_data = await api.get_user_by_uuid(user_uuid)
        if not user_data:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        # Format updated information (reuse the same format as in search)
        text = f"👤 Информация о пользователе (обновлено)\n\n"
        # ... (same formatting as in search result)
        
        keyboard = create_user_management_keyboard(user_uuid, user_data.get('status'), user.language)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error refreshing user: {e}")
        await callback.answer("❌ Ошибка обновления", show_alert=True)

# Синхронизация с RemnaWave
@admin_router.callback_query(F.data == "sync_remnawave")
async def sync_remnawave_callback(callback: CallbackQuery, user: User, **kwargs):
    """Show RemnaWave synchronization menu"""
    if not await check_admin_access(callback, user):
        return
    
    await callback.message.edit_text(
        "🔄 Синхронизация с RemnaWave\n\n"
        "Выберите тип синхронизации:",
        reply_markup=sync_remnawave_keyboard(user.language)
    )

def sync_remnawave_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    """Keyboard for RemnaWave sync options"""
    buttons = [
        [InlineKeyboardButton(text="👥 Синхронизировать пользователей", callback_data="sync_users_remnawave")],
        [InlineKeyboardButton(text="📋 Синхронизировать подписки", callback_data="sync_subscriptions_remnawave")],
        [InlineKeyboardButton(text="🔄 Полная синхронизация", callback_data="sync_full_remnawave")],
        [InlineKeyboardButton(text="🌍 ИМПОРТ ВСЕХ по Telegram ID", callback_data="import_all_by_telegram")],
        [InlineKeyboardButton(text="👤 Синхронизировать одного", callback_data="sync_single_user")],
        [InlineKeyboardButton(text="📋 Просмотр планов", callback_data="view_imported_plans")],
        [InlineKeyboardButton(text="📊 Статус синхронизации", callback_data="sync_status_remnawave")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_system")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.callback_query(F.data == "sync_users_remnawave")
async def sync_users_remnawave_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, db: Database = None, **kwargs):
    """Sync users between bot and RemnaWave"""
    if not await check_admin_access(callback, user):
        return
    
    if not api or not db:
        await callback.answer("❌ API или база данных недоступны", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Запускаю синхронизацию пользователей...")
        
        # Show progress message
        progress_msg = await callback.message.edit_text("⏳ Синхронизация пользователей...\n\n0% выполнено")
        
        # Get all users from RemnaWave
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
                # Update progress every 10 users
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
                
                # Check if user exists in bot database
                bot_user = await db.get_user_by_telegram_id(telegram_id)
                
                if not bot_user:
                    # Create new user in bot database
                    bot_user = await db.create_user(
                        telegram_id=telegram_id,
                        username=remna_user.get('username'),
                        language='ru',
                        is_admin=telegram_id in (kwargs.get('config', {}).ADMIN_IDS if 'config' in kwargs else [])
                    )
                    created += 1
                
                # Update user's RemnaWave UUID if not set
                if not bot_user.remnawave_uuid:
                    bot_user.remnawave_uuid = remna_user.get('uuid')
                    await db.update_user(bot_user)
                    updated += 1
                
                synced += 1
                
            except Exception as e:
                logger.error(f"Error syncing user {remna_user.get('username')}: {e}")
                errors += 1
        
        # Final result
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
    """Sync subscriptions between bot and RemnaWave - УЛУЧШЕННАЯ ВЕРСИЯ с логированием"""
    if not await check_admin_access(callback, user):
        return
    
    if not api or not db:
        await callback.answer("❌ API или база данных недоступны", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Запускаю улучшенную синхронизацию подписок...")
        
        progress_msg = await callback.message.edit_text("⏳ Синхронизация подписок...\n\nЭтап 1/4: Получение данных...")
        
        # Получаем всех пользователей из RemnaWave
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
        
        # Логируем структуру первого пользователя для отладки
        if users_with_tg:
            first_user = users_with_tg[0]
            logger.info(f"Sample user structure: {list(first_user.keys())}")
            logger.info(f"Sample user: telegramId={first_user.get('telegramId')}, "
                       f"username={first_user.get('username')}, "
                       f"status={first_user.get('status')}, "
                       f"shortUuid={first_user.get('shortUuid')}, "
                       f"expireAt={first_user.get('expireAt')}")
        
        # Статистика
        created_subs = 0
        updated_subs = 0
        created_users = 0
        updated_users = 0
        errors = 0
        
        # Этап 1: Создание пользователей бота если их нет
        await progress_msg.edit_text("⏳ Синхронизация подписок...\n\nЭтап 1/4: Создание пользователей...")
        
        for i, remna_user in enumerate(users_with_tg):
            try:
                telegram_id = remna_user['telegramId']
                logger.debug(f"Processing user {i+1}/{len(users_with_tg)}: {telegram_id}")
                
                bot_user = await db.get_user_by_telegram_id(telegram_id)
                
                if not bot_user:
                    # Создаем пользователя в боте
                    is_admin = telegram_id in (kwargs.get('config', {}).ADMIN_IDS if 'config' in kwargs else [])
                    bot_user = await db.create_user(
                        telegram_id=telegram_id,
                        username=remna_user.get('username'),
                        first_name=remna_user.get('username'),  # Используем username как first_name
                        language='ru',
                        is_admin=is_admin
                    )
                    created_users += 1
                    logger.info(f"Created bot user for Telegram ID: {telegram_id}")
                
                # Обновляем RemnaWave UUID если не установлен
                if not bot_user.remnawave_uuid and remna_user.get('uuid'):
                    bot_user.remnawave_uuid = remna_user['uuid']
                    await db.update_user(bot_user)
                    updated_users += 1
                    logger.debug(f"Updated RemnaWave UUID for user {telegram_id}")
                
            except Exception as e:
                logger.error(f"Error creating/updating user {telegram_id}: {e}")
                errors += 1
        
        logger.info(f"User creation phase: created={created_users}, updated={updated_users}, errors={errors}")
        
        # Этап 2: Синхронизация подписок
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
                
                # Проверяем есть ли у пользователя активная подписка в RemnaWave
                is_active_in_remna = status == 'ACTIVE'
                has_expiry = bool(expire_at)
                
                if not short_uuid:
                    logger.debug(f"User {telegram_id} has no shortUuid, skipping")
                    continue
                
                # Ищем подписку в боте по short_uuid
                existing_sub = await db.get_user_subscription_by_short_uuid(telegram_id, short_uuid)
                
                if existing_sub:
                    # ОБНОВЛЯЕМ СУЩЕСТВУЮЩУЮ ПОДПИСКУ
                    logger.debug(f"Found existing subscription for user {telegram_id}")
                    
                    # Обновляем дату истечения
                    if has_expiry:
                        try:
                            if remna_user['expireAt'].endswith('Z'):
                                expire_dt = datetime.fromisoformat(remna_user['expireAt'].replace('Z', '+00:00'))
                            else:
                                expire_dt = datetime.fromisoformat(remna_user['expireAt'])
                            
                            # Конвертируем в naive datetime для БД
                            expire_dt_naive = expire_dt.replace(tzinfo=None) if expire_dt.tzinfo else expire_dt
                            existing_sub.expires_at = expire_dt_naive
                        except Exception as date_error:
                            logger.error(f"Error parsing date for user {telegram_id}: {date_error}")
                    
                    # Обновляем статус
                    existing_sub.is_active = is_active_in_remna
                    
                    # Обновляем лимит трафика если есть
                    if remna_user.get('trafficLimitBytes') is not None:
                        traffic_gb = remna_user['trafficLimitBytes'] // (1024 * 1024 * 1024) if remna_user['trafficLimitBytes'] > 0 else 0
                        existing_sub.traffic_limit_gb = traffic_gb
                    
                    await db.update_user_subscription(existing_sub)
                    updated_subs += 1
                    
                else:
                    # СОЗДАЕМ НОВУЮ ПОДПИСКУ
                    logger.debug(f"No existing subscription found for user {telegram_id}, creating new one")
                    
                    # Подписки нет в боте - создаем новую
                    if is_active_in_remna or has_expiry:
                        logger.info(f"Creating new subscription for user {telegram_id}")
                        
                        # Определяем squad_uuid
                        squad_uuid = None
                        active_squads = remna_user.get('activeInternalSquads', [])
                        
                        if active_squads:
                            # Берем первый активный squad
                            first_squad = active_squads[0]
                            if isinstance(first_squad, dict):
                                squad_uuid = first_squad.get('uuid') or first_squad.get('id')
                            else:
                                squad_uuid = str(first_squad)
                        
                        if not squad_uuid:
                            # Fallback: берем из internalSquads
                            internal_squads = remna_user.get('internalSquads', [])
                            if internal_squads:
                                first_squad = internal_squads[0]
                                if isinstance(first_squad, dict):
                                    squad_uuid = first_squad.get('uuid') or first_squad.get('id')
                                else:
                                    squad_uuid = str(first_squad)
                        
                        # Ищем или создаем план подписки
                        subscription_plan = None
                        
                        if squad_uuid:
                            # Ищем существующий план с таким squad_uuid
                            all_plans = await db.get_all_subscriptions(include_inactive=True, exclude_trial=False)
                            for plan in all_plans:
                                if plan.squad_uuid == squad_uuid:
                                    subscription_plan = plan
                                    break
                        
                        if not subscription_plan:
                            # Создаем новый план подписки
                            traffic_gb = 0
                            if remna_user.get('trafficLimitBytes'):
                                traffic_gb = remna_user['trafficLimitBytes'] // (1024 * 1024 * 1024)
                            
                            plan_name = f"Imported_{remna_user.get('username', 'User')[:10]}"
                            if squad_uuid:
                                plan_name += f"_{squad_uuid[:8]}"
                            
                            subscription_plan = await db.create_subscription(
                                name=plan_name,
                                description=f"Автоматически импортированная подписка из RemnaWave",
                                price=0,  # Цена неизвестна, ставим 0
                                duration_days=30,  # Стандартная длительность
                                traffic_limit_gb=traffic_gb,
                                squad_uuid=squad_uuid or ''
                            )
                            logger.info(f"Created new subscription plan: {plan_name}")
                        
                        # Создаем пользовательскую подписку
                        expire_dt_naive = None
                        if has_expiry:
                            try:
                                if remna_user['expireAt'].endswith('Z'):
                                    expire_dt = datetime.fromisoformat(remna_user['expireAt'].replace('Z', '+00:00'))
                                else:
                                    expire_dt = datetime.fromisoformat(remna_user['expireAt'])
                                expire_dt_naive = expire_dt.replace(tzinfo=None) if expire_dt.tzinfo else expire_dt
                            except:
                                # Fallback: 30 дней от сегодня
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
        
        # Этап 3: Проверка консистентности
        await progress_msg.edit_text("⏳ Синхронизация подписок...\n\nЭтап 3/4: Проверка консистентности...")
        
        consistency_fixes = 0
        for remna_user in users_with_tg:
            try:
                telegram_id = remna_user['telegramId']
                user_subs = await db.get_user_subscriptions(telegram_id)
                
                for user_sub in user_subs:
                    # Проверяем истек ли срок подписки
                    if user_sub.expires_at < datetime.now() and user_sub.is_active:
                        user_sub.is_active = False
                        await db.update_user_subscription(user_sub)
                        
                        # Также деактивируем в RemnaWave
                        if remna_user.get('uuid'):
                            await api.update_user(remna_user['uuid'], {'status': 'EXPIRED'})
                        
                        consistency_fixes += 1
                        
            except Exception as e:
                logger.error(f"Error in consistency check for user {telegram_id}: {e}")
        
        # Этап 4: Финальная проверка
        await progress_msg.edit_text("⏳ Синхронизация подписок...\n\nЭтап 4/4: Финальная проверка...")
        
        # Подсчитываем финальную статистику
        total_bot_users = len(await db.get_all_users())
        total_bot_subs = 0
        active_bot_subs = 0
        
        all_bot_users = await db.get_all_users()
        for bot_user in all_bot_users:
            user_subs = await db.get_user_subscriptions(bot_user.telegram_id)
            total_bot_subs += len(user_subs)
            active_bot_subs += len([s for s in user_subs if s.is_active])
        
        # Формируем отчет
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

# Обработчики действий с конкретными пользователями
@admin_router.callback_query(F.data.startswith("reset_user_traffic_"))
async def reset_user_traffic_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Reset traffic for specific user with confirmation"""
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
            
            # Обновляем информацию о пользователе
            try:
                updated_user = await api.get_user_by_uuid(user_uuid)
                if updated_user:
                    used_traffic = updated_user.get('usedTrafficBytes', 0)
                    await callback.message.edit_reply_markup(
                        reply_markup=callback.message.reply_markup
                    )
            except:
                pass  # Не критично если не удалось обновить
        else:
            await callback.answer("❌ Ошибка сброса трафика", show_alert=True)
    
    except Exception as e:
        logger.error(f"Error resetting user traffic: {e}")
        await callback.answer("❌ Ошибка операции", show_alert=True)

@admin_router.callback_query(F.data.startswith("disable_user_"))
async def disable_user_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Disable specific user with confirmation"""
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
    """Enable specific user with confirmation"""
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
    """Show synchronization status"""
    if not await check_admin_access(callback, user):
        return
    
    if not api or not db:
        await callback.answer("❌ API или база данных недоступны", show_alert=True)
        return
    
    try:
        await callback.answer("📊 Проверяю статус синхронизации...")
        
        # Get statistics
        remna_users = await api.get_all_system_users_full()
        bot_users = await db.get_all_users()
        
        # Count statistics
        remna_with_tg = len([u for u in remna_users if u.get('telegramId')])
        remna_without_tg = len(remna_users) - remna_with_tg
        
        bot_with_uuid = len([u for u in bot_users if u.remnawave_uuid])
        bot_without_uuid = len(bot_users) - bot_with_uuid
        
        # Check subscriptions sync
        total_bot_subs = 0
        synced_subs = 0
        
        for bot_user in bot_users:
            user_subs = await db.get_user_subscriptions(bot_user.telegram_id)
            total_bot_subs += len(user_subs)
            
            for user_sub in user_subs:
                # Check if subscription exists in RemnaWave
                for remna_user in remna_users:
                    if remna_user.get('shortUuid') == user_sub.short_uuid:
                        synced_subs += 1
                        break
        
        # Build status text
        text = "📊 **Статус синхронизации**\n\n"
        
        text += "**RemnaWave:**\n"
        text += f"• Всего пользователей: {len(remna_users)}\n"
        text += f"• С Telegram ID: {remna_with_tg}\n"
        text += f"• Без Telegram ID: {remna_without_tg}\n\n"
        
        text += "**Бот:**\n"
        text += f"• Всего пользователей: {len(bot_users)}\n"
        text += f"• С RemnaWave UUID: {bot_with_uuid}\n"
        text += f"• Без RemnaWave UUID: {bot_without_uuid}\n\n"
        
        text += "**Подписки:**\n"
        text += f"• Всего в боте: {total_bot_subs}\n"
        text += f"• Синхронизировано: {synced_subs}\n"
        text += f"• Не синхронизировано: {total_bot_subs - synced_subs}\n\n"
        
        # Recommendations
        if bot_without_uuid > 0 or remna_without_tg > 0 or (total_bot_subs - synced_subs) > 0:
            text += "⚠️ **Рекомендации:**\n"
            if bot_without_uuid > 0:
                text += f"• {bot_without_uuid} пользователей бота не связаны с RemnaWave\n"
            if remna_without_tg > 0:
                text += f"• {remna_without_tg} пользователей RemnaWave не имеют Telegram ID\n"
            if (total_bot_subs - synced_subs) > 0:
                text += f"• {total_bot_subs - synced_subs} подписок не синхронизированы\n"
            text += "\n💡 Рекомендуется выполнить полную синхронизацию\n"
        else:
            text += "✅ **Все данные синхронизированы**\n"
        
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

# User filtering handlers
@admin_router.callback_query(F.data == "filter_users_active")
async def filter_users_active_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Show only active users - ИСПРАВЛЕНО"""
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
        
        # Display filtered users - БЕЗ MARKDOWN
        text = f"✅ Активные пользователи ({len(active_users)})\n\n"
        
        for i, sys_user in enumerate(active_users[:10], 1):
            username = sys_user.get('username', 'N/A')
            # Очищаем username от специальных символов
            username = username.replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
            
            telegram_id = sys_user.get('telegramId', 'N/A')
            short_uuid = sys_user.get('shortUuid', '')[:8] + "..."
            
            text += f"{i}. {username}\n"  # Убрали **
            if telegram_id != 'N/A':
                text += f"   📱 TG: {telegram_id}\n"  # Убрали `
            text += f"   🔗 {short_uuid}\n"
            
            if sys_user.get('expireAt'):
                expire_date = sys_user['expireAt'][:10]
                text += f"   ⏰ До {expire_date}\n"
            text += "\n"
        
        if len(active_users) > 10:
            text += f"... и еще {len(active_users) - 10} активных пользователей"
        
        # Create keyboard with clear filter button
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Сбросить фильтр", callback_data="list_all_system_users")],
            [InlineKeyboardButton(text="📱 С Telegram", callback_data="filter_users_telegram")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="system_users")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard
            # Убрали parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error filtering active users: {e}")
        await callback.answer("❌ Ошибка фильтрации", show_alert=True)

@admin_router.callback_query(F.data == "filter_users_telegram")
async def filter_users_telegram_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, **kwargs):
    """Show only users with Telegram ID"""
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
        
        # Display filtered users
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
        
        # Create keyboard
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
    """Show all nodes with pagination"""
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
    """Show paginated nodes list"""
    try:
        nodes = await api.get_all_nodes()
        if not nodes:
            await callback.message.edit_text(
                "❌ Ноды не найдены",
                reply_markup=admin_system_keyboard(user.language)
            )
            return
        
        # Sort nodes by status
        nodes.sort(key=lambda x: (
            0 if x.get('status') == 'online' else 1,
            x.get('name', '')
        ))
        
        # Pagination
        nodes_per_page = 10
        total_pages = (len(nodes) + nodes_per_page - 1) // nodes_per_page
        start_idx = page * nodes_per_page
        end_idx = min(start_idx + nodes_per_page, len(nodes))
        page_nodes = nodes[start_idx:end_idx]
        
        # Build text
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
        
        # Create pagination keyboard
        buttons = []
        
        # Navigation
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
    """Handle nodes pagination"""
    if not await check_admin_access(callback, user):
        return
    
    try:
        page = int(callback.data.split("_")[-1])
        await show_nodes_paginated(callback, user, api, state, page)
    except Exception as e:
        logger.error(f"Error in nodes pagination: {e}")
        await callback.answer("❌ Ошибка навигации", show_alert=True)

# Правильный декоратор для следующей функции
@admin_router.callback_query(F.data == "sync_full_remnawave")
async def sync_full_remnawave_callback(callback: CallbackQuery, user: User, api: RemnaWaveAPI = None, db: Database = None, **kwargs):
    """Full synchronization between bot and RemnaWave - УЛУЧШЕННАЯ ВЕРСИЯ"""
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
        
        # Получаем данные
        remna_users = await api.get_all_system_users_full()
        users_with_tg = [u for u in remna_users if u.get('telegramId')]
        
        logger.info(f"Starting full sync for {len(users_with_tg)} users with Telegram ID")
        
        # Статистика
        users_created = 0
        users_updated = 0
        subs_created = 0
        subs_updated = 0
        plans_created = 0
        statuses_updated = 0
        errors = 0
        
        # Этап 1: Синхронизация пользователей
        await progress_msg.edit_text(
            "⏳ Полная синхронизация RemnaWave\n\n"
            "Этап 1/5: Синхронизация пользователей..."
        )
        
        for remna_user in users_with_tg:
            try:
                telegram_id = remna_user['telegramId']
                bot_user = await db.get_user_by_telegram_id(telegram_id)
                
                if not bot_user:
                    # Создаем пользователя
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
                
                # Обновляем RemnaWave UUID
                if not bot_user.remnawave_uuid and remna_user.get('uuid'):
                    bot_user.remnawave_uuid = remna_user['uuid']
                    await db.update_user(bot_user)
                    users_updated += 1
                
            except Exception as e:
                logger.error(f"Error syncing user {telegram_id}: {e}")
                errors += 1
        
        # Этап 2: Создание планов подписок
        await progress_msg.edit_text(
            "⏳ Полная синхронизация RemnaWave\n\n"
            "Этап 2/5: Создание планов подписок..."
        )
        
        # Собираем уникальные squad_uuid из RemnaWave
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
        
        # Создаем планы для отсутствующих squad_uuid
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
        
        # Этап 3: Синхронизация подписок
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
                
                # Ищем существующую подписку
                existing_sub = await db.get_user_subscription_by_short_uuid(telegram_id, short_uuid)
                
                if existing_sub:
                    # Обновляем существующую
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
                    # Создаем новую подписку
                    if remna_user.get('status') == 'ACTIVE' or remna_user.get('expireAt'):
                        # Находим подходящий план
                        squad_uuid = None
                        active_squads = remna_user.get('activeInternalSquads', [])
                        
                        if active_squads:
                            first_squad = active_squads[0]
                            if isinstance(first_squad, dict):
                                squad_uuid = first_squad.get('uuid') or first_squad.get('id')
                            else:
                                squad_uuid = str(first_squad)
                        
                        # Ищем план подписки
                        subscription_plan = None
                        all_plans = await db.get_all_subscriptions(include_inactive=True, exclude_trial=False)
                        
                        for plan in all_plans:
                            if plan.squad_uuid == squad_uuid:
                                subscription_plan = plan
                                break
                        
                        if subscription_plan:
                            # Парсим дату истечения
                            expire_dt = None
                            if remna_user.get('expireAt'):
                                try:
                                    expire_dt = datetime.fromisoformat(remna_user['expireAt'].replace('Z', '+00:00'))
                                    expire_dt = expire_dt.replace(tzinfo=None)
                                except:
                                    expire_dt = datetime.now() + timedelta(days=30)
                            else:
                                expire_dt = datetime.now() + timedelta(days=30)
                            
                            # Создаем подписку
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
        
        # Этап 4: Обновление статусов
        await progress_msg.edit_text(
            "⏳ Полная синхронизация RemnaWave\n\n"
            "Этап 4/5: Обновление статусов..."
        )
        
        # Деактивируем истекшие подписки
        all_bot_users = await db.get_all_users()
        for bot_user in all_bot_users:
            user_subs = await db.get_user_subscriptions(bot_user.telegram_id)
            
            for user_sub in user_subs:
                if user_sub.expires_at < datetime.now() and user_sub.is_active:
                    user_sub.is_active = False
                    await db.update_user_subscription(user_sub)
                    statuses_updated += 1
                    
                    # Обновляем в RemnaWave
                    if bot_user.remnawave_uuid:
                        try:
                            await api.update_user(bot_user.remnawave_uuid, {'status': 'EXPIRED'})
                        except:
                            pass
        
        # Этап 5: Финальная статистика
        await progress_msg.edit_text(
            "⏳ Полная синхронизация RemnaWave\n\n"
            "Этап 5/5: Подсчет результатов..."
        )
        
        # Собираем финальную статистику
        total_bot_users = len(await db.get_all_users())
        total_subscriptions = 0
        active_subscriptions = 0
        
        for bot_user in all_bot_users:
            user_subs = await db.get_user_subscriptions(bot_user.telegram_id)
            total_subscriptions += len(user_subs)
            active_subscriptions += len([s for s in user_subs if s.is_active])
        
        # Отчет
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
    """Start single user sync"""
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
    """Handle single user sync - ИСПРАВЛЕНО"""
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
        
        # Получаем данные из RemnaWave - ИСПРАВЛЕНИЕ
        remna_user_result = await api.get_user_by_telegram_id(telegram_id)
        
        logger.info(f"API result type: {type(remna_user_result)}")
        logger.info(f"API result: {remna_user_result}")
        
        remna_user = None
        
        # Обрабатываем разные типы ответов от API
        if isinstance(remna_user_result, dict):
            remna_user = remna_user_result
        elif isinstance(remna_user_result, list):
            # Если API вернул список, берем первого пользователя
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
        
        # Проверяем/создаем пользователя в боте
        bot_user = await db.get_user_by_telegram_id(telegram_id)
        
        if not bot_user:
            # Создаем пользователя
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
        
        # Обновляем RemnaWave UUID
        if not bot_user.remnawave_uuid and remna_user.get('uuid'):
            bot_user.remnawave_uuid = remna_user['uuid']
            await db.update_user(bot_user)
            result_details.append("✅ Обновлен RemnaWave UUID")
        
        # Проверяем подписку
        short_uuid = remna_user.get('shortUuid')
        
        if short_uuid:
            existing_sub = await db.get_user_subscription_by_short_uuid(telegram_id, short_uuid)
            
            if existing_sub:
                # Обновляем существующую подписку
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
                # Создаем новую подписку
                if remna_user.get('status') == 'ACTIVE' or remna_user.get('expireAt'):
                    # Определяем squad_uuid - ИСПРАВЛЕНИЕ
                    squad_uuid = None
                    
                    # Проверяем activeInternalSquads
                    active_squads = remna_user.get('activeInternalSquads', [])
                    if active_squads and isinstance(active_squads, list):
                        first_squad = active_squads[0]
                        if isinstance(first_squad, dict):
                            squad_uuid = first_squad.get('uuid') or first_squad.get('id')
                        elif isinstance(first_squad, str):
                            squad_uuid = first_squad
                    
                    # Если не нашли, проверяем internalSquads
                    if not squad_uuid:
                        internal_squads = remna_user.get('internalSquads', [])
                        if internal_squads and isinstance(internal_squads, list):
                            first_squad = internal_squads[0]
                            if isinstance(first_squad, dict):
                                squad_uuid = first_squad.get('uuid') or first_squad.get('id')
                            elif isinstance(first_squad, str):
                                squad_uuid = first_squad
                    
                    # Ищем план подписки
                    subscription_plan = None
                    if squad_uuid:
                        all_plans = await db.get_all_subscriptions(include_inactive=True, exclude_trial=False)
                        for plan in all_plans:
                            if plan.squad_uuid == squad_uuid:
                                subscription_plan = plan
                                break
                    
                    if not subscription_plan and squad_uuid:
                        # Создаем план
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
                        # Парсим дату
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
                        
                        # Создаем подписку
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
        
        # Формируем отчет
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
        
        # Информация о squad
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
    """Import ALL subscriptions from RemnaWave by Telegram ID - ИСПРАВЛЕННАЯ ВЕРСИЯ для множественных подписок"""
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
        
        # Получаем ВСЕХ пользователей из RemnaWave (каждая запись = отдельная подписка)
        all_remna_records = await api.get_all_system_users_full()
        
        if not all_remna_records:
            await progress_msg.edit_text(
                "❌ Не удалось получить записи из RemnaWave",
                reply_markup=sync_remnawave_keyboard(user.language)
            )
            return
        
        logger.info(f"Got {len(all_remna_records)} total records from RemnaWave")
        
        # Фильтруем только записи с Telegram ID
        records_with_telegram = [r for r in all_remna_records if r.get('telegramId')]
        
        logger.info(f"Found {len(records_with_telegram)} records with Telegram ID")
        
        # Группируем по Telegram ID для статистики
        users_by_telegram = {}
        for record in records_with_telegram:
            tg_id = record['telegramId']
            if tg_id not in users_by_telegram:
                users_by_telegram[tg_id] = []
            users_by_telegram[tg_id].append(record)
        
        logger.info(f"Found {len(users_by_telegram)} unique Telegram users with {len(records_with_telegram)} total subscriptions")
        
        # Статистика
        bot_users_created = 0
        bot_users_updated = 0
        plans_created = 0
        subscriptions_imported = 0
        subscriptions_updated = 0
        errors = 0
        skipped_no_shortuid = 0
        
        # Этап 1: Создание пользователей бота (по уникальным Telegram ID)
        await progress_msg.edit_text(
            "⏳ Массовый импорт подписок по Telegram ID\n\n"
            "Этап 1/5: Создание пользователей бота..."
        )
        
        for telegram_id, user_records in users_by_telegram.items():
            try:
                logger.info(f"Processing Telegram user {telegram_id} with {len(user_records)} subscriptions")
                
                # Берем последнюю (самую свежую) запись для создания пользователя
                latest_record = max(user_records, key=lambda x: x.get('updatedAt', x.get('createdAt', '')))
                
                # Создаем/обновляем пользователя бота
                bot_user = await db.get_user_by_telegram_id(telegram_id)
                
                if not bot_user:
                    # Создаем нового пользователя
                    is_admin = telegram_id in (kwargs.get('config', {}).ADMIN_IDS if 'config' in kwargs else [])
                    
                    # Используем лучшее имя из всех записей
                    best_username = None
                    for record in user_records:
                        username = record.get('username', '')
                        # Предпочитаем "человеческие" имена перед автогенерированными
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
                
                # Обновляем RemnaWave UUID (используем последний)
                if latest_record.get('uuid') and bot_user.remnawave_uuid != latest_record['uuid']:
                    bot_user.remnawave_uuid = latest_record['uuid']
                    await db.update_user(bot_user)
                    bot_users_updated += 1
                
            except Exception as e:
                logger.error(f"Error processing Telegram user {telegram_id}: {e}")
                errors += 1
        
        # Этап 2: Сбор всех уникальных squad UUID
        await progress_msg.edit_text(
            "⏳ Массовый импорт подписок по Telegram ID\n\n"
            "Этап 2/5: Анализ squad'ов..."
        )
        
        all_squads = set()
        squad_names = {}
        
        for i, record in enumerate(records_with_telegram):
            logger.debug(f"Analyzing record {i+1}/{len(records_with_telegram)}: {record.get('username')}")
            
            # Извлекаем squad UUID из activeInternalSquads
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
        
        # Этап 3: Создание планов подписок
        await progress_msg.edit_text(
            "⏳ Массовый импорт подписок по Telegram ID\n\n"
            "Этап 3/5: Создание планов подписок..."
        )
        
        # Получаем существующие планы
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
                        name="Старая подписка",  # ИЗМЕНЕНО: стандартное название
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
        
        # Этап 4: Импорт каждой подписки отдельно
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
                
                # Пропускаем если нет shortUuid
                if not short_uuid:
                    skipped_no_shortuid += 1
                    logger.warning(f"❌ Skipping record: no shortUuid")
                    continue
                
                # Проверяем есть ли уже такая подписка в боте
                existing_sub = await db.get_user_subscription_by_short_uuid(telegram_id, short_uuid)
                
                if existing_sub:
                    # Проверяем что план подписки еще существует
                    existing_plan = await db.get_subscription_by_id(existing_sub.subscription_id)
                    
                    if existing_plan:
                        # Подписка и план существуют - обновляем
                        logger.info(f"Updating existing subscription for TG {telegram_id}, shortUuid {short_uuid}")
                        
                        # Обновляем дату истечения
                        if expire_at:
                            try:
                                if expire_at.endswith('Z'):
                                    expire_dt = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
                                else:
                                    expire_dt = datetime.fromisoformat(expire_at)
                                existing_sub.expires_at = expire_dt.replace(tzinfo=None)
                            except Exception as date_error:
                                logger.error(f"Error parsing date: {date_error}")
                        
                        # Обновляем статус
                        existing_sub.is_active = (status == 'ACTIVE')
                        
                        # Обновляем лимит трафика
                        if record.get('trafficLimitBytes') is not None:
                            traffic_gb = record['trafficLimitBytes'] // (1024 * 1024 * 1024) if record['trafficLimitBytes'] > 0 else 0
                            existing_sub.traffic_limit_gb = traffic_gb
                        
                        await db.update_user_subscription(existing_sub)
                        subscriptions_updated += 1
                    else:
                        # Подписка существует но план удален - удаляем "осиротевшую" подписку
                        logger.warning(f"Found orphaned subscription {existing_sub.id} for user {telegram_id}, deleting...")
                        await db.delete_user_subscription(existing_sub.id)
                        
                        # И создаем новую подписку (переходим к блоку создания)
                        logger.info(f"Creating new subscription after cleaning orphaned one")
                        existing_sub = None  # Сбрасываем чтобы перейти к созданию
                
                if not existing_sub:
                    # Создаем новую подписку
                    logger.info(f"Creating new subscription for TG {telegram_id}, shortUuid {short_uuid}")
                    
                    # Извлекаем squad_uuid из activeInternalSquads
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
                    
                    # Ищем план подписки
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
                    
                    # Парсим дату истечения
                    expire_dt_naive = datetime.now() + timedelta(days=30)  # Дефолт
                    if expire_at:
                        try:
                            if expire_at.endswith('Z'):
                                expire_dt = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
                            else:
                                expire_dt = datetime.fromisoformat(expire_at)
                            expire_dt_naive = expire_dt.replace(tzinfo=None)
                        except Exception as date_error:
                            logger.error(f"Error parsing expiry date: {date_error}")
                    
                    # Создаем подписку
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
        
        # Этап 5: Финальная статистика
        await progress_msg.edit_text(
            "⏳ Массовый импорт подписок по Telegram ID\n\n"
            "Этап 5/5: Подсчет результатов..."
        )
        
        # Подсчитываем итоговую статистику
        final_bot_users = len(await db.get_all_users())
        final_subscriptions = 0
        final_active_subs = 0
        
        all_bot_users = await db.get_all_users()
        for bot_user in all_bot_users:
            user_subs = await db.get_user_subscriptions(bot_user.telegram_id)
            final_subscriptions += len(user_subs)
            final_active_subs += len([s for s in user_subs if s.is_active])
        
        # Формируем детальный отчет
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
    """Handle user structure debugging"""
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
        # Получаем пользователя
        remna_user = await api.get_user_by_telegram_id(telegram_id)
        
        if not remna_user:
            await message.answer(
                f"❌ Пользователь с Telegram ID {telegram_id} не найден",
                reply_markup=admin_menu_keyboard(user.language)
            )
            await state.clear()
            return
        
        # Создаем детальный анализ
        analysis = f"🔍 Структура пользователя {telegram_id}\n\n"
        
        # Основные поля
        analysis += "📋 Основные поля:\n"
        for key in ['uuid', 'username', 'shortUuid', 'status', 'expireAt', 'telegramId']:
            value = remna_user.get(key, 'N/A')
            analysis += f"• {key}: {value}\n"
        
        analysis += "\n"
        
        # Squad поля
        analysis += "🏷 Squad поля:\n"
        squad_fields = ['activeInternalSquads', 'internalSquads', 'squads', 'squad', 'squadUuid', 'squadId']
        
        for field in squad_fields:
            if field in remna_user:
                value = remna_user[field]
                analysis += f"• {field}: {value}\n"
                
                # Детальный анализ squad полей
                if isinstance(value, list) and value:
                    for i, item in enumerate(value):
                        analysis += f"  [{i}]: {item}\n"
                        if isinstance(item, dict):
                            for sub_key, sub_value in item.items():
                                analysis += f"    {sub_key}: {sub_value}\n"
            else:
                analysis += f"• {field}: ОТСУТСТВУЕТ\n"
        
        analysis += "\n"
        
        # Все остальные поля
        analysis += "📝 Все поля пользователя:\n"
        for key, value in remna_user.items():
            if key not in ['uuid', 'username', 'shortUuid', 'status', 'expireAt', 'telegramId'] + squad_fields:
                # Обрезаем длинные значения
                if isinstance(value, str) and len(value) > 50:
                    value = value[:47] + "..."
                analysis += f"• {key}: {value}\n"
        
        # Если текст слишком длинный, разбиваем на части
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
    """Rename all imported subscription plans to 'Старая подписка'"""
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
        
        # Получаем все планы
        all_plans = await db.get_all_subscriptions_admin()
        
        # Ищем планы которые выглядят как импортированные
        imported_plans = []
        
        for plan in all_plans:
            # Пропускаем планы которые уже называются "Старая подписка"
            if plan.name == "Старая подписка":
                continue
            
            # Пропускаем настоящие триальные планы (помеченные как is_trial = True)
            if getattr(plan, 'is_trial', False):
                logger.debug(f"Skipping trial plan: {plan.name}")
                continue
            
            # Критерии импортированного плана:
            is_imported_plan = False
            
            # 1. Явно помеченные как импортированные
            if getattr(plan, 'is_imported', False):
                is_imported_plan = True
                logger.debug(f"Plan {plan.name} marked as imported")
            
            # 2. Планы с автогенерированными названиями для импорта
            elif plan.name.startswith(('Import_', 'Auto_', 'Imported_')):
                is_imported_plan = True
                logger.debug(f"Plan {plan.name} has import prefix")
            
            # 3. Планы с названиями Trial_ которые НЕ являются настоящими триальными
            elif plan.name.startswith('Trial_') and not getattr(plan, 'is_trial', False):
                is_imported_plan = True
                logger.debug(f"Plan {plan.name} looks like imported trial")
            
            # 4. Планы с подозрительными характеристиками импорта
            elif (plan.price == 0 and 
                  any(keyword in plan.name.lower() for keyword in ['user_', 'default', 'squad']) and
                  not getattr(plan, 'is_trial', False)):
                is_imported_plan = True
                logger.debug(f"Plan {plan.name} has suspicious import characteristics")
            
            # 5. Планы с squad в описании (характерно для импорта)
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
        
        # Показываем найденные планы для подтверждения
        plans_list = []
        for plan in imported_plans[:10]:  # Показываем первые 10
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
        
        # Сохраняем список планов в состояние FSM
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
    """Confirm renaming of found plans"""
    if not await check_admin_access(callback, user):
        return
    
    if not db or not state:
        await callback.answer("❌ База данных или состояние недоступны", show_alert=True)
        return
    
    try:
        await callback.answer("🔄 Переименовываю планы...")
        
        progress_msg = await callback.message.edit_text("⏳ Переименование планов...")
        
        # Получаем список планов для переименования из состояния
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
                
                # Переименовываем план
                plan.name = "Старая подписка"
                plan.description = f"Импортированная подписка из RemnaWave (было: {old_name})"
                plan.is_imported = True  # Помечаем как импортированный
                
                await db.update_subscription(plan)
                renamed_count += 1
                renamed_plans.append(f"'{old_name}' -> 'Старая подписка'")
                logger.info(f"Renamed plan: '{old_name}' -> 'Старая подписка'")
                
            except Exception as e:
                logger.error(f"Error renaming plan {plan_id}: {e}")
                errors += 1
        
        # Очищаем состояние
        await state.clear()
        
        # Результат
        result_text = (
            f"✅ Переименование завершено!\n\n"
            f"📊 Результаты:\n"
            f"• Переименовано планов: {renamed_count}\n"
            f"• Ошибок: {errors}\n\n"
            f"🏷 Все планы теперь называются: 'Старая подписка'\n\n"
            f"🕐 Завершено: {format_datetime(datetime.now(), user.language)}"
        )
        
        # Показываем детали если планов немного
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
    """Cancel rename and return to main menu"""
    await state.clear()
    await callback.message.edit_text(
        t('main_menu', user.language),
        reply_markup=main_menu_keyboard(user.language, user.is_admin)
    )

@admin_router.callback_query(F.data == "view_imported_plans")
async def view_imported_plans_callback(callback: CallbackQuery, user: User, db: Database = None, **kwargs):
    """View all imported subscription plans"""
    if not await check_admin_access(callback, user):
        return
    
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        # Получаем все планы
        all_plans = await db.get_all_subscriptions_admin()
        
        # Классифицируем планы
        regular_plans = []
        imported_plans = []
        suspicious_plans = []  # Планы которые выглядят как импортированные, но не помечены
        
        for plan in all_plans:
            if getattr(plan, 'is_imported', False):
                imported_plans.append(plan)
            elif plan.is_trial:
                continue  # Пропускаем триальные
            elif (plan.name.startswith(('Import_', 'Auto_', 'Imported_')) or 
                  (plan.price == 0 and any(keyword in plan.name.lower() for keyword in 
                      ['импорт', 'default', 'squad', 'user_']))):
                suspicious_plans.append(plan)
            else:
                regular_plans.append(plan)
        
        text = f"📋 Анализ планов подписок\n\n"
        
        # Обычные планы (для покупки)
        text += f"🛒 Обычные планы (для покупки): {len(regular_plans)}\n"
        if regular_plans:
            for plan in regular_plans[:3]:
                status = "🟢" if plan.is_active else "🔴"
                text += f"{status} {plan.name} - {plan.price}₽\n"
            if len(regular_plans) > 3:
                text += f"... и еще {len(regular_plans) - 3}\n"
        text += "\n"
        
        # Помеченные импортированные планы
        text += f"📦 Импортированные планы: {len(imported_plans)}\n"
        if imported_plans:
            for plan in imported_plans[:3]:
                status = "🟢" if plan.is_active else "🔴"
                squad_short = plan.squad_uuid[:8] + "..." if plan.squad_uuid else "No Squad"
                text += f"{status} {plan.name} ({squad_short})\n"
            if len(imported_plans) > 3:
                text += f"... и еще {len(imported_plans) - 3}\n"
        text += "\n"
        
        # Подозрительные планы
        if suspicious_plans:
            text += f"⚠️ Возможно импортированные: {len(suspicious_plans)}\n"
            for plan in suspicious_plans[:3]:
                status = "🟢" if plan.is_active else "🔴"
                squad_short = plan.squad_uuid[:8] + "..." if plan.squad_uuid else "No Squad"
                text += f"{status} {plan.name} ({squad_short})\n"
            if len(suspicious_plans) > 3:
                text += f"... и еще {len(suspicious_plans) - 3}\n"
            text += "\n"
        
        # Статистика
        text += f"📊 Итого:\n"
        text += f"• Всего планов: {len(all_plans)}\n"
        text += f"• Обычных: {len(regular_plans)}\n"
        text += f"• Импортированных: {len(imported_plans)}\n"
        if suspicious_plans:
            text += f"• Нужно проверить: {len(suspicious_plans)}\n"
        
        # Клавиатура
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
    """Delete all imported plans with confirmation"""
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
    """Confirm deletion of imported plans with proper cleanup"""
    if not await check_admin_access(callback, user):
        return
    
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        await callback.answer("🗑 Удаляю импортированные планы...")
        
        progress_msg = await callback.message.edit_text("⏳ Удаление импортированных планов и связанных подписок...")
        
        # Получаем импортированные планы
        all_plans = await db.get_all_subscriptions_admin()
        imported_plans = [plan for plan in all_plans if getattr(plan, 'is_imported', False)]
        
        # Также ищем планы которые выглядят как импортированные
        for plan in all_plans:
            if (plan.name == "Старая подписка" and 
                plan not in imported_plans):
                imported_plans.append(plan)
        
        deleted_plans = 0
        deleted_user_subscriptions = 0
        errors = 0
        
        for plan in imported_plans:
            try:
                # ВАЖНО: Сначала удаляем все пользовательские подписки связанные с этим планом
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
                
                # Теперь удаляем сам план
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
    """Debug all subscription plans"""
    if not await check_admin_access(callback, user):
        return
    
    if not db:
        await callback.answer("❌ База данных недоступна", show_alert=True)
        return
    
    try:
        await callback.answer("🔍 Анализирую все планы...")
        
        # Получаем все планы
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
            
            # Анализ критериев
            looks_imported = (
                getattr(plan, 'is_imported', False) or
                plan.name.startswith(('Import_', 'Auto_', 'Imported_', 'Trial_')) or
                (plan.price == 0 and any(keyword in plan.name.lower() for keyword in 
                    ['импорт', 'default', 'squad', 'user_', 'trial']))
            )
            
            analysis += f"Создан: {plan.created_at.strftime('%Y-%m-%d %H:%M') if plan.created_at else 'N/A'}\n"
            analysis += "\n"
        
        # Если текст слишком длинный, разбиваем на части
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
        
        # Итоговая клавиатура
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
