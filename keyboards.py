from database import Subscription
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Optional, Dict
from translations import t

def language_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")
        ]
    ])
    return keyboard

def main_menu_keyboard(lang: str = 'ru', is_admin: bool = False, show_trial: bool = False, show_lucky_game: bool = True) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="💰 " + t('balance', lang), callback_data="balance"),
            InlineKeyboardButton(text="📋 " + t('my_subscriptions', lang), callback_data="my_subscriptions")
        ],
        [InlineKeyboardButton(text="🛒 " + t('buy_subscription', lang), callback_data="buy_subscription")],
    ]

    if show_trial:
        buttons.insert(1, [InlineKeyboardButton(text="🆓 Тестовая подписка", callback_data="trial_subscription")])

    if show_lucky_game:
        buttons.append([InlineKeyboardButton(text="🎰 Проверь свою удачу!", callback_data="lucky_game")])

    buttons.extend([
        [
            InlineKeyboardButton(text="🎁 " + t('promocode', lang), callback_data="promocode"),
            InlineKeyboardButton(text="👥 Рефералы", callback_data="referral_program")
        ],
        [
            InlineKeyboardButton(text="💬 " + t('support', lang), callback_data="support")
        ],
        [InlineKeyboardButton(text="🌐 " + t('change_language', lang), callback_data="change_language")]
    ])

    if is_admin:
        buttons.append([InlineKeyboardButton(text="⚙️ " + t('admin_panel', lang), callback_data="admin_panel")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def balance_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 " + t('topup_balance', lang), callback_data="topup_balance")],
        [InlineKeyboardButton(text="📊 " + t('payment_history', lang), callback_data="payment_history")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="main_menu")]
    ])
    return keyboard

def topup_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        # [InlineKeyboardButton(text="💳 " + t('topup_card', lang), callback_data="topup_card")],
        [InlineKeyboardButton(text="👨‍💼 " + t('topup_support', lang), callback_data="topup_support")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="balance")]
    ])
    return keyboard

def subscriptions_keyboard(subscriptions: List[dict], lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    for i in range(0, len(subscriptions), 2):
        row = []
        for j in range(2):
            if i + j < len(subscriptions):
                sub = subscriptions[i + j]
                price_text = f"{sub['price']:.0f}₽"
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💎 " + t('buy_subscription_btn', lang, price=0), 
            callback_data=f"confirm_buy_{subscription_id}"
        )],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="buy_subscription")]
    ])
    return keyboard

def user_subscriptions_keyboard(user_subscriptions: List[dict], lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    for sub in user_subscriptions:
        buttons.append([InlineKeyboardButton(
            text=f"📱 {sub['name']}",
            callback_data=f"view_sub_{sub['id']}"
        )])
    
    if not user_subscriptions:
        buttons.append([InlineKeyboardButton(text="🛒 " + t('buy_subscription', lang), callback_data="buy_subscription")])
    
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def user_subscription_detail_keyboard(subscription_id: int, lang: str = 'ru', show_extend: bool = False, is_imported: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    
    if is_imported:
        buttons.append([InlineKeyboardButton(text="🔗 Получить ссылку подключения", callback_data=f"get_connection_{subscription_id}")])
        buttons.append([InlineKeyboardButton(text="🛒 Купить новую подписку", callback_data="buy_subscription")])
    else:
        if show_extend:
            buttons.append([InlineKeyboardButton(text="⏰ " + t('extend_subscription', lang), callback_data=f"extend_sub_{subscription_id}")])
        
        buttons.append([InlineKeyboardButton(text="🔗 Получить ссылку подключения", callback_data=f"get_connection_{subscription_id}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="my_subscriptions")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def extend_subscription_keyboard(subscription_id: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, продлить", callback_data=f"confirm_extend_{subscription_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_sub_{subscription_id}")
        ]
    ])
    return keyboard

def back_keyboard(callback_data: str, lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data=callback_data)]
    ])
    return keyboard

def cancel_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ " + t('cancel', lang), callback_data="main_menu")]
    ])
    return keyboard


def admin_menu_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 " + t('manage_subscriptions', lang), callback_data="admin_subscriptions"),
            InlineKeyboardButton(text="👥 " + t('manage_users', lang), callback_data="admin_users")
        ],
        [
            InlineKeyboardButton(text="💰 " + t('manage_balance', lang), callback_data="admin_balance"),
            InlineKeyboardButton(text="🎁 " + t('manage_promocodes', lang), callback_data="admin_promocodes")
        ],
        [
            InlineKeyboardButton(text="📨 " + t('send_message', lang), callback_data="admin_messages"),
            InlineKeyboardButton(text="👥 Рефералы", callback_data="admin_referrals")  # НОВАЯ КНОПКА
        ],
        [
            InlineKeyboardButton(text="🖥 Система RemnaWave", callback_data="admin_system")
        ],
        [
            InlineKeyboardButton(text="🔍 Мониторинг подписок", callback_data="admin_monitor"),
            InlineKeyboardButton(text="📊 " + t('statistics', lang), callback_data="admin_stats")
        ],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="main_menu")]
    ])
    return keyboard

def admin_subscriptions_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ " + t('create_subscription', lang), callback_data="create_subscription")],
        [InlineKeyboardButton(text="📋 Список подписок", callback_data="list_admin_subscriptions")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def admin_users_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="list_users")],
        [InlineKeyboardButton(text="🔍 Поиск пользователя", callback_data="search_user")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def admin_balance_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Пополнить баланс пользователю", callback_data="admin_add_balance")],
        [InlineKeyboardButton(text="📊 История платежей", callback_data="admin_payment_history")],
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="admin_stars_payments")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def admin_promocodes_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ " + t('create_promocode', lang), callback_data="create_promocode")],
        [InlineKeyboardButton(text="📋 Управление промокодами", callback_data="list_promocodes")],
        [InlineKeyboardButton(text="📊 Статистика промокодов", callback_data="promocodes_stats")],
        [InlineKeyboardButton(text="🧹 Очистить истекшие", callback_data="cleanup_expired_promos")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def promocodes_management_keyboard(promocodes: List, lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    for promo in promocodes[:15]:
        status_emoji = "🟢" if promo.is_active else "🔴"
        
        if promo.expires_at and promo.expires_at < datetime.utcnow():
            status_emoji = "⏰"
        
        usage_text = f"{promo.used_count}/{promo.usage_limit}"
        button_text = f"{status_emoji} {promo.code} ({promo.discount_amount}₽) [{usage_text}]"
        
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"promo_info_{promo.id}"
            )
        ])
        
        control_buttons = []
        
        if not promo.code.startswith('REF'):
            toggle_text = "🔴" if promo.is_active else "🟢"
            control_buttons.append(
                InlineKeyboardButton(text=toggle_text, callback_data=f"toggle_promo_{promo.id}")
            )
            
            control_buttons.append(
                InlineKeyboardButton(text="✏️", callback_data=f"edit_promo_{promo.id}")
            )
            
            control_buttons.append(
                InlineKeyboardButton(text="🗑", callback_data=f"delete_promo_{promo.id}")
            )
        else:
            control_buttons.append(
                InlineKeyboardButton(text="👥 Реферальный", callback_data="noop")
            )
        
        if control_buttons:
            buttons.append(control_buttons)
    
    buttons.append([
        InlineKeyboardButton(text="➕ Создать промокод", callback_data="create_promocode")
    ])
    
    buttons.append([
        InlineKeyboardButton(text="🧹 Очистить истекшие", callback_data="cleanup_expired_promos"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="promocodes_stats")
    ])
    
    buttons.append([
        InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_promocodes")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def promocode_edit_keyboard(promo_id: int, language: str = 'ru') -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="💰 Изменить скидку", callback_data=f"edit_promo_field_{promo_id}_discount")],
        [InlineKeyboardButton(text="📊 Изменить лимит", callback_data=f"edit_promo_field_{promo_id}_limit")],
        [InlineKeyboardButton(text="⏰ Изменить срок", callback_data=f"edit_promo_field_{promo_id}_expiry")],
        [InlineKeyboardButton(text="🗑 Удалить промокод", callback_data=f"delete_promo_{promo_id}")],
        [InlineKeyboardButton(text=t('back', language), callback_data="list_promocodes")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def promocode_info_keyboard(promo_id: int, is_referral: bool = False, lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    if not is_referral:
        buttons.extend([
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_promo_{promo_id}")],
            [
                InlineKeyboardButton(text="🟢/🔴 Переключить", callback_data=f"toggle_promo_{promo_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_promo_{promo_id}")
            ]
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="👥 Реферальный код", callback_data="noop")
        ])
    
    buttons.append([
        InlineKeyboardButton(text="🔙 К списку", callback_data="list_promocodes")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def bulk_promocodes_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🧹 Удалить все истекшие", callback_data="confirm_cleanup_expired")],
        [InlineKeyboardButton(text="🔴 Деактивировать все", callback_data="confirm_deactivate_all")],
        [InlineKeyboardButton(text="📊 Экспорт статистики", callback_data="export_promo_stats")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="list_promocodes")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def confirmation_keyboard(confirm_callback: str, cancel_callback: str, lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data=confirm_callback),
            InlineKeyboardButton(text="❌ Нет", callback_data=cancel_callback)
        ]
    ])
    return keyboard

def pagination_keyboard(page: int, total_pages: int, prefix: str, lang: str = 'ru') -> InlineKeyboardMarkup:
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
    buttons = []
    for sub in subs:
        status_emoji = "🟢" if sub.is_active else "🔴"
        price = f"{sub.price:.0f}₽"
        
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_emoji} {sub.name} — {price}",
                callback_data=f"list_sub_{sub.id}"
            )
        ])
        
        control_buttons = [
            InlineKeyboardButton(text="✏️", callback_data=f"edit_sub_{sub.id}"),
            InlineKeyboardButton(
                text="🟢" if not sub.is_active else "🔴",
                callback_data=f"toggle_sub_{sub.id}"
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"delete_sub_{sub.id}")
        ]
        
        buttons.append(control_buttons)
    
    buttons.append([InlineKeyboardButton(text="➕ Создать подписку", callback_data="create_subscription")])
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_subscriptions")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_payment_keyboard(payment_id: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить платеж", callback_data=f"approve_payment_{payment_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_payment_{payment_id}")
        ]
    ])
    return keyboard

def admin_messages_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 " + t('send_to_user', lang), callback_data="admin_send_to_user")],
        [InlineKeyboardButton(text="📢 " + t('send_to_all', lang), callback_data="admin_send_to_all")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def quick_topup_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключиться", url=subscription_url)],
        [InlineKeyboardButton(text="📱 Инструкция", callback_data="connection_guide")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="my_subscriptions")]
    ])
    return keyboard

def trial_subscription_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✅ Получить тестовую подписку", callback_data="confirm_trial")],
        [InlineKeyboardButton(text=t('back', lang), callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_monitor_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статус сервиса", callback_data="monitor_status")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def admin_system_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="refresh_system_stats")],
        [InlineKeyboardButton(text="🖥 Ноды", callback_data="nodes_management")],
        [InlineKeyboardButton(text="👥 Системные пользователи", callback_data="system_users")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_system")]
    ])
    return keyboard

def nodes_management_keyboard(nodes: List[Dict], lang: str = 'ru', timestamp: int = None) -> InlineKeyboardMarkup:
    buttons = []
    
    if nodes:
        online_count = len([n for n in nodes if n.get('status') == 'online'])
        total_count = len(nodes)
        
        buttons.append([
            InlineKeyboardButton(
                text=f"📊 Ноды: {online_count}/{total_count} онлайн",
                callback_data="noop"
            )
        ])
        
        for i, node in enumerate(nodes[:5]):
            status = node.get('status', 'unknown')
            
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
            
            if len(node_name) > 20:
                display_name = node_name[:17] + "..."
            else:
                display_name = node_name
            
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
    
    buttons.append([
        InlineKeyboardButton(text="🔄 Перезагрузить все", callback_data="restart_all_nodes"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="nodes_statistics")
    ])
    
    refresh_callback = f"refresh_nodes_stats_{timestamp}" if timestamp else "refresh_nodes_stats"
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=refresh_callback)
    ])
    
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_system")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def system_users_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика пользователей", callback_data="users_statistics")],
        [InlineKeyboardButton(text="👥 Список всех пользователей", callback_data="list_all_system_users")],
        [InlineKeyboardButton(text="🔍 Поиск пользователя", callback_data="search_user_uuid")],
        [InlineKeyboardButton(text="🔍 Отладка API пользователей", callback_data="debug_users_api")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_system")]
    ])
    return keyboard

def bulk_operations_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data="bulk_reset_traffic")],
        [InlineKeyboardButton(text="❌ Отключить пользователей", callback_data="bulk_disable_users")],
        [InlineKeyboardButton(text="✅ Включить пользователей", callback_data="bulk_enable_users")],
        [InlineKeyboardButton(text="🗑 Удалить пользователей", callback_data="bulk_delete_users")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="system_users")]
    ])
    return keyboard

def confirm_restart_keyboard(node_id: str = None, lang: str = 'ru') -> InlineKeyboardMarkup:
    action = f"confirm_restart_node_{node_id}" if node_id else "confirm_restart_all_nodes"
    back_action = f"node_details_{node_id}" if node_id else "nodes_management"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, перезагрузить", callback_data=action),
            InlineKeyboardButton(text="❌ Отмена", callback_data=back_action)
        ]
    ])
    return keyboard

def admin_referrals_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика рефералов", callback_data="referral_statistics")],
        [InlineKeyboardButton(text="👥 Список рефереров", callback_data="list_referrers")],
        [InlineKeyboardButton(text="💰 История выплат", callback_data="referral_payments")],
        [InlineKeyboardButton(text="⚙️ Настройки программы", callback_data="referral_settings")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def lucky_game_keyboard(can_play: bool, time_left_str: str = "", lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    if can_play:
        buttons.append([InlineKeyboardButton(text="🎲 Играть!", callback_data="start_lucky_game")])
    else:
        buttons.append([InlineKeyboardButton(text=f"⏰ Приходи через {time_left_str}", callback_data="noop")])
    
    buttons.extend([
        [InlineKeyboardButton(text="📈 История игр", callback_data="lucky_game_history")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="main_menu")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def lucky_numbers_keyboard(numbers_count: int) -> InlineKeyboardMarkup:
    buttons = []
    
    for i in range(0, numbers_count, 5):
        row = []
        for j in range(5):
            if i + j + 1 <= numbers_count:
                number = i + j + 1
                row.append(InlineKeyboardButton(
                    text=str(number),
                    callback_data=f"choose_number_{number}"
                ))
        buttons.append(row)
    
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="lucky_game")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def lucky_game_result_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 История игр", callback_data="lucky_game_history")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

def topup_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="topup_stars")],
        [InlineKeyboardButton(text="👨‍💼 " + t('topup_support', lang), callback_data="topup_support")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="balance")]
    ])
    return keyboard

def stars_topup_keyboard(stars_rates: Dict[int, float], lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура с вариантами пополнения через звезды"""
    buttons = []
    
    # Сортируем по количеству звезд
    sorted_rates = sorted(stars_rates.items())
    
    # Создаем кнопки по 2 в ряд
    for i in range(0, len(sorted_rates), 2):
        row = []
        for j in range(2):
            if i + j < len(sorted_rates):
                stars, rubles = sorted_rates[i + j]
                # Определяем выгодность предложения
                if stars >= 500:
                    emoji = "🔥"  # Выгодное предложение
                elif stars >= 250:
                    emoji = "💎"  # Хорошее предложение
                else:
                    emoji = "⭐"  # Базовое предложение
                
                button_text = f"{emoji} {stars} ⭐ → {rubles:.0f}₽"
                row.append(InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"buy_stars_{stars}"
                ))
        buttons.append(row)
    
    # Добавляем кнопку назад
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="topup_balance")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def stars_payment_keyboard(stars_amount: int, rub_amount: float, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура подтверждения платежа через звезды (не используется в send_invoice)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="topup_stars")]
    ])
    return keyboard
