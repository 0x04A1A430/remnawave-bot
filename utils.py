import re
import uuid
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
import logging

logger = logging.getLogger(__name__)

def generate_username() -> str:
    """Generate random username for RemnaWave"""
    return f"user_{secrets.token_hex(8)}"

def generate_password() -> str:
    """Generate random password"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(12))

def generate_promocode() -> str:
    """Generate random promocode"""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))

def is_valid_email(email: str) -> bool:
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def is_valid_amount(amount_str: str) -> Tuple[bool, float]:
    """Validate and parse amount"""
    try:
        amount = float(amount_str.replace(',', '.'))
        if amount <= 0:
            return False, 0
        if amount > 100000:  # Max amount limit
            return False, 0
        return True, amount
    except ValueError:
        return False, 0

def format_date(date: datetime, lang: str = 'ru') -> str:
    """Format date for display"""
    if lang == 'ru':
        months = [
            'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
            'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
        ]
        return f"{date.day} {months[date.month-1]} {date.year}"
    else:
        return date.strftime("%B %d, %Y")

def format_datetime(date: datetime, lang: str = 'ru') -> str:
    """Format datetime for display"""
    if lang == 'ru':
        return date.strftime("%d.%m.%Y %H:%M")
    else:
        return date.strftime("%m/%d/%Y %H:%M")

def calculate_expiry_date(days: int) -> str:
    """Calculate expiry date in ISO format"""
    expiry = datetime.utcnow() + timedelta(days=days)
    return expiry.isoformat() + 'Z'

def parse_telegram_id(text: str) -> Optional[int]:
    """Parse Telegram ID from text"""
    try:
        telegram_id = int(text.strip())
        if telegram_id > 0:
            return telegram_id
    except ValueError:
        pass
    return None

def format_traffic(gb: int, lang: str = 'ru') -> str:
    """Format traffic limit for display"""
    if gb == 0:
        return "Безлимитный" if lang == 'ru' else "Unlimited"
    else:
        return f"{gb} ГБ" if lang == 'ru' else f"{gb} GB"

def paginate_list(items: List[Any], page: int, per_page: int = 10) -> Tuple[List[Any], int]:
    """Paginate list of items"""
    total_pages = (len(items) + per_page - 1) // per_page
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    return items[start_idx:end_idx], total_pages

def escape_markdown(text: str) -> str:
    """Escape markdown special characters"""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def truncate_text(text: str, max_length: int = 4000) -> str:
    """Truncate text to fit Telegram message limits"""
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."

def validate_squad_uuid(uuid_str: str) -> bool:
    """Validate UUID format"""
    try:
        uuid.UUID(uuid_str)
        return True
    except ValueError:
        return False

def format_subscription_info(subscription: Dict[str, Any], lang: str = 'ru') -> str:
    """Format subscription information for display"""
    from translations import t
    
    traffic = format_traffic(subscription['traffic_limit_gb'], lang)
    
    info = t('subscription_info', lang,
        name=subscription['name'],
        price=subscription['price'],
        days=subscription['duration_days'],
        traffic=traffic,
        description=subscription.get('description', '')
    )
    
    return info

def format_user_subscription_info(user_sub: Dict[str, Any], subscription: Dict[str, Any], 
                                 expires_at: datetime, lang: str = 'ru') -> str:
    """Format user subscription information"""
    from translations import t
    
    traffic = format_traffic(subscription['traffic_limit_gb'], lang)
    
    # Check if expired
    now = datetime.utcnow()
    if expires_at < now:
        status = t('subscription_expired', lang)
    else:
        status = t('subscription_active', lang, date=format_date(expires_at, lang))
    
    info = f"📋 {subscription['name']}\n"
    info += f"⏱ {subscription['duration_days']} дней\n" if lang == 'ru' else f"⏱ {subscription['duration_days']} days\n"
    info += f"📊 {traffic}\n"
    info += f"🕒 {status}\n"
    
    if subscription.get('description'):
        info += f"\n{subscription['description']}"
    
    return info

def validate_promocode_format(code: str) -> bool:
    """Validate promocode format"""
    if not code:
        return False
    if len(code) < 3 or len(code) > 20:
        return False
    if not re.match(r'^[A-Z0-9]+$', code.upper()):
        return False
    return True

def calculate_discount(original_price: float, promocode: Dict[str, Any]) -> float:
    """Calculate discount amount"""
    if promocode.get('discount_percent'):
        return original_price * (promocode['discount_percent'] / 100)
    else:
        return min(promocode.get('discount_amount', 0), original_price)

def format_payment_status(status: str, lang: str = 'ru') -> str:
    """Format payment status for display"""
    status_map = {
        'pending': 'В ожидании' if lang == 'ru' else 'Pending',
        'completed': 'Завершен' if lang == 'ru' else 'Completed',
        'cancelled': 'Отменен' if lang == 'ru' else 'Cancelled',
        'failed': 'Ошибка' if lang == 'ru' else 'Failed'
    }
    return status_map.get(status, status)

def clean_phone_number(phone: str) -> str:
    """Clean and format phone number"""
    # Remove all non-digit characters
    digits = re.sub(r'\D', '', phone)
    
    # Handle Russian phone numbers
    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
    elif digits.startswith('9') and len(digits) == 10:
        digits = '7' + digits
    
    return digits

def format_bytes(bytes_value: int) -> str:
    """Format bytes to human readable format"""
    if bytes_value == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    value = float(bytes_value)
    
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    else:
        return f"{value:.1f} {units[unit_index]}"

def get_subscription_connection_url(base_url: str, short_uuid: str) -> str:
    """Generate subscription connection URL"""
    return f"{base_url.rstrip('/')}/api/sub/{short_uuid}"

def log_user_action(telegram_id: int, action: str, details: str = None):
    """Log user action for audit"""
    import logging
    logger = logging.getLogger(__name__)
    
    log_message = f"Admin action by {telegram_id}: {action}"
    if details:
        log_message += f" - {details}"
    
    logger.info(log_message)

def format_subscription_status(expires_at: datetime, lang: str = 'ru') -> str:
    """Format subscription status with emoji"""
    now = datetime.utcnow()
    
    if expires_at < now:
        return "❌ Истекла" if lang == 'ru' else "❌ Expired"
    
    days_left = (expires_at - now).days
    
    if days_left == 0:
        return "⚠️ Истекает сегодня" if lang == 'ru' else "⚠️ Expires today"
    elif days_left == 1:
        return "⚠️ Истекает завтра" if lang == 'ru' else "⚠️ Expires tomorrow"
    elif days_left <= 3:
        return f"🔶 Осталось {days_left} дней" if lang == 'ru' else f"🔶 {days_left} days left"
    else:
        return f"✅ Активна ({days_left} дней)" if lang == 'ru' else f"✅ Active ({days_left} days)"

def format_monitor_notification_type(notification_type: str, lang: str = 'ru') -> str:
    """Format notification type for display"""
    type_map = {
        'expired': 'Истекла' if lang == 'ru' else 'Expired',
        'expires_today': 'Истекает сегодня' if lang == 'ru' else 'Expires today',
        'expires_tomorrow': 'Истекает завтра' if lang == 'ru' else 'Expires tomorrow',
        'warning': 'Предупреждение' if lang == 'ru' else 'Warning',
        'urgent': 'Срочно' if lang == 'ru' else 'Urgent'
    }
    return type_map.get(notification_type, notification_type)

def calculate_days_until_expiry(expires_at: datetime) -> int:
    """Calculate days until expiry"""
    now = datetime.utcnow()
    delta = expires_at - now
    return max(0, delta.days)

def is_subscription_expiring_soon(expires_at: datetime, warning_days: int = 2) -> bool:
    """Check if subscription is expiring soon"""
    days_left = calculate_days_until_expiry(expires_at)
    return days_left <= warning_days

class States:
    """State constants for FSM"""
    WAITING_LANGUAGE = "waiting_language"
    WAITING_AMOUNT = "waiting_amount"
    WAITING_PROMOCODE = "waiting_promocode"
    
    # Admin states
    ADMIN_CREATE_SUB_NAME = "admin_create_sub_name"
    ADMIN_CREATE_SUB_DESC = "admin_create_sub_desc"
    ADMIN_CREATE_SUB_PRICE = "admin_create_sub_price"
    ADMIN_CREATE_SUB_DAYS = "admin_create_sub_days"
    ADMIN_CREATE_SUB_TRAFFIC = "admin_create_sub_traffic"
    ADMIN_CREATE_SUB_SQUAD = "admin_create_sub_squad"
    
    ADMIN_ADD_BALANCE_USER = "admin_add_balance_user"
    ADMIN_ADD_BALANCE_AMOUNT = "admin_add_balance_amount"
    
    ADMIN_CREATE_PROMO_CODE = "admin_create_promo_code"
    ADMIN_CREATE_PROMO_DISCOUNT = "admin_create_promo_discount"
    ADMIN_CREATE_PROMO_LIMIT = "admin_create_promo_limit"
