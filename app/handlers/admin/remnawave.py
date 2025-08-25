import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from app.states import SquadRenameStates, SquadCreateStates
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.admin import (
   get_admin_remnawave_keyboard, get_sync_options_keyboard,
   get_node_management_keyboard, get_confirmation_keyboard,
   get_squad_management_keyboard, get_squad_edit_keyboard
)
from app.localization.texts import get_texts
from app.services.remnawave_service import RemnaWaveService
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_bytes, format_datetime

logger = logging.getLogger(__name__)

squad_inbound_selections = {}
squad_create_data = {}

@admin_required
@error_handler
async def show_remnawave_menu(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   remnawave_service = RemnaWaveService()
   connection_test = await remnawave_service.test_api_connection()
   
   status_emoji = "✅" if connection_test["status"] == "connected" else "❌"
   
   text = f"""
🖥️ <b>Управление RemnaWave</b>

📡 <b>Соединение:</b> {status_emoji} {connection_test["message"]}
🌐 <b>URL:</b> <code>{settings.REMNAWAVE_API_URL}</code>

Выберите действие:
"""
   
   await callback.message.edit_text(
       text,
       reply_markup=get_admin_remnawave_keyboard(db_user.language)
   )
   await callback.answer()


@admin_required
@error_handler
async def show_system_stats(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   from datetime import datetime, timedelta
   
   remnawave_service = RemnaWaveService()
   stats = await remnawave_service.get_system_statistics()
   
   if "error" in stats:
       await callback.message.edit_text(
           f"❌ Ошибка получения статистики: {stats['error']}",
           reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
               [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
           ])
       )
       await callback.answer()
       return
   
   system = stats.get("system", {})
   users_by_status = stats.get("users_by_status", {})
   server_info = stats.get("server_info", {})
   bandwidth = stats.get("bandwidth", {})
   traffic_periods = stats.get("traffic_periods", {})
   nodes_realtime = stats.get("nodes_realtime", [])
   nodes_weekly = stats.get("nodes_weekly", [])
   
   memory_total = server_info.get('memory_total', 1)
   memory_used_percent = (server_info.get('memory_used', 0) / memory_total * 100) if memory_total > 0 else 0
   
   uptime_seconds = server_info.get('uptime_seconds', 0)
   uptime_days = int(uptime_seconds // 86400)
   uptime_hours = int((uptime_seconds % 86400) // 3600)
   uptime_str = f"{uptime_days}д {uptime_hours}ч"
   
   users_status_text = ""
   for status, count in users_by_status.items():
       status_emoji = {
           'ACTIVE': '✅',
           'DISABLED': '❌', 
           'LIMITED': '⚠️',
           'EXPIRED': '⏰'
       }.get(status, '❓')
       users_status_text += f"  {status_emoji} {status}: {count}\n"
   
   top_nodes_text = ""
   for i, node in enumerate(nodes_weekly[:3], 1):
       top_nodes_text += f"  {i}. {node['name']}: {format_bytes(node['total_bytes'])}\n"
   
   realtime_nodes_text = ""
   for node in nodes_realtime[:3]:
       node_total = node.get('downloadBytes', 0) + node.get('uploadBytes', 0)
       if node_total > 0:
           realtime_nodes_text += f"  📡 {node.get('nodeName', 'Unknown')}: {format_bytes(node_total)}\n"
   
   def format_traffic_change(difference_str):
       if not difference_str or difference_str == '0':
           return ""
       elif difference_str.startswith('-'):
           return f" (🔻 {difference_str[1:]})"
       else:
           return f" (🔺 {difference_str})"
   
   text = f"""
📊 <b>Детальная статистика RemnaWave</b>

🖥️ <b>Сервер:</b>
- CPU: {server_info.get('cpu_cores', 0)} ядер ({server_info.get('cpu_physical_cores', 0)} физ.)
- RAM: {format_bytes(server_info.get('memory_used', 0))} / {format_bytes(memory_total)} ({memory_used_percent:.1f}%)
- Свободно: {format_bytes(server_info.get('memory_available', 0))}
- Uptime: {uptime_str}

👥 <b>Пользователи ({system.get('total_users', 0)} всего):</b>
- 🟢 Онлайн сейчас: {system.get('users_online', 0)}
- 📅 За сутки: {system.get('users_last_day', 0)}
- 📊 За неделю: {system.get('users_last_week', 0)}
- 💤 Никогда не заходили: {system.get('users_never_online', 0)}

<b>Статусы пользователей:</b>
{users_status_text}

🌐 <b>Ноды ({system.get('nodes_online', 0)} онлайн):</b>"""

   if realtime_nodes_text:
       text += f"""
<b>Реалтайм активность:</b>
{realtime_nodes_text}"""
   
   if top_nodes_text:
       text += f"""
<b>Топ нод за неделю:</b>
{top_nodes_text}"""
   
   text += f"""

📈 <b>Общий трафик пользователей:</b> {format_bytes(system.get('total_user_traffic', 0))}

📊 <b>Трафик по периодам:</b>
- 2 дня: {format_bytes(traffic_periods.get('last_2_days', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('last_2_days', {}).get('difference', ''))}
- 7 дней: {format_bytes(traffic_periods.get('last_7_days', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('last_7_days', {}).get('difference', ''))}
- 30 дней: {format_bytes(traffic_periods.get('last_30_days', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('last_30_days', {}).get('difference', ''))}
- Месяц: {format_bytes(traffic_periods.get('current_month', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('current_month', {}).get('difference', ''))}
- Год: {format_bytes(traffic_periods.get('current_year', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('current_year', {}).get('difference', ''))}
"""

   if bandwidth.get('realtime_total', 0) > 0:
       text += f"""
⚡ <b>Реалтайм трафик:</b>
- Скачивание: {format_bytes(bandwidth.get('realtime_download', 0))}
- Загрузка: {format_bytes(bandwidth.get('realtime_upload', 0))}
- Итого: {format_bytes(bandwidth.get('realtime_total', 0))}
"""
   
   text += f"""
🕒 <b>Обновлено:</b> {format_datetime(stats.get('last_updated', datetime.now()))}
"""
   
   keyboard = [
       [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_rw_system")],
       [types.InlineKeyboardButton(text="📈 Ноды", callback_data="admin_rw_nodes"),
        types.InlineKeyboardButton(text="👥 Синхронизация", callback_data="admin_rw_sync")],
       [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
   ]
   
   await callback.message.edit_text(
       text,
       reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
   )
   await callback.answer()

@admin_required
@error_handler
async def show_traffic_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    from datetime import datetime, timedelta
    
    remnawave_service = RemnaWaveService()
    
    try:
        async with remnawave_service.api as api:
            bandwidth_stats = await api.get_bandwidth_stats()
            
            realtime_usage = await api.get_nodes_realtime_usage()
            
            nodes_stats = await api.get_nodes_statistics()
            
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка получения статистики трафика: {str(e)}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
            ])
        )
        await callback.answer()
        return
    
    def parse_bandwidth(bandwidth_str):
        return remnawave_service._parse_bandwidth_string(bandwidth_str)
    
    total_realtime_download = sum(node.get('downloadBytes', 0) for node in realtime_usage)
    total_realtime_upload = sum(node.get('uploadBytes', 0) for node in realtime_usage)
    total_realtime = total_realtime_download + total_realtime_upload
    
    total_download_speed = sum(node.get('downloadSpeedBps', 0) for node in realtime_usage)
    total_upload_speed = sum(node.get('uploadSpeedBps', 0) for node in realtime_usage)
    
    periods = {
        'last_2_days': bandwidth_stats.get('bandwidthLastTwoDays', {}),
        'last_7_days': bandwidth_stats.get('bandwidthLastSevenDays', {}),
        'last_30_days': bandwidth_stats.get('bandwidthLast30Days', {}),
        'current_month': bandwidth_stats.get('bandwidthCalendarMonth', {}),
        'current_year': bandwidth_stats.get('bandwidthCurrentYear', {})
    }
    
    def format_change(diff_str):
        if not diff_str or diff_str == '0':
            return ""
        elif diff_str.startswith('-'):
            return f" 🔻 {diff_str[1:]}"
        else:
            return f" 🔺 {diff_str}"
    
    text = f"""
📊 <b>Статистика трафика RemnaWave</b>

⚡ <b>Реалтайм данные:</b>
- Скачивание: {format_bytes(total_realtime_download)}
- Загрузка: {format_bytes(total_realtime_upload)}
- Общий трафик: {format_bytes(total_realtime)}

🚀 <b>Текущие скорости:</b>
- Скорость скачивания: {format_bytes(total_download_speed)}/с
- Скорость загрузки: {format_bytes(total_upload_speed)}/с
- Общая скорость: {format_bytes(total_download_speed + total_upload_speed)}/с

📈 <b>Статистика по периодам:</b>

<b>За 2 дня:</b>
- Текущий: {format_bytes(parse_bandwidth(periods['last_2_days'].get('current', '0')))}
- Предыдущий: {format_bytes(parse_bandwidth(periods['last_2_days'].get('previous', '0')))}
- Изменение:{format_change(periods['last_2_days'].get('difference', ''))}

<b>За 7 дней:</b>
- Текущий: {format_bytes(parse_bandwidth(periods['last_7_days'].get('current', '0')))}
- Предыдущий: {format_bytes(parse_bandwidth(periods['last_7_days'].get('previous', '0')))}
- Изменение:{format_change(periods['last_7_days'].get('difference', ''))}

<b>За 30 дней:</b>
- Текущий: {format_bytes(parse_bandwidth(periods['last_30_days'].get('current', '0')))}
- Предыдущий: {format_bytes(parse_bandwidth(periods['last_30_days'].get('previous', '0')))}
- Изменение:{format_change(periods['last_30_days'].get('difference', ''))}

<b>Текущий месяц:</b>
- Текущий: {format_bytes(parse_bandwidth(periods['current_month'].get('current', '0')))}
- Предыдущий: {format_bytes(parse_bandwidth(periods['current_month'].get('previous', '0')))}
- Изменение:{format_change(periods['current_month'].get('difference', ''))}

<b>Текущий год:</b>
- Текущий: {format_bytes(parse_bandwidth(periods['current_year'].get('current', '0')))}
- Предыдущий: {format_bytes(parse_bandwidth(periods['current_year'].get('previous', '0')))}
- Изменение:{format_change(periods['current_year'].get('difference', ''))}
"""
    
    if realtime_usage:
        text += "\n🌐 <b>Трафик по нодам (реалтайм):</b>\n"
        for node in sorted(realtime_usage, key=lambda x: x.get('totalBytes', 0), reverse=True):
            node_total = node.get('totalBytes', 0)
            if node_total > 0:
                text += f"- {node.get('nodeName', 'Unknown')}: {format_bytes(node_total)}\n"
    
    if nodes_stats.get('lastSevenDays'):
        text += "\n📊 <b>Топ нод за 7 дней:</b>\n"
        
        nodes_weekly = {}
        for day_data in nodes_stats['lastSevenDays']:
            node_name = day_data['nodeName']
            if node_name not in nodes_weekly:
                nodes_weekly[node_name] = 0
            nodes_weekly[node_name] += int(day_data['totalBytes'])
        
        sorted_nodes = sorted(nodes_weekly.items(), key=lambda x: x[1], reverse=True)
        for i, (node_name, total_bytes) in enumerate(sorted_nodes[:5], 1):
            text += f"{i}. {node_name}: {format_bytes(total_bytes)}\n"
    
    text += f"\n🕒 <b>Обновлено:</b> {format_datetime(datetime.now())}"
    
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_rw_traffic")],
        [types.InlineKeyboardButton(text="📈 Ноды", callback_data="admin_rw_nodes"),
         types.InlineKeyboardButton(text="📊 Система", callback_data="admin_rw_system")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_nodes_management(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   remnawave_service = RemnaWaveService()
   nodes = await remnawave_service.get_all_nodes()
   
   if not nodes:
       await callback.message.edit_text(
           "🖥️ Ноды не найдены или ошибка подключения",
           reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
               [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
           ])
       )
       await callback.answer()
       return
   
   text = "🖥️ <b>Управление нодами</b>\n\n"
   keyboard = []
   
   for node in nodes:
       status_emoji = "🟢" if node["is_node_online"] else "🔴"
       connection_emoji = "📡" if node["is_connected"] else "📵"
       
       text += f"{status_emoji} {connection_emoji} <b>{node['name']}</b>\n"
       text += f"🌍 {node['country_code']} • {node['address']}\n"
       text += f"👥 Онлайн: {node['users_online'] or 0}\n\n"
       
       keyboard.append([
           types.InlineKeyboardButton(
               text=f"⚙️ {node['name']}",
               callback_data=f"admin_node_manage_{node['uuid']}"
           )
       ])
   
   keyboard.extend([
       [types.InlineKeyboardButton(text="🔄 Перезагрузить все", callback_data="admin_restart_all_nodes")],
       [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
   ])
   
   await callback.message.edit_text(
       text,
       reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
   )
   await callback.answer()


@admin_required
@error_handler
async def show_node_details(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   node_uuid = callback.data.split('_')[-1]
   
   remnawave_service = RemnaWaveService()
   node = await remnawave_service.get_node_details(node_uuid)
   
   if not node:
       await callback.answer("❌ Нода не найдена", show_alert=True)
       return
   
   status_emoji = "🟢" if node["is_node_online"] else "🔴"
   xray_emoji = "✅" if node["is_xray_running"] else "❌"
   
   text = f"""
🖥️ <b>Нода: {node['name']}</b>

<b>Статус:</b>
- Онлайн: {status_emoji} {'Да' if node['is_node_online'] else 'Нет'}
- Xray: {xray_emoji} {'Запущен' if node['is_xray_running'] else 'Остановлен'}
- Подключена: {'📡 Да' if node['is_connected'] else '📵 Нет'}
- Отключена: {'❌ Да' if node['is_disabled'] else '✅ Нет'}

<b>Информация:</b>
- Адрес: {node['address']}
- Страна: {node['country_code']}
- Пользователей онлайн: {node['users_online']}

<b>Трафик:</b>
- Использовано: {format_bytes(node['traffic_used_bytes'])}
- Лимит: {format_bytes(node['traffic_limit_bytes']) if node['traffic_limit_bytes'] else 'Без лимита'}
"""
   
   await callback.message.edit_text(
       text,
       reply_markup=get_node_management_keyboard(node_uuid, db_user.language)
   )
   await callback.answer()


@admin_required
@error_handler
async def manage_node(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   action, node_uuid = callback.data.split('_')[1], callback.data.split('_')[-1]
   
   remnawave_service = RemnaWaveService()
   success = await remnawave_service.manage_node(node_uuid, action)
   
   if success:
       action_text = {"enable": "включена", "disable": "отключена", "restart": "перезагружена"}
       await callback.answer(f"✅ Нода {action_text.get(action, 'обработана')}")
   else:
       await callback.answer("❌ Ошибка выполнения действия", show_alert=True)
   
   await show_node_details(
       types.CallbackQuery(
           id=callback.id,
           from_user=callback.from_user,
           chat_instance=callback.chat_instance,
           data=f"admin_node_manage_{node_uuid}",
           message=callback.message
       ),
       db_user,
       db
   )

@admin_required
@error_handler
async def show_node_statistics(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    node_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    
    node = await remnawave_service.get_node_details(node_uuid)
    
    if not node:
        await callback.answer("❌ Нода не найдена", show_alert=True)
        return
    
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        node_usage = await remnawave_service.get_node_user_usage_by_range(
            node_uuid, start_date, end_date
        )
        
        realtime_stats = await remnawave_service.get_nodes_realtime_usage()
        
        node_realtime = None
        for stats in realtime_stats:
            if stats.get('nodeUuid') == node_uuid:
                node_realtime = stats
                break
        
        status_emoji = "🟢" if node["is_node_online"] else "🔴"
        xray_emoji = "✅" if node["is_xray_running"] else "❌"
        
        text = f"""
📊 <b>Статистика ноды: {node['name']}</b>

<b>Статус:</b>
- Онлайн: {status_emoji} {'Да' if node['is_node_online'] else 'Нет'}
- Xray: {xray_emoji} {'Запущен' if node['is_xray_running'] else 'Остановлен'}
- Пользователей онлайн: {node['users_online'] or 0}

<b>Трафик:</b>
- Использовано: {format_bytes(node['traffic_used_bytes'] or 0)}
- Лимит: {format_bytes(node['traffic_limit_bytes']) if node['traffic_limit_bytes'] else 'Без лимита'}
"""

        if node_realtime:
            text += f"""
<b>Реалтайм статистика:</b>
- Скачано: {format_bytes(node_realtime.get('downloadBytes', 0))}
- Загружено: {format_bytes(node_realtime.get('uploadBytes', 0))}
- Общий трафик: {format_bytes(node_realtime.get('totalBytes', 0))}
- Скорость скачивания: {format_bytes(node_realtime.get('downloadSpeedBps', 0))}/с
- Скорость загрузки: {format_bytes(node_realtime.get('uploadSpeedBps', 0))}/с
"""

        if node_usage:
            text += f"\n<b>Статистика за 7 дней:</b>\n"
            total_usage = 0
            for usage in node_usage[-5:]: 
                daily_usage = usage.get('total', 0)
                total_usage += daily_usage
                text += f"- {usage.get('date', 'N/A')}: {format_bytes(daily_usage)}\n"
            
            text += f"\n<b>Общий трафик за 7 дней:</b> {format_bytes(total_usage)}"
        else:
            text += "\n<b>Статистика за 7 дней:</b> Данные недоступны"
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Обновить", callback_data=f"node_stats_{node_uuid}")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_node_manage_{node_uuid}")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики ноды {node_uuid}: {e}")
        
        text = f"""
📊 <b>Статистика ноды: {node['name']}</b>

<b>Статус:</b>
- Онлайн: {status_emoji} {'Да' if node['is_node_online'] else 'Нет'}  
- Xray: {xray_emoji} {'Запущен' if node['is_xray_running'] else 'Остановлен'}
- Пользователей онлайн: {node['users_online'] or 0}

<b>Трафик:</b>
- Использовано: {format_bytes(node['traffic_used_bytes'] or 0)}
- Лимит: {format_bytes(node['traffic_limit_bytes']) if node['traffic_limit_bytes'] else 'Без лимита'}

⚠️ <b>Детальная статистика временно недоступна</b>
Возможные причины:
• Проблемы с подключением к API
• Нода недавно добавлена
• Недостаточно данных для отображения

<b>Обновлено:</b> {format_datetime('now')}
"""
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Попробовать снова", callback_data=f"node_stats_{node_uuid}")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_node_manage_{node_uuid}")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()

@admin_required
@error_handler
async def show_squad_details(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)
    
    if not squad:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    text = f"""
🌐 <b>Сквад: {squad['name']}</b>

<b>Информация:</b>
- UUID: <code>{squad['uuid']}</code>
- Участников: {squad['members_count']}
- Инбаундов: {squad['inbounds_count']}

<b>Инбаунды:</b>
"""
    
    if squad.get('inbounds'):
        for inbound in squad['inbounds']:
            text += f"- {inbound['tag']} ({inbound['type']})\n"
    else:
        text += "Нет активных инбаундов"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_squad_management_keyboard(squad_uuid, db_user.language)
    )
    await callback.answer()


@admin_required
@error_handler
async def manage_squad_action(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    parts = callback.data.split('_')
    action = parts[1] 
    squad_uuid = parts[-1]
    
    remnawave_service = RemnaWaveService()
    
    if action == "add_users":
        success = await remnawave_service.add_all_users_to_squad(squad_uuid)
        if success:
            await callback.answer("✅ Задача добавления пользователей в очередь")
        else:
            await callback.answer("❌ Ошибка добавления пользователей", show_alert=True)
            
    elif action == "remove_users":
        success = await remnawave_service.remove_all_users_from_squad(squad_uuid)
        if success:
            await callback.answer("✅ Задача удаления пользователей в очередь")
        else:
            await callback.answer("❌ Ошибка удаления пользователей", show_alert=True)
            
    elif action == "delete":
        success = await remnawave_service.delete_squad(squad_uuid)
        if success:
            await callback.message.edit_text(
                "✅ Сквад успешно удален",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="⬅️ К сквадам", callback_data="admin_rw_squads")]
                ])
            )
        else:
            await callback.answer("❌ Ошибка удаления сквада", show_alert=True)
        return
    
    await show_squad_details(
        types.CallbackQuery(
            id=callback.id,
            from_user=callback.from_user,
            chat_instance=callback.chat_instance,
            data=f"admin_squad_manage_{squad_uuid}",
            message=callback.message
        ),
        db_user,
        db
    )

@admin_required
@error_handler
async def show_squad_edit_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)
    
    if not squad:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    text = f"""
✏️ <b>Редактирование сквада: {squad['name']}</b>

<b>Текущие инбаунды:</b>
"""
    
    if squad.get('inbounds'):
        for inbound in squad['inbounds']:
            text += f"✅ {inbound['tag']} ({inbound['type']})\n"
    else:
        text += "Нет активных инбаундов\n"
    
    text += "\n<b>Доступные действия:</b>"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_squad_edit_keyboard(squad_uuid, db_user.language)
    )
    await callback.answer()

@admin_required
@error_handler
async def show_squad_inbounds_selection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    
    squad = await remnawave_service.get_squad_details(squad_uuid)
    all_inbounds = await remnawave_service.get_all_inbounds()
    
    if not squad:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    if not all_inbounds:
        await callback.answer("❌ Нет доступных инбаундов", show_alert=True)
        return
    
    if squad_uuid not in squad_inbound_selections:
        squad_inbound_selections[squad_uuid] = set(
            inbound['uuid'] for inbound in squad.get('inbounds', [])
        )
    
    text = f"""
🔧 <b>Изменение инбаундов</b>

<b>Сквад:</b> {squad['name']}
<b>Текущих инбаундов:</b> {len(squad_inbound_selections[squad_uuid])}

<b>Доступные инбаунды:</b>
"""
    
    keyboard = []
    
    for i, inbound in enumerate(all_inbounds[:15]): 
        is_selected = inbound['uuid'] in squad_inbound_selections[squad_uuid]
        emoji = "✅" if is_selected else "☐"
        
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"{emoji} {inbound['tag']} ({inbound['type']})",
                callback_data=f"sqd_tgl_{i}_{squad_uuid[:8]}"
            )
        ])
    
    if len(all_inbounds) > 15:
        text += f"\n⚠️ Показано первые 15 из {len(all_inbounds)} инбаундов"
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="💾 Сохранить изменения", callback_data=f"sqd_save_{squad_uuid[:8]}")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sqd_edit_{squad_uuid[:8]}")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def show_squad_rename_form(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)
    
    if not squad:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    await state.update_data(squad_uuid=squad_uuid, squad_name=squad['name'])
    await state.set_state(SquadRenameStates.waiting_for_new_name)
    
    text = f"""
✏️ <b>Переименование сквада</b>

<b>Текущее название:</b> {squad['name']}

📝 <b>Введите новое название сквада:</b>

<i>Требования к названию:</i>
• От 2 до 20 символов
• Только буквы, цифры, дефисы и подчеркивания
• Без пробелов и специальных символов

Отправьте сообщение с новым названием или нажмите "Отмена" для выхода.
"""
    
    keyboard = [
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_rename_{squad_uuid}")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def cancel_squad_rename(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    squad_uuid = callback.data.split('_')[-1]
    
    await state.clear()
    
    new_callback = types.CallbackQuery(
        id=callback.id,
        from_user=callback.from_user,
        chat_instance=callback.chat_instance,
        data=f"squad_edit_{squad_uuid}",
        message=callback.message
    )
    
    await show_squad_edit_menu(new_callback, db_user, db)

@admin_required
@error_handler
async def process_squad_new_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    data = await state.get_data()
    squad_uuid = data.get('squad_uuid')
    old_name = data.get('squad_name')
    
    if not squad_uuid:
        await message.answer("❌ Ошибка: сквад не найден")
        await state.clear()
        return
    
    new_name = message.text.strip()
    
    if not new_name:
        await message.answer("❌ Название не может быть пустым. Попробуйте еще раз:")
        return
    
    if len(new_name) < 2 or len(new_name) > 20:
        await message.answer("❌ Название должно быть от 2 до 20 символов. Попробуйте еще раз:")
        return
    
    import re
    if not re.match(r'^[A-Za-z0-9_-]+$', new_name):
        await message.answer("❌ Название может содержать только буквы, цифры, дефисы и подчеркивания. Попробуйте еще раз:")
        return
    
    if new_name == old_name:
        await message.answer("❌ Новое название совпадает с текущим. Введите другое название:")
        return
    
    remnawave_service = RemnaWaveService()
    success = await remnawave_service.rename_squad(squad_uuid, new_name)
    
    if success:
        await message.answer(
            f"✅ <b>Сквад успешно переименован!</b>\n\n"
            f"<b>Старое название:</b> {old_name}\n"
            f"<b>Новое название:</b> {new_name}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📋 Детали сквада", callback_data=f"admin_squad_manage_{squad_uuid}")],
                [types.InlineKeyboardButton(text="⬅️ К сквадам", callback_data="admin_rw_squads")]
            ])
        )
        await state.clear()
    else:
        await message.answer(
            "❌ <b>Ошибка переименования сквада</b>\n\n"
            "Возможные причины:\n"
            "• Сквад с таким названием уже существует\n"
            "• Проблемы с подключением к API\n"
            "• Недостаточно прав\n\n"
            "Попробуйте другое название:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_rename_{squad_uuid}")]
            ])
        )


@admin_required
@error_handler
async def toggle_squad_inbound(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    parts = callback.data.split('_')
    inbound_index = int(parts[2])
    short_squad_uuid = parts[3]
    
    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()
    
    full_squad_uuid = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            break
    
    if not full_squad_uuid:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    all_inbounds = await remnawave_service.get_all_inbounds()
    if inbound_index >= len(all_inbounds):
        await callback.answer("❌ Инбаунд не найден", show_alert=True)
        return
    
    selected_inbound = all_inbounds[inbound_index]
    
    if full_squad_uuid not in squad_inbound_selections:
        squad_inbound_selections[full_squad_uuid] = set()
    
    if selected_inbound['uuid'] in squad_inbound_selections[full_squad_uuid]:
        squad_inbound_selections[full_squad_uuid].remove(selected_inbound['uuid'])
        await callback.answer(f"➖ Убран: {selected_inbound['tag']}")
    else:
        squad_inbound_selections[full_squad_uuid].add(selected_inbound['uuid'])
        await callback.answer(f"➕ Добавлен: {selected_inbound['tag']}")
    
    text = f"""
🔧 <b>Изменение инбаундов</b>

<b>Сквад:</b> {squads[0]['name'] if squads else 'Неизвестно'}
<b>Выбрано инбаундов:</b> {len(squad_inbound_selections[full_squad_uuid])}

<b>Доступные инбаунды:</b>
"""
    
    keyboard = []
    for i, inbound in enumerate(all_inbounds[:15]):
        is_selected = inbound['uuid'] in squad_inbound_selections[full_squad_uuid]
        emoji = "✅" if is_selected else "☐"
        
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"{emoji} {inbound['tag']} ({inbound['type']})",
                callback_data=f"sqd_tgl_{i}_{short_squad_uuid}"
            )
        ])
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="💾 Сохранить изменения", callback_data=f"sqd_save_{short_squad_uuid}")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sqd_edit_{short_squad_uuid}")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@admin_required  
@error_handler
async def save_squad_inbounds(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    short_squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()
    
    full_squad_uuid = None
    squad_name = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            squad_name = squad['name']
            break
    
    if not full_squad_uuid:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    selected_inbounds = squad_inbound_selections.get(full_squad_uuid, set())
    
    try:
        success = await remnawave_service.update_squad_inbounds(full_squad_uuid, list(selected_inbounds))
        
        if success:
            if full_squad_uuid in squad_inbound_selections:
                del squad_inbound_selections[full_squad_uuid]
            
            await callback.message.edit_text(
                f"✅ <b>Инбаунды сквада обновлены</b>\n\n"
                f"<b>Сквад:</b> {squad_name}\n"
                f"<b>Количество инбаундов:</b> {len(selected_inbounds)}",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="⬅️ К сквадам", callback_data="admin_rw_squads")],
                    [types.InlineKeyboardButton(text="📋 Детали сквада", callback_data=f"admin_squad_manage_{full_squad_uuid}")]
                ])
            )
            await callback.answer("✅ Изменения сохранены!")
        else:
            await callback.answer("❌ Ошибка сохранения изменений", show_alert=True)
            
    except Exception as e:
        logger.error(f"Error saving squad inbounds: {e}")
        await callback.answer("❌ Ошибка при сохранении", show_alert=True)

@admin_required
@error_handler
async def show_squad_edit_menu_short(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    short_squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()
    
    full_squad_uuid = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            break
    
    if not full_squad_uuid:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    new_callback = types.CallbackQuery(
        id=callback.id,
        from_user=callback.from_user,
        chat_instance=callback.chat_instance,
        data=f"squad_edit_{full_squad_uuid}",
        message=callback.message
    )
    
    await show_squad_edit_menu(new_callback, db_user, db)

@admin_required
@error_handler
async def start_squad_creation(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    await state.set_state(SquadCreateStates.waiting_for_name)
    
    text = """
➕ <b>Создание нового сквада</b>

<b>Шаг 1 из 2: Название сквада</b>

📝 <b>Введите название для нового сквада:</b>

<i>Требования к названию:</i>
• От 2 до 20 символов
• Только буквы, цифры, дефисы и подчеркивания
• Без пробелов и специальных символов

Отправьте сообщение с названием или нажмите "Отмена" для выхода.
"""
    
    keyboard = [
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_squad_create")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def process_squad_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    squad_name = message.text.strip()
    
    if not squad_name:
        await message.answer("❌ Название не может быть пустым. Попробуйте еще раз:")
        return
    
    if len(squad_name) < 2 or len(squad_name) > 20:
        await message.answer("❌ Название должно быть от 2 до 20 символов. Попробуйте еще раз:")
        return
    
    import re
    if not re.match(r'^[A-Za-z0-9_-]+$', squad_name):
        await message.answer("❌ Название может содержать только буквы, цифры, дефисы и подчеркивания. Попробуйте еще раз:")
        return
    
    await state.update_data(squad_name=squad_name)
    await state.set_state(SquadCreateStates.selecting_inbounds)
    
    user_id = message.from_user.id
    squad_create_data[user_id] = {'name': squad_name, 'selected_inbounds': set()}
    
    remnawave_service = RemnaWaveService()
    all_inbounds = await remnawave_service.get_all_inbounds()
    
    if not all_inbounds:
        await message.answer(
            "❌ <b>Нет доступных инбаундов</b>\n\n"
            "Для создания сквада необходимо иметь хотя бы один инбаунд.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ К сквадам", callback_data="admin_rw_squads")]
            ])
        )
        await state.clear()
        return
    
    text = f"""
➕ <b>Создание сквада: {squad_name}</b>

<b>Шаг 2 из 2: Выбор инбаундов</b>

<b>Выбрано инбаундов:</b> 0

<b>Доступные инбаунды:</b>
"""
    
    keyboard = []
    
    for i, inbound in enumerate(all_inbounds[:15]): 
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"☐ {inbound['tag']} ({inbound['type']})",
                callback_data=f"create_tgl_{i}"
            )
        ])
    
    if len(all_inbounds) > 15:
        text += f"\n⚠️ Показано первые 15 из {len(all_inbounds)} инбаундов"
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="✅ Создать сквад", callback_data="create_squad_finish")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_squad_create")]
    ])
    
    await message.answer(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@admin_required
@error_handler
async def toggle_create_inbound(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    inbound_index = int(callback.data.split('_')[-1])
    user_id = callback.from_user.id
    
    if user_id not in squad_create_data:
        await callback.answer("❌ Ошибка: данные сессии не найдены", show_alert=True)
        await state.clear()
        return
    
    remnawave_service = RemnaWaveService()
    all_inbounds = await remnawave_service.get_all_inbounds()
    
    if inbound_index >= len(all_inbounds):
        await callback.answer("❌ Инбаунд не найден", show_alert=True)
        return
    
    selected_inbound = all_inbounds[inbound_index]
    selected_inbounds = squad_create_data[user_id]['selected_inbounds']
    
    if selected_inbound['uuid'] in selected_inbounds:
        selected_inbounds.remove(selected_inbound['uuid'])
        await callback.answer(f"➖ Убран: {selected_inbound['tag']}")
    else:
        selected_inbounds.add(selected_inbound['uuid'])
        await callback.answer(f"➕ Добавлен: {selected_inbound['tag']}")
    
    squad_name = squad_create_data[user_id]['name']
    
    text = f"""
➕ <b>Создание сквада: {squad_name}</b>

<b>Шаг 2 из 2: Выбор инбаундов</b>

<b>Выбрано инбаундов:</b> {len(selected_inbounds)}

<b>Доступные инбаунды:</b>
"""
    
    keyboard = []
    
    for i, inbound in enumerate(all_inbounds[:15]):
        is_selected = inbound['uuid'] in selected_inbounds
        emoji = "✅" if is_selected else "☐"
        
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"{emoji} {inbound['tag']} ({inbound['type']})",
                callback_data=f"create_tgl_{i}"
            )
        ])
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="✅ Создать сквад", callback_data="create_squad_finish")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_squad_create")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@admin_required
@error_handler
async def finish_squad_creation(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    user_id = callback.from_user.id
    
    if user_id not in squad_create_data:
        await callback.answer("❌ Ошибка: данные сессии не найдены", show_alert=True)
        await state.clear()
        return
    
    squad_name = squad_create_data[user_id]['name']
    selected_inbounds = list(squad_create_data[user_id]['selected_inbounds'])
    
    if not selected_inbounds:
        await callback.answer("❌ Необходимо выбрать хотя бы один инбаунд", show_alert=True)
        return
    
    remnawave_service = RemnaWaveService()
    success = await remnawave_service.create_squad(squad_name, selected_inbounds)
    
    if user_id in squad_create_data:
        del squad_create_data[user_id]
    await state.clear()
    
    if success:
        await callback.message.edit_text(
            f"✅ <b>Сквад успешно создан!</b>\n\n"
            f"<b>Название:</b> {squad_name}\n"
            f"<b>Количество инбаундов:</b> {len(selected_inbounds)}\n\n"
            f"Сквад готов к использованию!",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📋 Список сквадов", callback_data="admin_rw_squads")],
                [types.InlineKeyboardButton(text="⬅️ К панели RemnaWave", callback_data="admin_remnawave")]
            ])
        )
        await callback.answer("✅ Сквад создан!")
    else:
        await callback.message.edit_text(
            f"❌ <b>Ошибка создания сквада</b>\n\n"
            f"<b>Название:</b> {squad_name}\n\n"
            f"Возможные причины:\n"
            f"• Сквад с таким названием уже существует\n"
            f"• Проблемы с подключением к API\n"
            f"• Недостаточно прав\n"
            f"• Некорректные инбаунды",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="admin_squad_create")],
                [types.InlineKeyboardButton(text="⬅️ К сквадам", callback_data="admin_rw_squads")]
            ])
        )
        await callback.answer("❌ Ошибка создания сквада", show_alert=True)

@admin_required
@error_handler
async def cancel_squad_creation(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    user_id = callback.from_user.id
    
    if user_id in squad_create_data:
        del squad_create_data[user_id]
    await state.clear()
    
    await show_squads_management(callback, db_user, db)


@admin_required
@error_handler
async def restart_all_nodes(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   remnawave_service = RemnaWaveService()
   success = await remnawave_service.restart_all_nodes()
   
   if success:
       await callback.message.edit_text(
           "✅ Команда перезагрузки всех нод отправлена",
           reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
               [types.InlineKeyboardButton(text="⬅️ К нодам", callback_data="admin_rw_nodes")]
           ])
       )
   else:
       await callback.message.edit_text(
           "❌ Ошибка перезагрузки нод",
           reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
               [types.InlineKeyboardButton(text="⬅️ К нодам", callback_data="admin_rw_nodes")]
           ])
       )
   
   await callback.answer()


@admin_required
@error_handler
async def show_sync_options(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    text = """
🔄 <b>Синхронизация с RemnaWave</b>

Выберите тип синхронизации:

🔄 <b>Синхронизировать всех</b>
• Полная синхронизация всех пользователей
• Создание новых пользователей из панели
• Обновление данных существующих
• Удаление неактуальных подписок
• ⏱️ Время выполнения: 2-5 минут

🆕 <b>Только новых</b>
• Создание пользователей из панели, которых нет в боте
• Быстрое добавление при массовой регистрации
• ⏱️ Время выполнения: 30 секунд - 2 минуты

📈 <b>Обновить данные</b>
• Обновление информации о трафике и подписках
• Синхронизация статуса и лимитов
• Обновление подключенных сквадов
• ⏱️ Время выполнения: 1-3 минуты

🔍 <b>Валидация подписок</b>
• Проверка и исправление проблем в данных
• Восстановление отсутствующих полей
• Исправление некорректных статусов

🧹 <b>Мягкая очистка</b>  
• Деактивация подписок отсутствующих в панели
• Сохранение транзакций и истории

🗑️ <b>ПРИНУДИТЕЛЬНАЯ ОЧИСТКА</b>
• ⚠️ ОПАСНО: Полное удаление данных пользователей
• Удаление транзакций, балансов, рефералов  
• Только при серьезных проблемах синхронизации

⚠️ <b>Важно:</b>
• Во время синхронизации не выполняйте другие операции
• При полной синхронизации подписки пользователей, отсутствующих в панели, будут деактивированы
• Рекомендуется делать полную синхронизацию ежедневно
"""
    
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Синхронизировать всех", callback_data="sync_all_users")],
        [types.InlineKeyboardButton(text="🆕 Только новых", callback_data="sync_new_users")],
        [types.InlineKeyboardButton(text="📈 Обновить данные", callback_data="sync_update_data")],
        [types.InlineKeyboardButton(text="🔍 Валидация подписок", callback_data="sync_validate")],
        [types.InlineKeyboardButton(text="🧹 Мягкая очистка", callback_data="sync_cleanup")],
        [types.InlineKeyboardButton(text="🗑️ ПРИНУДИТЕЛЬНАЯ ОЧИСТКА", callback_data="confirm_force_cleanup")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def show_sync_recommendations(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    await callback.message.edit_text(
        "🔍 Анализируем состояние синхронизации...",
        reply_markup=None
    )
    
    remnawave_service = RemnaWaveService()
    recommendations = await remnawave_service.get_sync_recommendations(db)
    
    priority_emoji = {
        "low": "🟢",
        "medium": "🟡", 
        "high": "🔴"
    }
    
    text = f"""
💡 <b>Рекомендации по синхронизации</b>

{priority_emoji.get(recommendations['priority'], '🟢')} <b>Приоритет:</b> {recommendations['priority'].upper()}
⏱️ <b>Время выполнения:</b> {recommendations['estimated_time']}

<b>Рекомендуемое действие:</b>
"""
    
    if recommendations['sync_type'] == 'all':
        text += "🔄 Полная синхронизация"
    elif recommendations['sync_type'] == 'update_only':
        text += "📈 Обновление данных"
    elif recommendations['sync_type'] == 'new_only':
        text += "🆕 Синхронизация новых"
    else:
        text += "✅ Синхронизация не требуется"
    
    text += "\n\n<b>Причины:</b>\n"
    for reason in recommendations['reasons']:
        text += f"• {reason}\n"
    
    keyboard = []
    
    if recommendations['should_sync'] and recommendations['sync_type'] != 'none':
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"✅ Выполнить рекомендацию", 
                callback_data=f"sync_{recommendations['sync_type']}_users" if recommendations['sync_type'] != 'update_only' else "sync_update_data"
            )
        ])
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="🔄 Другие опции", callback_data="admin_rw_sync")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def validate_subscriptions(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    await callback.message.edit_text(
        "🔍 Выполняется валидация подписок...\n\nПроверяем данные, может занять несколько минут.",
        reply_markup=None
    )
    
    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.validate_and_fix_subscriptions(db)
    
    if stats['errors'] == 0:
        status_emoji = "✅"
        status_text = "успешно завершена"
    else:
        status_emoji = "⚠️"
        status_text = "завершена с ошибками"
    
    text = f"""
{status_emoji} <b>Валидация {status_text}</b>

📊 <b>Результаты:</b>
• 🔍 Проверено подписок: {stats['checked']}
• 🔧 Исправлено подписок: {stats['fixed']}
• ⚠️ Найдено проблем: {stats['issues_found']}
• ❌ Ошибок: {stats['errors']}
"""
    
    if stats['fixed'] > 0:
        text += "\n✅ <b>Исправленные проблемы:</b>\n"
        text += "• Статусы просроченных подписок\n"
        text += "• Отсутствующие данные RemnaWave\n" 
        text += "• Некорректные лимиты трафика\n"
        text += "• Настройки устройств\n"
    
    if stats['errors'] > 0:
        text += f"\n⚠️ Обнаружены ошибки при обработке.\nПроверьте логи для подробной информации."
    
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Повторить валидацию", callback_data="sync_validate")],
        [types.InlineKeyboardButton(text="🔄 Полная синхронизация", callback_data="sync_all_users")],
        [types.InlineKeyboardButton(text="⬅️ К синхронизации", callback_data="admin_rw_sync")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def cleanup_subscriptions(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    await callback.message.edit_text(
        "🧹 Выполняется очистка неактуальных подписок...\n\nУдаляем подписки пользователей, отсутствующих в панели.",
        reply_markup=None
    )
    
    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.cleanup_orphaned_subscriptions(db)
    
    if stats['errors'] == 0:
        status_emoji = "✅"
        status_text = "успешно завершена"
    else:
        status_emoji = "⚠️"
        status_text = "завершена с ошибками"
    
    text = f"""
{status_emoji} <b>Очистка {status_text}</b>

📊 <b>Результаты:</b>
• 🔍 Проверено подписок: {stats['checked']}
• 🗑️ Деактивировано: {stats['deactivated']}
• ❌ Ошибок: {stats['errors']}
"""
    
    if stats['deactivated'] > 0:
        text += f"\n🗑️ <b>Деактивированные подписки:</b>\n"
        text += f"Отключены подписки пользователей, которые\n"
        text += f"отсутствуют в панели RemnaWave.\n"
    else:
        text += f"\n✅ Все подписки актуальны!\nНеактуальных подписок не найдено."
    
    if stats['errors'] > 0:
        text += f"\n⚠️ Обнаружены ошибки при обработке.\nПроверьте логи для подробной информации."
    
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Повторить очистку", callback_data="sync_cleanup")],
        [types.InlineKeyboardButton(text="🔍 Валидация", callback_data="sync_validate")],
        [types.InlineKeyboardButton(text="⬅️ К синхронизации", callback_data="admin_rw_sync")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def force_cleanup_all_orphaned_users(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    await callback.message.edit_text(
        "🗑️ Выполняется принудительная очистка всех пользователей, отсутствующих в панели...\n\n"
        "⚠️ ВНИМАНИЕ: Это полностью удалит ВСЕ данные пользователей!\n"
        "📊 Включая: транзакции, реферальные доходы, промокоды, серверы, балансы\n\n"
        "⏳ Пожалуйста, подождите...",
        reply_markup=None
    )
    
    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.cleanup_orphaned_subscriptions(db)
    
    if stats['errors'] == 0:
        status_emoji = "✅"
        status_text = "успешно завершена"
    else:
        status_emoji = "⚠️"
        status_text = "завершена с ошибками"
    
    text = f"""
{status_emoji} <b>Принудительная очистка {status_text}</b>

📊 <b>Результаты:</b>
• 🔍 Проверено подписок: {stats['checked']}
• 🗑️ Полностью очищено: {stats['deactivated']}
• ❌ Ошибок: {stats['errors']}
"""
    
    if stats['deactivated'] > 0:
        text += f"""

🗑️ <b>Полностью очищенные данные:</b>
• Подписки сброшены к начальному состоянию
• Удалены ВСЕ транзакции пользователей
• Удалены ВСЕ реферальные доходы  
• Удалены использования промокодов
• Сброшены балансы к нулю
• Удалены подключенные серверы
• Сброшены HWID устройства в RemnaWave
• Очищены RemnaWave UUID
"""
    else:
        text += f"\n✅ Неактуальных подписок не найдено!\nВсе пользователи синхронизированы с панелью."
    
    if stats['errors'] > 0:
        text += f"\n⚠️ Обнаружены ошибки при обработке.\nПроверьте логи для подробной информации."
    
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Повторить очистку", callback_data="force_cleanup_orphaned")],
        [types.InlineKeyboardButton(text="🔄 Полная синхронизация", callback_data="sync_all_users")],
        [types.InlineKeyboardButton(text="⬅️ К синхронизации", callback_data="admin_rw_sync")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_force_cleanup(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    text = """
⚠️ <b>ВНИМАНИЕ! ОПАСНАЯ ОПЕРАЦИЯ!</b>

🗑️ <b>Принудительная очистка полностью удалит:</b>
• ВСЕ транзакции пользователей отсутствующих в панели
• ВСЕ реферальные доходы и связи
• ВСЕ использования промокодов
• ВСЕ подключенные серверы подписок
• ВСЕ балансы (сброс к нулю)
• ВСЕ HWID устройства в RemnaWave
• ВСЕ RemnaWave UUID и ссылки

⚡ <b>Это действие НЕОБРАТИМО!</b>

Используйте только если:
• Обычная синхронизация не помогает
• Нужно полностью очистить "мусорные" данные
• После массового удаления пользователей из панели

❓ <b>Вы действительно хотите продолжить?</b>
"""
    
    keyboard = [
        [types.InlineKeyboardButton(text="🗑️ ДА, ОЧИСТИТЬ ВСЕ", callback_data="force_cleanup_orphaned")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_rw_sync")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def sync_users(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   sync_type = callback.data.split('_')[-2] + "_" + callback.data.split('_')[-1]
   
   progress_text = "🔄 Выполняется синхронизация...\n\n"
   
   if sync_type == "all_users":
       progress_text += "📋 Тип: Полная синхронизация\n"
       progress_text += "• Создание новых пользователей\n"
       progress_text += "• Обновление существующих\n"
       progress_text += "• Удаление неактуальных подписок\n"
   elif sync_type == "new_users":
       progress_text += "📋 Тип: Только новые пользователи\n"
       progress_text += "• Создание пользователей из панели\n"
   elif sync_type == "update_data":
       progress_text += "📋 Тип: Обновление данных\n"
       progress_text += "• Обновление информации о трафике\n"
       progress_text += "• Синхронизация подписок\n"
   
   progress_text += "\n⏳ Пожалуйста, подождите..."
   
   await callback.message.edit_text(
       progress_text,
       reply_markup=None
   )
   
   remnawave_service = RemnaWaveService()
   
   sync_map = {
       "all_users": "all",
       "new_users": "new_only", 
       "update_data": "update_only"
   }
   
   stats = await remnawave_service.sync_users_from_panel(db, sync_map.get(sync_type, "all"))
   
   total_operations = stats['created'] + stats['updated'] + stats.get('deleted', 0)
   success_operations = stats['created'] + stats['updated'] + stats.get('deleted', 0)
   
   if stats['errors'] == 0:
       status_emoji = "✅"
       status_text = "успешно завершена"
   elif stats['errors'] < total_operations:
       status_emoji = "⚠️"
       status_text = "завершена с предупреждениями"
   else:
       status_emoji = "❌"
       status_text = "завершена с ошибками"
   
   text = f"""
{status_emoji} <b>Синхронизация {status_text}</b>

📊 <b>Результат:</b>
"""
   
   if sync_type == "all_users":
       text += f"• 🆕 Создано: {stats['created']}\n"
       text += f"• 🔄 Обновлено: {stats['updated']}\n"
       if 'deleted' in stats:
           text += f"• 🗑️ Удалено: {stats['deleted']}\n"
       text += f"• ❌ Ошибок: {stats['errors']}\n"
   elif sync_type == "new_users":
       text += f"• 🆕 Создано: {stats['created']}\n"
       text += f"• ❌ Ошибок: {stats['errors']}\n"
       if stats['created'] == 0 and stats['errors'] == 0:
           text += "\n💡 Новых пользователей не найдено"
   elif sync_type == "update_data":
       text += f"• 🔄 Обновлено: {stats['updated']}\n"
       text += f"• ❌ Ошибок: {stats['errors']}\n"
       if stats['updated'] == 0 and stats['errors'] == 0:
           text += "\n💡 Все данные актуальны"
   
   if stats['errors'] > 0:
       text += f"\n⚠️ <b>Внимание:</b>\n"
       text += f"Некоторые операции завершились с ошибками.\n"
       text += f"Проверьте логи для получения подробной информации."
   
   if sync_type == "all_users" and 'deleted' in stats and stats['deleted'] > 0:
       text += f"\n🗑️ <b>Удаленные подписки:</b>\n"
       text += f"Деактивированы подписки пользователей,\n"
       text += f"которые отсутствуют в панели RemnaWave."
   
   text += f"\n\n💡 <b>Рекомендации:</b>\n"
   if sync_type == "all_users":
       text += "• Полная синхронизация выполнена\n"
       text += "• Рекомендуется запускать раз в день\n"
   elif sync_type == "new_users":
       text += "• Синхронизация новых пользователей\n"
       text += "• Используйте при массовом добавлении\n"
   elif sync_type == "update_data":
       text += "• Обновление данных о трафике\n"
       text += "• Запускайте для актуализации статистики\n"
   
   keyboard = []
   
   if stats['errors'] > 0:
       keyboard.append([
           types.InlineKeyboardButton(
               text="🔄 Повторить синхронизацию", 
               callback_data=callback.data
           )
       ])
   
   if sync_type != "all_users":
       keyboard.append([
           types.InlineKeyboardButton(
               text="🔄 Полная синхронизация", 
               callback_data="sync_all_users"
           )
       ])
   
   keyboard.extend([
       [
           types.InlineKeyboardButton(text="📊 Статистика системы", callback_data="admin_rw_system"),
           types.InlineKeyboardButton(text="🌐 Ноды", callback_data="admin_rw_nodes")
       ],
       [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
   ])
   
   await callback.message.edit_text(
       text,
       reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
   )
   await callback.answer()


@admin_required
@error_handler
async def show_squads_management(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   remnawave_service = RemnaWaveService()
   squads = await remnawave_service.get_all_squads()
   
   text = "🌍 <b>Управление сквадами</b>\n\n"
   keyboard = []
   
   if squads:
       for squad in squads:
           text += f"🔹 <b>{squad['name']}</b>\n"
           text += f"👥 Участников: {squad['members_count']}\n"
           text += f"📡 Инбаундов: {squad['inbounds_count']}\n\n"
           
           keyboard.append([
               types.InlineKeyboardButton(
                   text=f"⚙️ {squad['name']}",
                   callback_data=f"admin_squad_manage_{squad['uuid']}"
               )
           ])
   else:
       text += "Сквады не найдены"
   
   keyboard.extend([
       [types.InlineKeyboardButton(text="➕ Создать сквад", callback_data="admin_squad_create")],
       [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
   ])
   
   await callback.message.edit_text(
       text,
       reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
   )
   await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_remnawave_menu, F.data == "admin_remnawave")
    dp.callback_query.register(show_system_stats, F.data == "admin_rw_system")
    dp.callback_query.register(show_traffic_stats, F.data == "admin_rw_traffic")
    dp.callback_query.register(show_nodes_management, F.data == "admin_rw_nodes")
    dp.callback_query.register(show_node_details, F.data.startswith("admin_node_manage_"))
    dp.callback_query.register(show_node_statistics, F.data.startswith("node_stats_"))
    dp.callback_query.register(manage_node, F.data.startswith("node_enable_"))
    dp.callback_query.register(manage_node, F.data.startswith("node_disable_"))
    dp.callback_query.register(manage_node, F.data.startswith("node_restart_"))
    dp.callback_query.register(restart_all_nodes, F.data == "admin_restart_all_nodes")
    dp.callback_query.register(show_sync_options, F.data == "admin_rw_sync")
    dp.callback_query.register(sync_users, F.data.startswith("sync_"))
    dp.callback_query.register(show_sync_recommendations, F.data == "sync_recommendations")
    dp.callback_query.register(validate_subscriptions, F.data == "sync_validate") 
    dp.callback_query.register(cleanup_subscriptions, F.data == "sync_cleanup")
    dp.callback_query.register(confirm_force_cleanup, F.data == "confirm_force_cleanup")
    dp.callback_query.register(force_cleanup_all_orphaned_users, F.data == "force_cleanup_orphaned")
    dp.callback_query.register(show_squads_management, F.data == "admin_rw_squads")
    
    dp.callback_query.register(show_squad_details, F.data.startswith("admin_squad_manage_"))
    
    dp.callback_query.register(manage_squad_action, F.data.startswith("squad_add_users_"))
    dp.callback_query.register(manage_squad_action, F.data.startswith("squad_remove_users_"))
    dp.callback_query.register(manage_squad_action, F.data.startswith("squad_delete_"))
    
    dp.callback_query.register(show_squad_edit_menu, F.data.startswith("squad_edit_") & ~F.data.startswith("squad_edit_inbounds_"))
    
    dp.callback_query.register(show_squad_inbounds_selection, F.data.startswith("squad_edit_inbounds_"))
    dp.callback_query.register(show_squad_rename_form, F.data.startswith("squad_rename_"))
    
    dp.callback_query.register(cancel_squad_rename, F.data.startswith("cancel_rename_"))
    
    dp.callback_query.register(toggle_squad_inbound, F.data.startswith("sqd_tgl_"))
    dp.callback_query.register(save_squad_inbounds, F.data.startswith("sqd_save_"))
    
    dp.callback_query.register(show_squad_edit_menu_short, F.data.startswith("sqd_edit_"))
    
    
    dp.callback_query.register(start_squad_creation, F.data == "admin_squad_create")
    
    dp.callback_query.register(cancel_squad_creation, F.data == "cancel_squad_create")
    
    dp.callback_query.register(toggle_create_inbound, F.data.startswith("create_tgl_"))
    
    dp.callback_query.register(finish_squad_creation, F.data == "create_squad_finish")
    
    dp.message.register(
        process_squad_new_name, 
        SquadRenameStates.waiting_for_new_name,
        F.text
    )
    
    dp.message.register(
        process_squad_name,
        SquadCreateStates.waiting_for_name,
        F.text
    )
