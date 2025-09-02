from typing import List, Optional
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.config import settings, PERIOD_PRICES, TRAFFIC_PRICES
from app.localization.texts import get_texts


def get_rules_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.RULES_ACCEPT, callback_data="rules_accept"),
            InlineKeyboardButton(text=texts.RULES_DECLINE, callback_data="rules_decline")
        ]
    ])


def get_main_menu_keyboard(
    language: str = "ru", 
    is_admin: bool = False,
    has_had_paid_subscription: bool = False,
    has_active_subscription: bool = False,
    subscription_is_active: bool = False 
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    if settings.DEBUG:
        print(f"DEBUG KEYBOARD: language={language}, is_admin={is_admin}, has_had_paid={has_had_paid_subscription}, has_active={has_active_subscription}, sub_active={subscription_is_active}")
    
    keyboard = [
        [
            InlineKeyboardButton(text=texts.MENU_BALANCE, callback_data="menu_balance"),
            InlineKeyboardButton(text=texts.MENU_SUBSCRIPTION, callback_data="menu_subscription")
        ]
    ]
    
    
    show_trial = not has_had_paid_subscription and not has_active_subscription
    
    show_buy = not has_active_subscription or not subscription_is_active
    
    subscription_buttons = []
    
    if show_trial:
        subscription_buttons.append(
            InlineKeyboardButton(text=texts.MENU_TRIAL, callback_data="menu_trial")
        )
    
    if show_buy:
        subscription_buttons.append(
            InlineKeyboardButton(text=texts.MENU_BUY_SUBSCRIPTION, callback_data="menu_buy")
        )
    
    if subscription_buttons:
        if len(subscription_buttons) == 2:
            keyboard.append(subscription_buttons)
        else:
            keyboard.append([subscription_buttons[0]])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text=texts.MENU_PROMOCODE, callback_data="menu_promocode"),
            InlineKeyboardButton(text=texts.MENU_REFERRALS, callback_data="menu_referrals")
        ],
        [
            InlineKeyboardButton(text=texts.MENU_SUPPORT, callback_data="menu_support"),
            InlineKeyboardButton(text=texts.MENU_RULES, callback_data="menu_rules")
        ]
    ])
    
    if settings.DEBUG:
        print(f"DEBUG KEYBOARD: is_admin={is_admin}, добавляем админ кнопку: {is_admin}")
    
    if is_admin:
        if settings.DEBUG:
            print("DEBUG KEYBOARD: Админ кнопка ДОБАВЛЕНА!")
        keyboard.append([
            InlineKeyboardButton(text=texts.MENU_ADMIN, callback_data="admin_panel")
        ])
    else:
        if settings.DEBUG:
            print("DEBUG KEYBOARD: Админ кнопка НЕ добавлена")
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_back_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
    ])


def get_subscription_keyboard(
    language: str = "ru", 
    has_subscription: bool = False, 
    is_trial: bool = False,
    subscription=None
) -> InlineKeyboardMarkup:
    from app.config import settings 
    
    texts = get_texts(language)
    keyboard = []
    
    if has_subscription:
        if subscription and subscription.subscription_url:
            connect_mode = settings.CONNECT_BUTTON_MODE
            
            if connect_mode == "miniapp_subscription":
                keyboard.append([
                    InlineKeyboardButton(
                        text="🚀 Открыть в мини-приложении",
                        web_app=types.WebAppInfo(url=subscription.subscription_url)
                    )
                ])
                keyboard.append([
                    InlineKeyboardButton(text="📋 Показать ссылку", callback_data="open_subscription_link")
                ])
            elif connect_mode == "miniapp_custom":
                if settings.MINIAPP_CUSTOM_URL:
                    keyboard.append([
                        InlineKeyboardButton(
                            text="🚀 Открыть приложение",
                            web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL)
                        )
                    ])
                    keyboard.append([
                        InlineKeyboardButton(text="📋 Показать ссылку подписки", callback_data="open_subscription_link")
                    ])
                else:
                    keyboard.append([
                        InlineKeyboardButton(text="🔗 Подключиться", callback_data="subscription_connect")
                    ])
            else:
                keyboard.append([
                    InlineKeyboardButton(text="🔗 Подключиться", callback_data="subscription_connect")
                ])
        
        if not is_trial and subscription and subscription.days_left <= 3:
            keyboard.append([
                InlineKeyboardButton(text="⏰ Продлить", callback_data="subscription_extend")
            ])
        
        if not is_trial:
            keyboard.append([
                InlineKeyboardButton(text="💳 Автоплатеж", callback_data="subscription_autopay")
            ])
        
        if is_trial:
            keyboard.append([
                InlineKeyboardButton(text=texts.MENU_BUY_SUBSCRIPTION, callback_data="subscription_upgrade")
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(text="⚙️ Настройки подписки", callback_data="subscription_settings")
            ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_subscription_settings_keyboard(language: str = "ru", show_countries_management: bool = True) -> InlineKeyboardMarkup:
    from app.config import settings
    
    texts = get_texts(language)
    keyboard = []
    
    if show_countries_management:
        keyboard.append([
            InlineKeyboardButton(text="🌍 Добавить страны", callback_data="subscription_add_countries")
        ])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text="📱 Добавить устройства", callback_data="subscription_add_devices")
        ],
        [
            InlineKeyboardButton(text="🔄 Сбросить устройства", callback_data="subscription_reset_devices")
        ]
    ])
    
    if settings.is_traffic_selectable():
        keyboard.insert(-2, [
            InlineKeyboardButton(text="📈 Добавить трафик", callback_data="subscription_add_traffic")
        ])
        keyboard.insert(-2, [
            InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data="subscription_reset_traffic")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_trial_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎁 Активировать", callback_data="trial_activate"),
            InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
        ]
    ])


def get_subscription_period_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    available_periods = settings.get_available_subscription_periods()
    
    period_texts = {
        14: texts.PERIOD_14_DAYS,
        30: texts.PERIOD_30_DAYS,
        60: texts.PERIOD_60_DAYS,
        90: texts.PERIOD_90_DAYS,
        180: texts.PERIOD_180_DAYS,
        360: texts.PERIOD_360_DAYS
    }
    
    for days in available_periods:
        if days in period_texts:
            keyboard.append([
                InlineKeyboardButton(
                    text=period_texts[days], 
                    callback_data=f"period_{days}"
                )
            ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_traffic_packages_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    from app.config import settings
    
    if settings.is_traffic_fixed():
        return get_back_keyboard(language)
    
    texts = get_texts(language)
    keyboard = []
    
    packages = [
        (5, texts.TRAFFIC_5GB),
        (10, texts.TRAFFIC_10GB),
        (25, texts.TRAFFIC_25GB),
        (50, texts.TRAFFIC_50GB),
        (100, texts.TRAFFIC_100GB),
        (250, texts.TRAFFIC_250GB),
        (0, texts.TRAFFIC_UNLIMITED) 
    ]
    
    for gb, text in packages:
        keyboard.append([
            InlineKeyboardButton(text=text, callback_data=f"traffic_{gb}")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="subscription_config_back")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_countries_keyboard(countries: List[dict], selected: List[str], language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    for country in countries:
        if not country.get('is_available', True):
            continue 
            
        emoji = "✅" if country['uuid'] in selected else "⚪"
        
        if country['price_kopeks'] > 0:
            price_text = f" (+{texts.format_price(country['price_kopeks'])})"
        else:
            price_text = " (Бесплатно)"
        
        keyboard.append([
            InlineKeyboardButton(
                text=f"{emoji} {country['name']}{price_text}",
                callback_data=f"country_{country['uuid']}"
            )
        ])
    
    if not keyboard:
        keyboard.append([
            InlineKeyboardButton(
                text="❌ Нет доступных серверов",
                callback_data="no_servers"
            )
        ])
    
    keyboard.extend([
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="countries_continue")],
        [InlineKeyboardButton(text=texts.BACK, callback_data="subscription_config_back")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_devices_keyboard(current: int, language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    for devices in range(1, 6): 
        price = (devices - 1) * settings.PRICE_PER_DEVICE
        price_text = f" (+{texts.format_price(price)})" if price > 0 else ""
        emoji = "✅" if devices == current else "⚪"
        
        device_word = _get_device_declension(devices)
        
        keyboard.append([
            InlineKeyboardButton(
                text=f"{emoji} {devices} {device_word}{price_text}",
                callback_data=f"devices_{devices}"
            )
        ])
    
    keyboard.extend([
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="devices_continue")],
        [InlineKeyboardButton(text=texts.BACK, callback_data="subscription_config_back")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def _get_device_declension(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "устройство"
    elif count % 10 in [2, 3, 4] and count % 100 not in [12, 13, 14]:
        return "устройства"
    else:
        return "устройств"

def get_subscription_confirm_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.CONFIRM, callback_data="subscription_confirm"),
            InlineKeyboardButton(text=texts.CANCEL, callback_data="subscription_cancel")
        ]
    ])


def get_balance_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    keyboard = [
        [
            InlineKeyboardButton(text=texts.BALANCE_HISTORY, callback_data="balance_history"),
            InlineKeyboardButton(text=texts.BALANCE_TOP_UP, callback_data="balance_topup")
        ],
        [
            InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
        ]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_payment_methods_keyboard(amount_kopeks: int, language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    if settings.TELEGRAM_STARS_ENABLED:
        keyboard.append([
            InlineKeyboardButton(
                text="⭐ Telegram Stars", 
                callback_data="topup_stars"
            )
        ])
    
    if settings.is_yookassa_enabled():
        keyboard.append([
            InlineKeyboardButton(
                text="💳 Банковская карта (YooKassa)", 
                callback_data="topup_yookassa"
            )
        ])
    
    if settings.TRIBUTE_ENABLED:
        keyboard.append([
            InlineKeyboardButton(
                text="💎 Банковская карта (Tribute)", 
                callback_data="topup_tribute"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(
            text="🛠️ Через поддержку", 
            callback_data="topup_support"
        )
    ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_balance")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_yookassa_payment_keyboard(
    payment_id: str, 
    amount_kopeks: int, 
    confirmation_url: str,
    language: str = "ru"
) -> InlineKeyboardMarkup:
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💳 Оплатить",
                url=confirmation_url
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Проверить статус",
                callback_data=f"check_yookassa_status_{payment_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="💰 Мой баланс",
                callback_data="menu_balance"
            )
        ]
    ])

def get_autopay_notification_keyboard(subscription_id: int, language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💳 Пополнить баланс", 
                callback_data="balance_topup"
            )
        ],
        [
            InlineKeyboardButton(
                text="📱 Моя подписка", 
                callback_data="menu_subscription"
            )
        ]
    ])

def get_subscription_expiring_keyboard(subscription_id: int, language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⏰ Продлить подписку", 
                callback_data="subscription_extend"
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Пополнить баланс", 
                callback_data="balance_topup"
            )
        ],
        [
            InlineKeyboardButton(
                text="📱 Моя подписка", 
                callback_data="menu_subscription"
            )
        ]
    ])

def get_referral_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.CREATE_INVITE, callback_data="referral_create_invite")
        ],
        [
            InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
        ]
    ])


def get_support_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.CONTACT_SUPPORT, url=f"https://t.me/{settings.SUPPORT_USERNAME.lstrip('@')}")
        ],
        [
            InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
        ]
    ])


def get_pagination_keyboard(
    current_page: int,
    total_pages: int,
    callback_prefix: str,
    language: str = "ru"
) -> List[List[InlineKeyboardButton]]:
    keyboard = []
    
    if total_pages > 1:
        row = []
        
        if current_page > 1:
            row.append(InlineKeyboardButton(
                text="⬅️",
                callback_data=f"{callback_prefix}_page_{current_page - 1}"
            ))
        
        row.append(InlineKeyboardButton(
            text=f"{current_page}/{total_pages}",
            callback_data="current_page"
        ))
        
        if current_page < total_pages:
            row.append(InlineKeyboardButton(
                text="➡️",
                callback_data=f"{callback_prefix}_page_{current_page + 1}"
            ))
        
        keyboard.append(row)
    
    return keyboard

def get_confirmation_keyboard(
    confirm_data: str,
    cancel_data: str = "cancel",
    language: str = "ru"
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.YES, callback_data=confirm_data),
            InlineKeyboardButton(text=texts.NO, callback_data=cancel_data)
        ]
    ])


def get_autopay_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Включить", callback_data="autopay_enable"),
            InlineKeyboardButton(text="❌ Выключить", callback_data="autopay_disable")
        ],
        [
            InlineKeyboardButton(text="⚙️ Настроить дни", callback_data="autopay_set_days")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
        ]
    ])


def get_autopay_days_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    keyboard = []
    
    for days in [1, 3, 7, 14]:
        keyboard.append([
            InlineKeyboardButton(
                text=f"{days} дн{_get_days_suffix(days)}",
                callback_data=f"autopay_days_{days}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="subscription_autopay")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _get_days_suffix(days: int) -> str:
    if days == 1:
        return "ь"
    elif 2 <= days <= 4:
        return "я"
    else:
        return "ей"



def get_extend_subscription_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    periods = [
        (14, f"📅 14 дней - {settings.format_price(settings.PRICE_14_DAYS)}"),
        (30, f"📅 30 дней - {settings.format_price(settings.PRICE_30_DAYS)}"),
        (60, f"📅 60 дней - {settings.format_price(settings.PRICE_60_DAYS)}"),
        (90, f"📅 90 дней - {settings.format_price(settings.PRICE_90_DAYS)}")
    ]
    
    for days, text in periods:
        keyboard.append([
            InlineKeyboardButton(text=text, callback_data=f"extend_period_{days}")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_add_traffic_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    from app.config import settings
    
    if settings.is_traffic_fixed():
        return get_back_keyboard(language)
    
    texts = get_texts(language)
    keyboard = []
    
    packages = [
        (5, f"📊 +5 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_5GB)}"),
        (10, f"📊 +10 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_10GB)}"),
        (25, f"📊 +25 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_25GB)}"),
        (50, f"📊 +50 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_50GB)}"),
        (100, f"📊 +100 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_100GB)}"),
        (0, f"📊 Безлимит - {settings.format_price(settings.PRICE_TRAFFIC_UNLIMITED)}")
    ]
    
    for gb, text in packages:
        keyboard.append([
            InlineKeyboardButton(text=text, callback_data=f"add_traffic_{gb}")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_add_devices_keyboard(current_devices: int, language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    max_devices = 10 
    
    for add_count in range(1, min(6, max_devices - current_devices + 1)): 
        price = add_count * settings.PRICE_PER_DEVICE
        total_devices = current_devices + add_count
        
        add_device_word = _get_device_declension(add_count)
        
        keyboard.append([
            InlineKeyboardButton(
                text=f"📱 +{add_count} {add_device_word} (итого: {total_devices}) - {settings.format_price(price)}",
                callback_data=f"add_devices_{add_count}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_reset_traffic_confirm_keyboard(price_kopeks: int, language: str = "ru") -> InlineKeyboardMarkup:
    from app.config import settings
    
    if settings.is_traffic_fixed():
        return get_back_keyboard(language)
    
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"✅ Сбросить за {settings.format_price(price_kopeks)}", 
                callback_data="confirm_reset_traffic"
            )
        ],
        [
            InlineKeyboardButton(text="⌛ Отмена", callback_data="menu_subscription")
        ]
    ])

def get_manage_countries_keyboard(
    countries: List[dict], 
    selected: List[str], 
    current_subscription_countries: List[str],
    language: str = "ru"
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    for country in countries:
        if not country.get('is_available', True):
            continue
        
        is_currently_connected = country['uuid'] in current_subscription_countries
        is_selected = country['uuid'] in selected
        
        if is_currently_connected:
            if is_selected:
                emoji = "✅"  
                status = ""
            else:
                emoji = "➖"  
                status = " (отключить БЕСПЛАТНО)"
        else:
            if is_selected:
                emoji = "➕"  
                price_text = f" (+{texts.format_price(country['price_kopeks'])})" if country['price_kopeks'] > 0 else " (Бесплатно)"
                status = price_text
            else:
                emoji = "⚪"  
                price_text = f" (+{texts.format_price(country['price_kopeks'])})" if country['price_kopeks'] > 0 else " (Бесплатно)"
                status = price_text
        
        keyboard.append([
            InlineKeyboardButton(
                text=f"{emoji} {country['name']}{status}",
                callback_data=f"country_manage_{country['uuid']}"
            )
        ])
    
    if not keyboard:
        keyboard.append([
            InlineKeyboardButton(
                text="❌ Нет доступных серверов",
                callback_data="no_servers"
            )
        ])
    
    added = [c for c in selected if c not in current_subscription_countries]
    removed = [c for c in current_subscription_countries if c not in selected]
    
    apply_text = "✅ Применить изменения"
    if added or removed:
        changes_count = len(added) + len(removed)
        apply_text += f" ({changes_count})"
    
    keyboard.extend([
        [InlineKeyboardButton(text=apply_text, callback_data="countries_apply")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_subscription")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_device_selection_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    from app.config import settings
    
    keyboard = [
        [
            InlineKeyboardButton(text="📱 iOS (iPhone/iPad)", callback_data="device_guide_ios"),
            InlineKeyboardButton(text="🤖 Android", callback_data="device_guide_android")
        ],
        [
            InlineKeyboardButton(text="💻 Windows", callback_data="device_guide_windows"),
            InlineKeyboardButton(text="🎯 macOS", callback_data="device_guide_mac")
        ],
        [
            InlineKeyboardButton(text="📺 Android TV", callback_data="device_guide_tv")
        ]
    ]
    
    if settings.CONNECT_BUTTON_MODE == "guide":
        keyboard.append([
            InlineKeyboardButton(text="📋 Показать ссылку подписки", callback_data="open_subscription_link")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_connection_guide_keyboard(
    subscription_url: str, 
    app: dict, 
    language: str = "ru"
) -> InlineKeyboardMarkup:
    from app.handlers.subscription import create_deep_link
    
    keyboard = []
    
    if 'installationStep' in app and 'buttons' in app['installationStep']:
        app_buttons = []
        for button in app['installationStep']['buttons']:
            button_text = button['buttonText'].get(language, button['buttonText']['en'])
            app_buttons.append(
                InlineKeyboardButton(text=f"📥 {button_text}", url=button['buttonLink'])
            )
            if len(app_buttons) == 2:
                keyboard.append(app_buttons)
                app_buttons = []
        
        if app_buttons:
            keyboard.append(app_buttons)
    
    keyboard.append([
        InlineKeyboardButton(text="📋 Скопировать ссылку подписки", url=subscription_url)
    ])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text="📱 Выбрать другое устройство", callback_data="subscription_connect")
        ],
        [
            InlineKeyboardButton(text="⬅️ К подписке", callback_data="menu_subscription")
        ]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_app_selection_keyboard(
    device_type: str, 
    apps: list, 
    language: str = "ru"
) -> InlineKeyboardMarkup:
    keyboard = []
    
    for app in apps:
        app_name = app['name']
        if app.get('isFeatured', False):
            app_name = f"⭐ {app_name}"
        
        keyboard.append([
            InlineKeyboardButton(
                text=app_name, 
                callback_data=f"app_{device_type}_{app['id']}"
            )
        ])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text="📱 Выбрать другое устройство", callback_data="subscription_connect")
        ],
        [
            InlineKeyboardButton(text="⬅️ К подписке", callback_data="menu_subscription")
        ]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_specific_app_keyboard(
    subscription_url: str,
    app: dict,
    device_type: str,
    language: str = "ru"
) -> InlineKeyboardMarkup:
    from app.handlers.subscription import create_deep_link
    
    keyboard = []
    
    if 'installationStep' in app and 'buttons' in app['installationStep']:
        app_buttons = []
        for button in app['installationStep']['buttons']:
            button_text = button['buttonText'].get(language, button['buttonText']['en'])
            app_buttons.append(
                InlineKeyboardButton(text=f"📥 {button_text}", url=button['buttonLink'])
            )
            if len(app_buttons) == 2:
                keyboard.append(app_buttons)
                app_buttons = []
        
        if app_buttons:
            keyboard.append(app_buttons)
    
    keyboard.append([
        InlineKeyboardButton(text="📋 Скопировать ссылку подписки", url=subscription_url)
    ])
    
    if 'additionalAfterAddSubscriptionStep' in app and 'buttons' in app['additionalAfterAddSubscriptionStep']:
        for button in app['additionalAfterAddSubscriptionStep']['buttons']:
            button_text = button['buttonText'].get(language, button['buttonText']['en'])
            keyboard.append([
                InlineKeyboardButton(text=button_text, url=button['buttonLink'])
            ])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text="📋 Другие приложения", callback_data=f"app_list_{device_type}")
        ],
        [
            InlineKeyboardButton(text="📱 Выбрать другое устройство", callback_data="subscription_connect")
        ],
        [
            InlineKeyboardButton(text="⬅️ К подписке", callback_data="menu_subscription")
        ]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_extend_subscription_keyboard_with_prices(language: str, prices: dict) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    available_periods = settings.get_available_renewal_periods()
    
    period_display = {
        14: "14 дней",
        30: "30 дней", 
        60: "60 дней",
        90: "90 дней",
        180: "180 дней",
        360: "360 дней"
    }
    
    for days in available_periods:
        if days in prices and days in period_display:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"📅 {period_display[days]} - {texts.format_price(prices[days])}", 
                    callback_data=f"extend_period_{days}"
                )
            ])
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
