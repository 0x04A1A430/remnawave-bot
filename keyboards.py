from database import Subscription
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Optional, Dict
from translations import t
from datetime import datetime

def language_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")
        ]
    ])
    return keyboard

def main_menu_keyboard(lang: str = 'ru', is_admin: bool = False, show_trial: bool = False, show_lucky_game: bool = False) -> InlineKeyboardMarkup:
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
        [InlineKeyboardButton(text="🌐 " + t('change_language', lang), callback_data="change_language")],
        [InlineKeyboardButton(text="📜 Правила сервиса", callback_data="service_rules")]
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

def topup_keyboard(lang: str, tribute_enabled: bool = False, stars_enabled: bool = False) -> InlineKeyboardMarkup:
    keyboard = []
    
    if tribute_enabled:
        keyboard.append([
            InlineKeyboardButton(
                text="💳 Tribute (Карта)" if lang == 'ru' else "💳 Tribute (Card/SBP)",
                callback_data="topup_tribute"
            )
        ])
    
    if stars_enabled:
        keyboard.append([
            InlineKeyboardButton(
                text="⭐ Telegram Stars" if lang == 'ru' else "⭐ Telegram Stars",
                callback_data="topup_stars"
            )
        ])
    
    keyboard.extend([
        [InlineKeyboardButton(
            text="💬 Связаться с поддержкой" if lang == 'ru' else "💬 Contact Support",
            callback_data="topup_support"
        )],
        [InlineKeyboardButton(
            text="🔙 Назад" if lang == 'ru' else "🔙 Back",
            callback_data="balance"
        )]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


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

def user_subscription_detail_keyboard(subscription_id: int, lang: str = 'ru', 
                                   show_extend: bool = False, is_imported: bool = False, 
                                   is_trial: bool = False, autopay_enabled: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    
    if is_imported:
        buttons.append([InlineKeyboardButton(text="🔗 Получить ссылку подключения", callback_data=f"get_connection_{subscription_id}")])
        buttons.append([InlineKeyboardButton(text="🛒 Купить новую подписку", callback_data="buy_subscription")])
    elif is_trial:
        buttons.append([InlineKeyboardButton(text="🔗 Получить ссылку подключения", callback_data=f"get_connection_{subscription_id}")])
        buttons.append([InlineKeyboardButton(text="🛒 Купить полную подписку", callback_data="buy_subscription")])
    else:
        if show_extend:
            buttons.append([InlineKeyboardButton(text="⏰ " + t('extend_subscription', lang), callback_data=f"extend_sub_{subscription_id}")])
        
        buttons.append([InlineKeyboardButton(text="🔗 Получить ссылку подключения", callback_data=f"get_connection_{subscription_id}")])
        
        if autopay_enabled:
            autopay_text = "🔄✅ Настроить автоплатеж"
        else:
            autopay_text = "🔄❌ Настроить автоплатеж"
        
        buttons.append([InlineKeyboardButton(text=autopay_text, callback_data=f"autopay_settings_{subscription_id}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="my_subscriptions")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def autopay_confirmation_keyboard(subscription_id: int, action: str, lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_autopay_{action}_{subscription_id}"),
            InlineKeyboardButton(text="❌ Нет", callback_data=f"autopay_settings_{subscription_id}")
        ]
    ])
    return keyboard

def autopay_help_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура помощи по автоплатежам"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="topup_balance")],
        [InlineKeyboardButton(text="📋 Мои подписки", callback_data="my_subscriptions")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])
    return keyboard

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
            InlineKeyboardButton(text="👥 Рефералы", callback_data="admin_referrals")
        ],
        [
            InlineKeyboardButton(text="📜 Правила сервиса", callback_data="admin_rules"),
            InlineKeyboardButton(text="🔄 Автоплатежи", callback_data="admin_autopay")
        ],
        [
            InlineKeyboardButton(text="🖥 Система RemnaWave", callback_data="admin_system"),
            InlineKeyboardButton(text="🔍 Мониторинг подписок", callback_data="admin_monitor")
        ],
        [
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
        [
            InlineKeyboardButton(text="👥 Список пользователей", callback_data="list_users"),
            InlineKeyboardButton(text="🔍 Поиск пользователя", callback_data="search_user")
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="users_stats"),
            InlineKeyboardButton(text="📋 Все подписки", callback_data="admin_user_subscriptions_all")
        ],
        [
            InlineKeyboardButton(text="💰 Управление балансом", callback_data="admin_balance"),
            InlineKeyboardButton(text="📨 Массовая рассылка", callback_data="admin_send_to_all")
        ],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def users_list_keyboard(page: int, total_pages: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура для списка пользователей с улучшенной навигацией"""
    buttons = []
    
    # Навигация по страницам
    if total_pages > 1:
        nav_row = []
        
        # Кнопка "В начало" если не на первой странице
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="⏪", callback_data="users_page_0"))
        
        # Кнопка "Назад"
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"users_page_{page - 1}"))
        
        # Индикатор страницы
        nav_row.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop"))
        
        # Кнопка "Вперед"
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"users_page_{page + 1}"))
        
        # Кнопка "В конец" если не на последней странице  
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="⏩", callback_data=f"users_page_{total_pages - 1}"))
        
        buttons.append(nav_row)
    
    # Управляющие кнопки
    buttons.extend([
        [
            InlineKeyboardButton(text="🔍 Поиск", callback_data="search_user"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="users_stats"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"users_page_{page}")
        ],
        [
            InlineKeyboardButton(text="📋 Все подписки", callback_data="admin_user_subscriptions_all"),
            InlineKeyboardButton(text="💰 Баланс", callback_data="admin_balance")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def user_quick_actions_keyboard(user_id: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура быстрых действий для пользователя"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Баланс", callback_data=f"user_balance_{user_id}"),
            InlineKeyboardButton(text="📋 Подписки", callback_data=f"user_subs_{user_id}")
        ],
        [
            InlineKeyboardButton(text="✉️ Сообщение", callback_data=f"user_message_{user_id}"),
            InlineKeyboardButton(text="🔧 Управление", callback_data=f"user_manage_{user_id}")
        ]
    ])
    return keyboard

def user_detail_actions_keyboard(user_id: int, is_admin: bool = False, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Расширенная клавиатура действий для детального просмотра пользователя"""
    buttons = [
        [
            InlineKeyboardButton(text="💰 Управление балансом", callback_data=f"user_balance_{user_id}"),
            InlineKeyboardButton(text="📋 Подписки", callback_data=f"user_subs_{user_id}")
        ],
        [
            InlineKeyboardButton(text="💳 История платежей", callback_data=f"user_payments_{user_id}"),
            InlineKeyboardButton(text="✉️ Отправить сообщение", callback_data=f"user_message_{user_id}")
        ]
    ]
    
    if not is_admin:
        buttons.append([
            InlineKeyboardButton(text="🔧 Управление аккаунтом", callback_data=f"user_manage_{user_id}"),
            InlineKeyboardButton(text="🎯 Реферальная система", callback_data=f"user_referrals_{user_id}")
        ])
    
    buttons.extend([
        [
            InlineKeyboardButton(text="🔄 Обновить данные", callback_data=f"user_detail_{user_id}"),
            InlineKeyboardButton(text="📊 Статистика активности", callback_data=f"user_activity_{user_id}")
        ],
        [InlineKeyboardButton(text="🔙 К списку пользователей", callback_data="list_users")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def search_results_keyboard(users: List, show_pagination: bool = False, page: int = 0, total_pages: int = 1, search_query: str = "", lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура для результатов поиска пользователей"""
    buttons = []
    
    # Добавляем кнопки для найденных пользователей (компактно, по 2 в ряд)
    for i in range(0, len(users), 2):
        row = []
        for j in range(2):
            if i + j < len(users):
                u = users[i + j]
                display_name = (u.first_name or "Без имени")[:10]
                status_emoji = "👑" if u.is_admin else "💰" if u.balance > 100 else "👤"
                
                row.append(InlineKeyboardButton(
                    text=f"{status_emoji} {display_name}",
                    callback_data=f"user_detail_{u.telegram_id}"
                ))
        buttons.append(row)
    
    # Навигация для поиска (если нужна)
    if show_pagination and total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"search_page_{page-1}_{search_query}"))
        
        nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"search_page_{page+1}_{search_query}"))
        
        buttons.append(nav_row)
    
    # Управляющие кнопки
    buttons.extend([
        [
            InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_user"),
            InlineKeyboardButton(text="📋 Все пользователи", callback_data="list_users")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def balance_management_keyboard(user_id: int, current_balance: float, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура для управления балансом пользователя"""
    buttons = [
        [
            InlineKeyboardButton(text="💸 Добавить баланс", callback_data=f"admin_add_balance_to_{user_id}"),
            InlineKeyboardButton(text="💳 Списать баланс", callback_data=f"admin_subtract_balance_{user_id}")
        ]
    ]
    
    # Быстрые кнопки добавления баланса
    if current_balance < 1000:
        quick_amounts = [100, 300, 500, 1000]
        quick_row = []
        for amount in quick_amounts:
            quick_row.append(InlineKeyboardButton(
                text=f"+{amount}₽",
                callback_data=f"quick_add_balance_{user_id}_{amount}"
            ))
            if len(quick_row) == 2:
                buttons.append(quick_row)
                quick_row = []
        if quick_row:
            buttons.append(quick_row)
    
    buttons.extend([
        [
            InlineKeyboardButton(text="📊 История операций", callback_data=f"user_payments_{user_id}"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"user_balance_{user_id}")
        ],
        [InlineKeyboardButton(text="🔙 К пользователю", callback_data=f"user_detail_{user_id}")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def user_subscriptions_management_keyboard(user_id: int, has_subscriptions: bool = True, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура управления подписками пользователя"""
    buttons = []
    
    if has_subscriptions:
        buttons.extend([
            [
                InlineKeyboardButton(text="📋 Все подписки пользователя", callback_data=f"admin_user_subs_detailed_{user_id}"),
                InlineKeyboardButton(text="🛒 Создать новую", callback_data=f"admin_create_user_sub_{user_id}")
            ],
            [
                InlineKeyboardButton(text="⏰ Продлить подписку", callback_data=f"admin_extend_user_sub_{user_id}"),
                InlineKeyboardButton(text="🔄 Автопродление", callback_data=f"admin_user_autopay_{user_id}")
            ]
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="🛒 Создать первую подписку", callback_data=f"admin_create_user_sub_{user_id}")
        ])
    
    buttons.extend([
        [
            InlineKeyboardButton(text="📊 Статистика использования", callback_data=f"user_sub_stats_{user_id}"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"user_subs_{user_id}")
        ],
        [InlineKeyboardButton(text="🔙 К пользователю", callback_data=f"user_detail_{user_id}")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_statistics_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Расширенная клавиатура статистики"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Пользователи", callback_data="users_stats"),
            InlineKeyboardButton(text="📋 Подписки", callback_data="subscriptions_stats")
        ],
        [
            InlineKeyboardButton(text="💰 Финансы", callback_data="financial_stats"),
            InlineKeyboardButton(text="🎯 Реферальная система", callback_data="referral_stats")
        ],
        [
            InlineKeyboardButton(text="📊 Общая аналитика", callback_data="general_analytics"),
            InlineKeyboardButton(text="🔄 Автопродления", callback_data="autopay_stats")
        ],
        [
            InlineKeyboardButton(text="📈 Графики активности", callback_data="activity_charts"),
            InlineKeyboardButton(text="📉 Тренды", callback_data="trends_analysis")
        ],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def confirmation_keyboard_enhanced(confirm_text: str, cancel_text: str, confirm_callback: str, cancel_callback: str, 
                                 warning: bool = False, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Улучшенная клавиатура подтверждения с предупреждениями"""
    confirm_emoji = "⚠️" if warning else "✅"
    cancel_emoji = "❌"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{confirm_emoji} {confirm_text}", callback_data=confirm_callback),
            InlineKeyboardButton(text=f"{cancel_emoji} {cancel_text}", callback_data=cancel_callback)
        ]
    ])
    return keyboard

# Специальные клавиатуры для массовых операций
def bulk_user_operations_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура массовых операций с пользователями"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📨 Массовая рассылка", callback_data="admin_bulk_message"),
            InlineKeyboardButton(text="💰 Массовое начисление", callback_data="admin_bulk_balance")
        ],
        [
            InlineKeyboardButton(text="📋 Экспорт пользователей", callback_data="export_users"),
            InlineKeyboardButton(text="📊 Детальная аналитика", callback_data="detailed_user_analytics")
        ],
        [
            InlineKeyboardButton(text="🧹 Очистка неактивных", callback_data="cleanup_inactive_users"),
            InlineKeyboardButton(text="🔄 Синхронизация", callback_data="sync_user_data")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
    ])
    return keyboard

def noop_keyboard(text: str = "Обновлено", lang: str = 'ru') -> InlineKeyboardMarkup:
    """Простая клавиатура-заглушка"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ {text}", callback_data="noop")]
    ])
    return keyboard

def admin_user_subscriptions_filters_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура фильтров для подписок пользователей"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 Активные", callback_data="filter_subs_active"),
            InlineKeyboardButton(text="🔴 Истекшие", callback_data="filter_subs_expired")
        ],
        [
            InlineKeyboardButton(text="🔄✅ С автоплатежом", callback_data="filter_subs_autopay"),
            InlineKeyboardButton(text="⏰ Истекают скоро", callback_data="filter_subs_expiring")
        ],
        [
            InlineKeyboardButton(text="🆓 Триальные", callback_data="filter_subs_trial"),
            InlineKeyboardButton(text="📦 Импортированные", callback_data="filter_subs_imported")
        ],
        [
            InlineKeyboardButton(text="📋 Все подписки", callback_data="admin_user_subscriptions_all"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")
        ]
    ])
    return keyboard

def admin_user_subscription_detail_keyboard(subscription_id: int, user_id: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    """Клавиатура для детального просмотра подписки пользователя"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_user_sub_{subscription_id}"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_user_sub_{subscription_id}")
        ],
        [
            InlineKeyboardButton(text="👤 К пользователю", callback_data=f"admin_user_detail_{user_id}"),
            InlineKeyboardButton(text="📋 К списку подписок", callback_data="admin_user_subscriptions_all")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
    ])
    return keyboard

def user_subscriptions_pagination_keyboard(current_page: int, total_pages: int, 
                                         filter_type: str = "all", lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    if total_pages > 1:
        nav_row = []
        
        if current_page > 0:
            nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"user_subs_page_{current_page - 1}_{filter_type}"))
        
        nav_row.append(InlineKeyboardButton(text=f"{current_page + 1}/{total_pages}", callback_data="noop"))
        
        if current_page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"user_subs_page_{current_page + 1}_{filter_type}"))
        
        buttons.append(nav_row)
    
    buttons.append([
        InlineKeyboardButton(text="🔍 Фильтры", callback_data="admin_user_subscriptions_filters"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_user_subs_{filter_type}")
    ])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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

def stars_topup_keyboard(stars_rates: Dict[int, float], lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    sorted_rates = sorted(stars_rates.items())
    
    for i in range(0, len(sorted_rates), 2):
        row = []
        for j in range(2):
            if i + j < len(sorted_rates):
                stars, rubles = sorted_rates[i + j]
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
    
    buttons.append([InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="topup_balance")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def stars_payment_keyboard(stars_amount: int, rub_amount: float, lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="topup_stars")]
    ])
    return keyboard

def service_rules_keyboard(current_page: int, total_pages: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    if total_pages > 1:
        nav_row = []
        
        if current_page > 0:
            nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"rules_page_{current_page - 1}"))
        
        nav_row.append(InlineKeyboardButton(text=f"{current_page + 1}/{total_pages}", callback_data="noop"))
        
        if current_page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"rules_page_{current_page + 1}"))
        
        buttons.append(nav_row)
    
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_rules_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список правил", callback_data="admin_rules_list")],
        [InlineKeyboardButton(text="➕ Добавить страницу", callback_data="admin_rules_create")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_panel")]
    ])
    return keyboard

def admin_rules_list_keyboard(rules, lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    for rule in rules:
        status_emoji = "🟢" if rule.is_active else "🔴"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_emoji} {rule.page_order}. {rule.title}",
                callback_data=f"admin_rule_view_{rule.id}"
            )
        ])
    
    buttons.extend([
        [InlineKeyboardButton(text="➕ Добавить страницу", callback_data="admin_rules_create")],
        [InlineKeyboardButton(text="🔙 " + t('back', lang), callback_data="admin_rules")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_rule_edit_keyboard(rule_id: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Редактировать заголовок", callback_data=f"admin_rule_edit_title_{rule_id}"),
            InlineKeyboardButton(text="📝 Редактировать содержимое", callback_data=f"admin_rule_edit_content_{rule_id}")
        ],
        [
            InlineKeyboardButton(text="🔄 Изменить порядок", callback_data=f"admin_rule_edit_order_{rule_id}"),
            InlineKeyboardButton(text="🟢/🔴 Вкл/Выкл", callback_data=f"admin_rule_toggle_{rule_id}")
        ],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_rule_delete_{rule_id}")],
        [InlineKeyboardButton(text="🔙 К списку", callback_data="admin_rules_list")]
    ])
    return keyboard

def admin_rule_delete_confirm_keyboard(rule_id: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"admin_rule_confirm_delete_{rule_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_rule_view_{rule_id}")
        ]
    ])
    return keyboard

def autopay_settings_keyboard(user_sub_id: int, user_sub, lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    if user_sub.auto_pay_enabled:
        toggle_text = "❌ Отключить автоплатеж"
        toggle_callback = f"toggle_autopay_{user_sub_id}"
    else:
        toggle_text = "✅ Включить автоплатеж"
        toggle_callback = f"toggle_autopay_{user_sub_id}"
    
    buttons.append([InlineKeyboardButton(text=toggle_text, callback_data=toggle_callback)])
    
    if user_sub.auto_pay_enabled:
        buttons.append([InlineKeyboardButton(text="📅 Настроить дни до продления", callback_data="noop")])
        
        days_row = []
        for days in [1, 3, 5, 7]:
            emoji = "🔹" if user_sub.auto_pay_days_before == days else "⚪"
            days_row.append(InlineKeyboardButton(
                text=f"{emoji} {days}д",
                callback_data=f"autopay_days_{user_sub_id}_{days}"
            ))
        buttons.append(days_row)
    
    buttons.append([InlineKeyboardButton(text="🔙 К подписке", callback_data=f"view_sub_{user_sub_id}")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def autopay_status_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="autopay_status")],
        [InlineKeyboardButton(text="🚀 Принудительная проверка", callback_data="autopay_force_check")],
        [InlineKeyboardButton(text="📋 Список подписок", callback_data="autopay_subscriptions_list")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_autopay")]
    ])
    return keyboard

def autopay_subscriptions_keyboard(subscriptions_data: List[Dict], lang: str = 'ru') -> InlineKeyboardMarkup:
    buttons = []
    
    expired = [s for s in subscriptions_data if s['expires_in_days'] <= 0]
    due_soon = [s for s in subscriptions_data if 0 < s['expires_in_days'] <= s['auto_pay_days_before']]
    
    critical_subs = expired + due_soon
    
    for sub_data in critical_subs[:8]: 
        username = sub_data['username'] if sub_data['username'] != 'N/A' else f"ID:{sub_data['user_id']}"
        days = sub_data['expires_in_days']
        
        if days <= 0:
            status_emoji = "❌"
            status_text = f"Истекла"
        elif days <= sub_data['auto_pay_days_before']:
            status_emoji = "⚠️"
            status_text = f"Через {days}д"
        else:
            status_emoji = "✅"
            status_text = f"Через {days}д"
        
        button_text = f"{status_emoji} @{username} ({status_text})"
        
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"autopay_user_detail_{sub_data['user_id']}"
            )
        ])
    
    if len(subscriptions_data) > 8:
        buttons.append([
            InlineKeyboardButton(
                text=f"... и еще {len(subscriptions_data) - 8}",
                callback_data="noop"
            )
        ])
    
    buttons.extend([
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="autopay_subscriptions_list")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="autopay_statistics")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_autopay")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def autopay_user_detail_keyboard(user_id: int, lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Добавить баланс", callback_data=f"admin_add_balance_to_{user_id}")],
        [InlineKeyboardButton(text="📋 Управление подписками", callback_data=f"admin_user_subscriptions_{user_id}")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"autopay_user_detail_{user_id}")],
        [InlineKeyboardButton(text="🔙 К списку", callback_data="autopay_subscriptions_list")]
    ])
    return keyboard


def autopay_statistics_keyboard(lang: str = 'ru') -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Недостаточно средств", callback_data="autopay_insufficient_balance_users")],
        [InlineKeyboardButton(text="📋 Список подписок", callback_data="autopay_subscriptions_list")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="autopay_statistics")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_autopay")]
    ])
    return keyboard
