import logging
import secrets
import string
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, Any

logger = logging.getLogger(__name__)

def is_valid_amount(text: str) -> Tuple[bool, float]:
    try:
        text = text.strip().replace(' ', '').replace(',', '.')
        
        amount = float(text)
        
        if amount <= 0:
            return False, 0.0
        
        if amount > 1000000: 
            return False, 0.0
        
        amount = round(amount, 2)
        
        return True, amount
        
    except (ValueError, TypeError):
        return False, 0.0

def validate_promocode_format(code: str) -> bool:
    if not code:
        return False
    
    code = code.strip().upper()
    
    if len(code) < 3 or len(code) > 20:
        return False
    
    if not code.replace('_', '').isalnum():
        return False
    
    return True

def validate_squad_uuid(uuid: str) -> bool:
    if not uuid or not isinstance(uuid, str):
        return False
    
    uuid = uuid.strip()
    
    if len(uuid) < 8:
        return False
    
    allowed_chars = set('0123456789abcdefABCDEF-')
    if not all(c in allowed_chars for c in uuid):
        return False
    
    return True

def parse_telegram_id(text: str) -> Optional[int]:
    try:
        text = text.strip().replace(' ', '')
        
        if text.startswith('@'):
            text = text[1:]
        
        if text.startswith('id'):
            text = text[2:]
        
        telegram_id = int(text)
        
        if telegram_id <= 0 or telegram_id > 9999999999:
            return None
        
        return telegram_id
        
    except (ValueError, TypeError):
        return None

def generate_username() -> str:
    prefix = "user_"
    random_part = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return prefix + random_part

def generate_password() -> str:
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

def calculate_expiry_date(days: int) -> str:
    expiry_date = datetime.now() + timedelta(days=days)
    return expiry_date.isoformat() + 'Z'

def format_datetime(dt: datetime, language: str = 'ru') -> str:
    if not dt:
        return "N/A"
    
    if language == 'ru':
        return dt.strftime('%d.%m.%Y %H:%M')
    else:
        return dt.strftime('%Y-%m-%d %H:%M')

def format_date(dt: datetime, language: str = 'ru') -> str:
    if not dt:
        return "N/A"
    
    if language == 'ru':
        return dt.strftime('%d.%m.%Y')
    else:
        return dt.strftime('%Y-%m-%d')

def format_bytes(bytes_value: int) -> str:
    if bytes_value == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    value = float(bytes_value)
    
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    
    if value >= 100:
        return f"{value:.0f} {units[unit_index]}"
    elif value >= 10:
        return f"{value:.1f} {units[unit_index]}"
    else:
        return f"{value:.2f} {units[unit_index]}"

def format_payment_status(status: str, language: str = 'ru') -> str:
    status_map = {
        'ru': {
            'pending': 'Ожидает',
            'completed': 'Завершен', 
            'cancelled': 'Отменен',
            'failed': 'Ошибка'
        },
        'en': {
            'pending': 'Pending',
            'completed': 'Completed',
            'cancelled': 'Cancelled',
            'failed': 'Failed'
        }
    }
    
    return status_map.get(language, status_map['ru']).get(status, status)

def format_subscription_info(subscription: Dict[str, Any], language: str = 'ru') -> str:
    text = ""
    
    if language == 'ru':
        text += f"📋 **Подписка: {subscription['name']}**\n\n"
        text += f"💰 Цена: {subscription['price']} руб.\n"
        text += f"⏱ Длительность: {subscription['duration_days']} дн.\n"
        
        if subscription['traffic_limit_gb'] > 0:
            text += f"📊 Лимит трафика: {subscription['traffic_limit_gb']} ГБ\n"
        else:
            text += f"📊 Лимит трафика: Безлимит\n"
        
        if subscription.get('description'):
            text += f"\n📝 Описание:\n{subscription['description']}"
    else:
        text += f"📋 **Subscription: {subscription['name']}**\n\n"
        text += f"💰 Price: ${subscription['price']}\n"
        text += f"⏱ Duration: {subscription['duration_days']} days\n"
        
        if subscription['traffic_limit_gb'] > 0:
            text += f"📊 Traffic limit: {subscription['traffic_limit_gb']} GB\n"
        else:
            text += f"📊 Traffic limit: Unlimited\n"
        
        if subscription.get('description'):
            text += f"\n📝 Description:\n{subscription['description']}"
    
    return text

def format_user_subscription_info(user_sub: Dict[str, Any], subscription: Dict[str, Any], 
                                expires_at: datetime, language: str = 'ru') -> str:
    text = ""
    
    if language == 'ru':
        text += f"📋 **{subscription['name']}**\n\n"
        
        now = datetime.utcnow()
        if expires_at < now:
            status = "❌ Истекла"
            days_left = 0
        elif not user_sub.get('is_active', True):
            status = "⏸ Приостановлена"
            days_left = (expires_at - now).days
        else:
            days_left = (expires_at - now).days
            status = f"✅ Активна"
        
        text += f"🔘 Статус: {status}\n"
        text += f"📅 Истекает: {format_datetime(expires_at, language)}\n"
        
        if days_left > 0:
            text += f"⏰ Осталось: {days_left} дн.\n"
        
        if subscription['traffic_limit_gb'] > 0:
            text += f"📊 Лимит трафика: {subscription['traffic_limit_gb']} ГБ\n"
        else:
            text += f"📊 Лимит трафика: Безлимит\n"
        
        if subscription.get('name') == "Старая подписка" or (subscription.get('description') and 'импорт' in subscription.get('description', '').lower()):
            text += f"\n🔄 Тип: Импортированная из старой системы\n"
            text += f"ℹ️ Продление недоступно"
        
        if subscription.get('description') and not ('импорт' in subscription.get('description', '').lower()):
            text += f"\n📝 {subscription['description']}"
    else:
        text += f"📋 **{subscription['name']}**\n\n"
        
        now = datetime.utcnow()
        if expires_at < now:
            status = "❌ Expired"
            days_left = 0
        elif not user_sub.get('is_active', True):
            status = "⏸ Suspended"
            days_left = (expires_at - now).days
        else:
            days_left = (expires_at - now).days
            status = f"✅ Active"
        
        text += f"🔘 Status: {status}\n"
        text += f"📅 Expires: {format_datetime(expires_at, language)}\n"
        
        if days_left > 0:
            text += f"⏰ Days left: {days_left}\n"
        
        if subscription['traffic_limit_gb'] > 0:
            text += f"📊 Traffic limit: {subscription['traffic_limit_gb']} GB\n"
        else:
            text += f"📊 Traffic limit: Unlimited\n"
        
        if subscription.get('name') == "Старая подписка" or (subscription.get('description') and 'import' in subscription.get('description', '').lower()):
            text += f"\n🔄 Type: Imported from old system\n"
            text += f"ℹ️ Extension not available"
        
        if subscription.get('description') and not ('import' in subscription.get('description', '').lower()):
            text += f"\n📝 {subscription['description']}"
    
    return text

def log_user_action(user_id: int, action: str, details: str = ""):
    logger.info(f"USER_ACTION: {user_id} - {action}" + (f" - {details}" if details else ""))

def bytes_to_gb(bytes_value: int) -> float:
    if not bytes_value or bytes_value == 0:
        return 0.0
    return round(bytes_value / (1024**3), 2)

def format_memory_usage(used_gb: float, total_gb: float) -> str:
    if total_gb == 0:
        return "N/A"
    
    usage_percent = (used_gb / total_gb) * 100
    available_gb = total_gb - used_gb
    
    return f"{used_gb:.1f}/{total_gb:.1f} ГБ ({usage_percent:.1f}%) • Доступно: {available_gb:.1f} ГБ"

def format_uptime(uptime_seconds: float) -> str:
    if uptime_seconds <= 0:
        return "N/A"
    
    uptime_hours = int(uptime_seconds // 3600)
    uptime_days = uptime_hours // 24
    uptime_hours = uptime_hours % 24
    uptime_minutes = int((uptime_seconds % 3600) // 60)
    
    if uptime_days > 0:
        return f"{uptime_days}д {uptime_hours}ч"
    elif uptime_hours > 0:
        return f"{uptime_hours}ч {uptime_minutes}м"
    else:
        return f"{uptime_minutes}м"
