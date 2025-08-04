TRANSLATIONS = {
    'ru': {
        # Language selection
        'select_language': '🌍 Выберите язык / Choose language',
        'language_selected': '🇷🇺 Язык выбран: Русский',
        
        # Main menu
        'main_menu': '📱 Главное меню',
        'balance': 'Баланс',
        'my_subscriptions': 'Мои подписки',
        'buy_subscription': 'Купить подписку',
        'support': 'Техподдержка',
        'promocode': 'Промокод',
        'admin_panel': 'Админ панель',
        'trial_subscription': '🆓 Тестовая подписка',
        'trial_not_available': '❌ Тестовая подписка недоступна',
        'trial_success': '🎉 Тестовая подписка успешно активирована!\n\nТеперь вы можете найти её в разделе "Мои подписки".',
        'trial_error': '❌ Ошибка при создании тестовой подписки',
        'trial_info': '🧪 Тестовая подписка выдается на три дня!\n\nТариф действует 3 дня!\n\nОграничение трафика - 2гб!',
        
        # Balance menu
        'your_balance': '💰 Ваш баланс: {balance:.2f} руб.',
        'topup_balance': 'Пополнить баланс',
        'payment_history': 'История платежей',
        'topup_card': 'Пополнение картой',
        'topup_support': 'Через саппорт',
        'back': 'Назад',

        'send_message': 'Отправить сообщение',
        'send_to_user': 'Отправить пользователю',
        'send_to_all': 'Отправить всем',
        'enter_message_text': 'Введите текст сообщения:',
        'enter_user_id_message': 'Введите Telegram ID пользователя:',
        'message_sent': '✅ Сообщение отправлено',
        'broadcast_sent': '✅ Рассылка завершена',
        'broadcast_stats': 'Отправлено: {sent}, Ошибок: {errors}',
        'change_language': 'Сменить язык',
        'language_changed': '✅ Язык изменен',
        'extend_subscription': 'Продлить',
        'subscription_extended': '✅ Подписка продлена',
        'extend_confirmation': 'Продлить подписку "{name}" на {days} дней за {price:.2f} руб.?',
        'subscription_expires_soon': 'Подписка истекает через {days} дней',
        
        # Subscriptions
        'no_subscriptions': '❌ У вас нет активных подписок',
        'subscription_info': '📋 Подписка: {name}\n💰 Цена: {price:.2f} руб.\n⏱ Длительность: {days} дней\n📊 Трафик: {traffic}\n\n{description}',
        'unlimited_traffic': 'Безлимитный',
        'gb_traffic': '{gb} ГБ',
        'buy_subscription_btn': '🛒 Купить за {price:.2f} руб.',
        'get_connection': '🔗 Получить подключение',
        'subscription_expires': 'Истекает: {date}',
        'subscription_expired': 'Истекла',
        'subscription_active': 'Активна до: {date}',
        
        # Payments
        'payment_created': '✅ Заявка на пополнение создана.\nОбратитесь к @{support} для оплаты.',
        'enter_amount': '💰 Введите сумму для пополнения (руб.):',
        'invalid_amount': '❌ Неверная сумма',
        'payment_card_info': '💳 Для пополнения картой свяжитесь с @{support}',
        'no_payments': '❌ История платежей пуста',
        'payment_item': '{date}: {amount:.2f} руб. - {description} ({status})',
        
        # Promocodes
        'enter_promocode': '🎁 Введите промокод:',
        'promocode_success': '✅ Промокод активирован! Скидка: {discount}',
        'promocode_not_found': '❌ Промокод не найден',
        'promocode_expired': '❌ Промокод истек',
        'promocode_used': '❌ Промокод уже использован',
        'promocode_limit': '❌ Лимит использований исчерпан',
        
        # Purchase
        'insufficient_balance': '❌ Недостаточно средств на балансе',
        'subscription_purchased': '✅ Подписка успешно приобретена!',
        'purchase_error': '❌ Ошибка при покупке подписки',
        
        # Support
        'support_message': '🎧 Для получения поддержки обратитесь к @{support}',
        
        # Admin panel
        'admin_menu': '⚙️ Админ панель',
        'manage_subscriptions': 'Управление подписками',
        'manage_users': 'Управление пользователями',
        'manage_balance': 'Управление балансом',
        'manage_promocodes': 'Управление промокодами',
        'statistics': 'Статистика',
        'not_admin': '❌ У вас нет прав администратора',
        
        # Admin - subscriptions
        'create_subscription': 'Создать подписку',
        'enter_sub_name': 'Введите название подписки:',
        'enter_sub_description': 'Введите описание подписки:',
        'enter_sub_price': 'Введите цену подписки (руб.):',
        'enter_sub_days': 'Введите длительность (дней):',
        'enter_sub_traffic': 'Введите лимит трафика (ГБ, 0 для безлимита):',
        'enter_squad_uuid': 'Введите UUID squad из панели:',
        'subscription_created': '✅ Подписка создана',
        
        # Admin - users
        'user_list': 'Список пользователей:',
        'user_item': 'ID: {id}, @{username}, Баланс: {balance:.2f} руб.',
        'enter_user_id': 'Введите Telegram ID пользователя:',
        'enter_balance_amount': 'Введите сумму для пополнения:',
        'balance_added': '✅ Баланс пополнен',
        'user_not_found': '❌ Пользователь не найден',
        
        # Admin - promocodes
        'create_promocode': 'Создать промокод',
        'enter_promo_code': 'Введите код промокода:',
        'enter_promo_discount': 'Введите размер скидки (руб.):',
        'enter_promo_limit': 'Введите лимит использований:',
        'promocode_created': '✅ Промокод создан',
        'promocode_exists': '❌ Промокод уже существует',
        
        # Statistics
        'stats_info': '📊 Статистика:\n👥 Пользователей: {users}\n📋 Подписок: {subscriptions}\n💰 Выручка: {revenue:.2f} руб.',
        
        # Errors
        'error_occurred': '❌ Произошла ошибка',
        'try_again': 'Попробуйте еще раз',
        'invalid_input': '❌ Неверный ввод',
        'cancel': 'Отмена',
        
        # Connection
        'connection_link': '🔗 Ссылка для подключения:\n\n`{link}`\n\nНажмите на ссылку чтобы скопировать',
        'connection_error': '❌ Ошибка получения ссылки подключения',
    },
    
    'en': {
        # Language selection
        'select_language': '🌍 Choose language / Выберите язык',
        'language_selected': '🇺🇸 Language selected: English',
        
        # Main menu
        'main_menu': '📱 Main Menu',
        'balance': 'Balance',
        'my_subscriptions': 'My Subscriptions',
        'buy_subscription': 'Buy Subscription',
        'support': 'Support',
        'promocode': 'Promocode',
        'admin_panel': 'Admin Panel',
        'change_language': 'Change Language',
        'send_message': 'Send messsage',
        'send_to_user': 'Send to user',
        'send_to_all': 'Send to all',
        'enter_user_id_message': 'Enter user id message',
        'enter_message_text': 'Enter message text',
        'trial_subscription': 'Trial subscription',

        
        # Balance menu
        'your_balance': '💰 Your balance: ${balance:.2f}',
        'topup_balance': 'Top up balance',
        'payment_history': 'Payment history',
        'topup_card': 'Card payment',
        'topup_support': 'Through support',
        'back': 'Back',
        
        # Subscriptions
        'no_subscriptions': '❌ You have no active subscriptions',
        'subscription_info': '📋 Subscription: {name}\n💰 Price: ${price:.2f}\n⏱ Duration: {days} days\n📊 Traffic: {traffic}\n\n{description}',
        'unlimited_traffic': 'Unlimited',
        'gb_traffic': '{gb} GB',
        'buy_subscription_btn': '🛒 Buy for ${price:.2f}',
        'get_connection': '🔗 Get connection',
        'subscription_expires': 'Expires: {date}',
        'subscription_expired': 'Expired',
        'subscription_active': 'Active until: {date}',
        
        # Payments
        'payment_created': '✅ Top-up request created.\nContact @{support} for payment.',
        'enter_amount': '💰 Enter amount to top up ($):',
        'invalid_amount': '❌ Invalid amount',
        'payment_card_info': '💳 For card payment contact @{support}',
        'no_payments': '❌ Payment history is empty',
        'payment_item': '{date}: ${amount:.2f} - {description} ({status})',
        
        # Promocodes
        'enter_promocode': '🎁 Enter promocode:',
        'promocode_success': '✅ Promocode activated! Discount: {discount}',
        'promocode_not_found': '❌ Promocode not found',
        'promocode_expired': '❌ Promocode expired',
        'promocode_used': '❌ Promocode already used',
        'promocode_limit': '❌ Usage limit reached',
        
        # Purchase
        'insufficient_balance': '❌ Insufficient balance',
        'subscription_purchased': '✅ Subscription purchased successfully!',
        'purchase_error': '❌ Error purchasing subscription',
        
        # Support
        'support_message': '🎧 For support contact @{support}',
        
        # Admin panel
        'admin_menu': '⚙️ Admin Panel',
        'manage_subscriptions': 'Manage Subscriptions',
        'manage_users': 'Manage Users',
        'manage_balance': 'Manage Balance',
        'manage_promocodes': 'Manage Promocodes',
        'statistics': 'Statistics',
        'not_admin': '❌ You don\'t have admin rights',
        
        # Admin - subscriptions
        'create_subscription': 'Create Subscription',
        'enter_sub_name': 'Enter subscription name:',
        'enter_sub_description': 'Enter subscription description:',
        'enter_sub_price': 'Enter subscription price ($):',
        'enter_sub_days': 'Enter duration (days):',
        'enter_sub_traffic': 'Enter traffic limit (GB, 0 for unlimited):',
        'enter_squad_uuid': 'Enter squad UUID from panel:',
        'subscription_created': '✅ Subscription created',
        
        # Admin - users
        'user_list': '👥 User list:',
        'user_item': 'ID: {id}, @{username}, Balance: ${balance:.2f}',
        'enter_user_id': 'Enter user Telegram ID:',
        'enter_balance_amount': 'Enter amount to add:',
        'balance_added': '✅ Balance added',
        'user_not_found': '❌ User not found',
        
        # Admin - promocodes
        'create_promocode': 'Create Promocode',
        'enter_promo_code': 'Enter promocode:',
        'enter_promo_discount': 'Enter discount amount ($):',
        'enter_promo_limit': 'Enter usage limit:',
        'promocode_created': '✅ Promocode created',
        'promocode_exists': '❌ Promocode already exists',
        
        # Statistics
        'stats_info': '📊 Statistics:\n👥 Users: {users}\n📋 Subscriptions: {subscriptions}\n💰 Revenue: ${revenue:.2f}',
        
        # Errors
        'error_occurred': '❌ An error occurred',
        'try_again': 'Try again',
        'invalid_input': '❌ Invalid input',
        'cancel': 'Cancel',
        
        # Connection
        'connection_link': '🔗 Connection link:\n\n`{link}`\n\nClick the link to copy',
        'connection_error': '❌ Error getting connection link',
    }
}

def t(key: str, lang: str = 'ru', **kwargs) -> str:
    """Get translation for key with optional formatting"""
    translation = TRANSLATIONS.get(lang, TRANSLATIONS['ru']).get(key, key)
    if kwargs:
        try:
            return translation.format(**kwargs)
        except KeyError:
            return translation
    return translation
