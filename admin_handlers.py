import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta
import logging
from typing import List, Dict

from database import Database, User
from remnawave_api import RemnaWaveAPI
from keyboards import *
from translations import t
from utils import *
from handlers import BotStates

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

# Cancel handlers for admin states
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
    BotStates.admin_broadcast_text
))

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

async def cancel_admin_action(callback: CallbackQuery, state: FSMContext, user: User, **kwargs):
    """Cancel admin action and return to main menu"""
    await state.clear()
    await callback.message.edit_text(
        t('main_menu', user.language),
        reply_markup=main_menu_keyboard(user.language, user.is_admin)
    )
