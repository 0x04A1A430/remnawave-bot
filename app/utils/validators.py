import re
from typing import Optional, Union
from datetime import datetime


def validate_email(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_phone(phone: str) -> bool:
    pattern = r'^\+?[1-9]\d{1,14}$'
    cleaned_phone = re.sub(r'[\s\-\(\)]', '', phone)
    return re.match(pattern, cleaned_phone) is not None


def validate_telegram_username(username: str) -> bool:
    if not username:
        return False
    username = username.lstrip('@')
    pattern = r'^[a-zA-Z0-9_]{5,32}$'
    return re.match(pattern, username) is not None


def validate_promocode(code: str) -> bool:
    if not code or len(code) < 3 or len(code) > 20:
        return False
    return code.replace('_', '').replace('-', '').isalnum()


def validate_amount(amount_str: str, min_amount: float = 0, max_amount: float = float('inf')) -> Optional[float]:
    try:
        amount = float(amount_str.replace(',', '.'))
        if min_amount <= amount <= max_amount:
            return amount
        return None
    except (ValueError, TypeError):
        return None


def validate_positive_integer(value: Union[str, int], max_value: int = None) -> Optional[int]:
    try:
        num = int(value)
        if num > 0 and (max_value is None or num <= max_value):
            return num
        return None
    except (ValueError, TypeError):
        return None


def validate_date_string(date_str: str, date_format: str = "%Y-%m-%d") -> Optional[datetime]:
    try:
        return datetime.strptime(date_str, date_format)
    except ValueError:
        return None


def validate_url(url: str) -> bool:
    pattern = r'^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)$'
    return re.match(pattern, url) is not None


def validate_uuid(uuid_str: str) -> bool:
    pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    return re.match(pattern, uuid_str.lower()) is not None


def validate_traffic_amount(traffic_str: str) -> Optional[int]:
    traffic_str = traffic_str.upper().strip()
    
    if traffic_str in ['UNLIMITED', 'БЕЗЛИМИТ', '∞']:
        return 0
    
    units = {
        'MB': 1,
        'GB': 1024,
        'TB': 1024 * 1024,
        'МБ': 1,
        'ГБ': 1024,
        'ТБ': 1024 * 1024
    }
    
    for unit, multiplier in units.items():
        if traffic_str.endswith(unit):
            try:
                value = float(traffic_str[:-len(unit)].strip())
                return int(value * multiplier)
            except ValueError:
                break
    
    try:
        return int(float(traffic_str))
    except ValueError:
        return None


def validate_subscription_period(days: Union[str, int]) -> Optional[int]:
    try:
        days_int = int(days)
        if 1 <= days_int <= 3650:
            return days_int
        return None
    except (ValueError, TypeError):
        return None


def sanitize_html(text: str) -> str:
    allowed_tags = ['b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 'code', 'pre']
    
    for tag in allowed_tags:
        text = re.sub(f'<{tag}>', f'<{tag}>', text, flags=re.IGNORECASE)
        text = re.sub(f'</{tag}>', f'</{tag}>', text, flags=re.IGNORECASE)
    
    text = re.sub(r'<(?!/?(?:' + '|'.join(allowed_tags) + r')\b)[^>]*>', '', text)
    
    return text


def validate_device_count(count: Union[str, int]) -> Optional[int]:
    try:
        count_int = int(count)
        if 1 <= count_int <= 10:
            return count_int
        return None
    except (ValueError, TypeError):
        return None


def validate_referral_code(code: str) -> bool:
    if not code:
        return False
    
    if code.startswith('ref') and len(code) > 3:
        user_id_part = code[3:]
        return user_id_part.isdigit()
    
    return validate_promocode(code)