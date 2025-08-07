from database import Subscription
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Optional, Dict
from translations import t

def language_keyboard() -> InlineKeyboardMarkup:
    """Language selection keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")
        ]
    ])
    return keyboard

def main_menu_keyboard(lang: str = 'ru', is_admin: bool = False, show_trial: bool = False) -> InlineKeyboardMarkup:
    """Beautiful main menu keyboard with emojis and better layout"""
    buttons = [
        # Первый ряд - основные функции
        [
            InlineKeyboardButton(text="💰 " + t('balance', lang), callback_data="balance"),
            InlineKeyboardButton(text="📋 " + t('my_subscriptions', lang), callback_data="my_subscriptions")
        ],
        # Второй ряд - покупка подписки (выделена отдельно как главная функция)
        [InlineKeyboardButton(text="🛒 " + t('buy_subscription', lang), callback_data="buy_subscription")],
    ]

    # Добавляем тестовую подписку если доступна
    if show_trial:
        buttons.insert(1, [InlineKeyboardButton(text="🆓 Тестовая подписка", callback_data="trial_subscription")])

    # Добавляем остальные кнопки
    buttons.extend([
        # Дополнительные функции
        [
            InlineKeyboardButton(text="🎁 " + t('promocode', lang), callback_data="promocode"),
            InlineKeyboardButton(text="💬 " + t('support', lang), callback_data="support")
        ],
        # Последний ряд - настройки
        [InlineKeyboardButton(text="🌐 " + t('change_language', lang), callback_data="change_language")]
    ])

    # Добавляем админ панель если пользователь админ
    if is_admin:
        buttons.append([InlineKeyboardButton(text="⚙️ " + t('admin_panel', lang), callback_data="admin_panel")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Остальные функции остаются прежними...
def balance_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful balance menu keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 " + t('topup_balance', lang), callback_data="topup_balance")],
        [InlineKeyboardButton(text="📊 " + t('payment_history', lang), callback_data="payment_history")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="main_menu")]
    ])
    return keyboard

def topup_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful top up balance keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 " + t('topup_card', lang), callback_data="topup_card")],
        [InlineKeyboardButton(text="👨‍💼 " + t('topup_support', lang), callback_data="topup_support")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="balance")]
    ])
    return keyboard

def subscriptions_keyboard(subscriptions: List[dict], lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful available subscriptions keyboard"""
    buttons = []
    
    # Группируем подписки по две в ряд для компактности
    for i in range(0, len(subscriptions), 2):
        row = []
        for j in range(2):
            if i + j < len(subscriptions):
                sub = subscriptions[i + j]
                price_text = f"{sub['price']:.0f}₽"
                # Используем разные эмодзи для разных ценовых категорий
                if sub['price'] <= 100:
                    emoji = "🥉"
                elif sub['price'] <= 300:
                    emoji = "🥈"
                else:
                    emoji = "🥇"
                
                row.append(InlineKeyboardButton(
                    text=f"{emoji} {sub['name']} - {price_text}",
                    callback_data=f"buy_sub_{sub['id']}"
                ))
        buttons.append(row)
    
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def subscription_detail_keyboard(subscription_id: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful subscription detail keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💎 " + t('buy_subscription_btn', lang, price=0), 
            callback_data=f"confirm_buy_{subscription_id}"
        )],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="buy_subscription")]
    ])
    return keyboard

def user_subscriptions_keyboard(user_subscriptions: List[dict], lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful user's subscriptions keyboard"""
    buttons = []
    
    for sub in user_subscriptions:
        # Добавляем эмодзи статуса для каждой подписки
        buttons.append([InlineKeyboardButton(
            text=f"📱 {sub['name']}",
            callback_data=f"view_sub_{sub['id']}"
        )])
    
    if not user_subscriptions:
        # Если нет подписок, показываем кнопку покупки
        buttons.append([InlineKeyboardButton(text="🛒 " + t('buy_subscription', lang), callback_data="buy_subscription")])
    
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def user_subscription_detail_keyboard(subscription_id: int, lang: str = 'ru', show_extend: bool = False) -> InlineKeyboardMarkup:
    """Beautiful user's subscription detail keyboard with connection and optional extend button"""
    buttons = []
    
    # Add extend button if subscription is expiring soon
    if show_extend:
        buttons.append([InlineKeyboardButton(text="⏰ " + t('extend_subscription', lang), callback_data=f"extend_sub_{subscription_id}")])
    
    # Connection button (главная кнопка)
    buttons.append([InlineKeyboardButton(text="🔗 Получить ссылку подключения", callback_data=f"get_connection_{subscription_id}")])
    
    # Back button
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="my_subscriptions")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def extend_subscription_keyboard(subscription_id: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful extend subscription confirmation keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, продлить", callback_data=f"confirm_extend_{subscription_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_sub_{subscription_id}")
        ]
    ])
    return keyboard

def back_keyboard(callback_data: str, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful back button keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data=callback_data)]
    ])
    return keyboard

def cancel_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful cancel button keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ " + t('cancel', lang), callback_data="main_menu")]
    ])
    return keyboard

# Admin keyboards

def admin_menu_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful admin menu keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        # Первый ряд - управление контентом
        [
            InlineKeyboardButton(text="📦 " + t('manage_subscriptions', lang), callback_data="admin_subscriptions"),
            InlineKeyboardButton(text="👥 " + t('manage_users', lang), callback_data="admin_users")
        ],
        # Второй ряд - финансы
        [
            InlineKeyboardButton(text="💰 " + t('manage_balance', lang), callback_data="admin_balance"),
            InlineKeyboardButton(text="🎁 " + t('manage_promocodes', lang), callback_data="admin_promocodes")
        ],
        # Третий ряд - коммуникации и система
        [
            InlineKeyboardButton(text="📨 " + t('send_message', lang), callback_data="admin_messages"),
            InlineKeyboardButton(text="🖥 Система RemnaWave", callback_data="admin_system")  # НОВОЕ!
        ],
        # Четвертый ряд - мониторинг и статистика
        [
            InlineKeyboardButton(text="🔍 Мониторинг подписок", callback_data="admin_monitor"),
            InlineKeyboardButton(text="📊 " + t('statistics', lang), callback_data="admin_stats")
        ],
        # Назад
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="main_menu")]
    ])
    return keyboard

def admin_subscriptions_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful admin subscriptions management keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ " + t('create_subscription', lang), callback_data="create_subscription")],
        [InlineKeyboardButton(text="📋 Список подписок", callback_data="list_admin_subscriptions")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def admin_users_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful admin users management keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="list_users")],
        [InlineKeyboardButton(text="🔍 Поиск пользователя", callback_data="search_user")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def admin_balance_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful admin balance management keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Пополнить баланс пользователю", callback_data="admin_add_balance")],
        [InlineKeyboardButton(text="📊 История платежей", callback_data="admin_payment_history")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def admin_promocodes_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful admin promocodes management keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ " + t('create_promocode', lang), callback_data="create_promocode")],
        [InlineKeyboardButton(text="📋 Список промокодов", callback_data="list_promocodes")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def confirmation_keyboard(confirm_callback: str, cancel_callback: str, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful confirmation keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data=confirm_callback),
            InlineKeyboardButton(text="❌ Нет", callback_data=cancel_callback)
        ]
    ])
    return keyboard

def pagination_keyboard(page: int, total_pages: int, prefix: str, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful pagination keyboard"""
    buttons = []
    
    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{prefix}_page_{page-1}"))
        
        nav_buttons.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="noop"))
        
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"{prefix}_page_{page+1}"))
        
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_subscriptions_list_keyboard(subs: List[Subscription], lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful keyboard for admin subscriptions list with enhanced controls"""
    buttons = []
    for sub in subs:
        status_emoji = "🟢" if sub.is_active else "🔴"
        price = f"{sub.price:.0f}₽"
        
        # Основная кнопка с информацией о подписке
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_emoji} {sub.name} — {price}",
                callback_data=f"list_sub_{sub.id}"
            )
        ])
        
        # Кнопки управления для каждой подписки в одну строку
        control_buttons = [
            InlineKeyboardButton(text="✏️", callback_data=f"edit_sub_{sub.id}"),
            InlineKeyboardButton(
                text="🟢" if not sub.is_active else "🔴",
                callback_data=f"toggle_sub_{sub.id}"
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"delete_sub_{sub.id}")
        ]
        
        buttons.append(control_buttons)
    
    # Кнопка создания новой подписки
    buttons.append([InlineKeyboardButton(text="➕ Создать подписку", callback_data="create_subscription")])
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_subscriptions")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_payment_keyboard(payment_id: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful keyboard for admin payment approval"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить платеж", callback_data=f"approve_payment_{payment_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_payment_{payment_id}")
        ]
    ])
    return keyboard

def admin_messages_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful admin messages management keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 " + t('send_to_user', lang), callback_data="admin_send_to_user")],
        [InlineKeyboardButton(text="📢 " + t('send_to_all', lang), callback_data="admin_send_to_all")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def quick_topup_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Quick topup amounts keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 100₽", callback_data="quick_topup_100"),
            InlineKeyboardButton(text="💳 300₽", callback_data="quick_topup_300")
        ],
        [
            InlineKeyboardButton(text="💳 500₽", callback_data="quick_topup_500"),
            InlineKeyboardButton(text="💳 1000₽", callback_data="quick_topup_1000")
        ],
        [InlineKeyboardButton(text="💰 Другая сумма", callback_data="topup_support")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="balance")]
    ])
    return keyboard

def connection_keyboard(subscription_url: str, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful connection keyboard with web app"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключиться", url=subscription_url)],
        [InlineKeyboardButton(text="📱 Инструкция", callback_data="connection_guide")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="my_subscriptions")]
    ])
    return keyboard

def trial_subscription_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Trial subscription confirmation keyboard"""
    buttons = [
        [InlineKeyboardButton(text="✅ Получить тестовую подписку", callback_data="confirm_trial")],
        [InlineKeyboardButton(text=t('back', lang), callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_monitor_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful admin monitor management keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статус сервиса", callback_data="monitor_status")],
        [InlineKeyboardButton(text="🔄 Принудительная проверка", callback_data="monitor_force_check")],
        [InlineKeyboardButton(text="⚰️ Деактивировать истекшие", callback_data="monitor_deactivate_expired")],
        [InlineKeyboardButton(text="👤 Тест для пользователя", callback_data="monitor_test_user")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def admin_system_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Beautiful admin system management keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Системная статистика", callback_data="system_stats")],
        [InlineKeyboardButton(text="🖥 Управление нодами", callback_data="nodes_management")],
        [InlineKeyboardButton(text="👥 Пользователи системы", callback_data="system_users")],
        [InlineKeyboardButton(text="🔄 Синхронизация с RemnaWave", callback_data="sync_remnawave")],
        [InlineKeyboardButton(text="🔍 Отладка API", callback_data="debug_api_comprehensive")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def system_stats_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """System statistics keyboard with refresh"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="refresh_system_stats")],
        [InlineKeyboardButton(text="🖥 Ноды", callback_data="nodes_management")],
        [InlineKeyboardButton(text="👥 Системные пользователи", callback_data="system_users")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_system")]
    ])
    return keyboard

def nodes_management_keyboard(nodes: List[Dict], lang: str = 'ru', timestamp: int = None) -> InlineKeyboardMarkup:
    """Improved nodes management keyboard"""
    buttons = []
    
    if nodes:
        # Statistics row
        online_count = len([n for n in nodes if n.get('status') == 'online'])
        total_count = len(nodes)
        
        buttons.append([
            InlineKeyboardButton(
                text=f"📊 Ноды: {online_count}/{total_count} онлайн",
                callback_data="noop"
            )
        ])
        
        # Show first 5 nodes with improved display
        for i, node in enumerate(nodes[:5]):
            status = node.get('status', 'unknown')
            
            # Status emoji based on actual status
            if status == 'online':
                status_emoji = "🟢"
            elif status == 'disabled':
                status_emoji = "⚫"
            elif status == 'disconnected':
                status_emoji = "🔴"
            elif status == 'xray_stopped':
                status_emoji = "🟡"
            else:
                status_emoji = "⚪"
            
            node_name = node.get('name', f'Node-{i+1}')
            node_id = node.get('id', node.get('uuid'))
            
            # Truncate long names
            if len(node_name) > 20:
                display_name = node_name[:17] + "..."
            else:
                display_name = node_name
            
            # CPU/Memory usage if available
            usage_info = ""
            if node.get('cpuUsage'):
                usage_info += f" CPU:{node['cpuUsage']:.0f}%"
            if node.get('memUsage'):
                usage_info += f" MEM:{node['memUsage']:.0f}%"
            
            buttons.append([
                InlineKeyboardButton(
                    text=f"{status_emoji} {display_name}{usage_info}",
                    callback_data=f"node_details_{node_id}"
                ),
                InlineKeyboardButton(
                    text="🔄",
                    callback_data=f"restart_node_{node_id}"
                ),
                InlineKeyboardButton(
                    text="⚙️",
                    callback_data=f"node_settings_{node_id}"
                )
            ])
        
        if len(nodes) > 5:
            buttons.append([
                InlineKeyboardButton(
                    text=f"... и еще {len(nodes) - 5} нод",
                    callback_data="show_all_nodes"
                )
            ])
    else:
        buttons.append([
            InlineKeyboardButton(
                text="❌ Ноды не найдены",
                callback_data="noop"
            )
        ])
    
    # Action buttons
    buttons.append([
        InlineKeyboardButton(text="🔄 Перезагрузить все", callback_data="restart_all_nodes"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="nodes_statistics")
    ])
    
    # Refresh button
    refresh_callback = f"refresh_nodes_stats_{timestamp}" if timestamp else "refresh_nodes_stats"
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=refresh_callback)
    ])
    
    # Back button
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_system")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def system_users_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """System users management keyboard - ИСПРАВЛЕНО"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика пользователей", callback_data="users_statistics")],
        [InlineKeyboardButton(text="👥 Список всех пользователей", callback_data="list_all_system_users")],
        [InlineKeyboardButton(text="🔍 Поиск пользователя", callback_data="search_user_uuid")],
        [InlineKeyboardButton(text="🔍 Отладка API пользователей", callback_data="debug_users_api")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_system")]
    ])
    return keyboard

def bulk_operations_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Bulk operations keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data="bulk_reset_traffic")],
        [InlineKeyboardButton(text="❌ Отключить пользователей", callback_data="bulk_disable_users")],
        [InlineKeyboardButton(text="✅ Включить пользователей", callback_data="bulk_enable_users")],
        [InlineKeyboardButton(text="🗑 Удалить пользователей", callback_data="bulk_delete_users")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="system_users")]
    ])
    return keyboard

def confirm_restart_keyboard(node_id: str = None, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Confirmation keyboard for node restart"""
    action = f"confirm_restart_node_{node_id}" if node_id else "confirm_restart_all_nodes"
    back_action = f"node_details_{node_id}" if node_id else "nodes_management"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, перезагрузить", callback_data=action),
            InlineKeyboardButton(text="❌ Отмена", callback_data=back_action)
        ]
    ])
    return keyboard
