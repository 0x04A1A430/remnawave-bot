import os
from dataclasses import dataclass
from typing import List, Dict

@dataclass
class Config:
    BOT_TOKEN: str
    BOT_USERNAME: str
    DATABASE_URL: str
    REMNAWAVE_URL: str
    REMNAWAVE_TOKEN: str
    SUBSCRIPTION_BASE_URL: str
    ADMIN_IDS: List[int]
    SUPPORT_USERNAME: str
    DEFAULT_LANGUAGE: str
    TRIAL_ENABLED: bool
    TRIAL_DURATION_DAYS: int
    TRIAL_TRAFFIC_GB: int
    TRIAL_SQUAD_UUID: str
    REFERRAL_FIRST_REWARD: float
    REFERRAL_REFERRED_BONUS: float
    REFERRAL_THRESHOLD: float
    REFERRAL_PERCENTAGE: float
    MONITOR_ENABLED: bool = True
    MONITOR_CHECK_INTERVAL: int = 3600  
    MONITOR_DAILY_CHECK_HOUR: int = 10  
    MONITOR_WARNING_DAYS: int = 3  
    DELETE_EXPIRED_TRIAL_DAYS: int = 1  
    DELETE_EXPIRED_REGULAR_DAYS: int = 7  
    AUTO_DELETE_ENABLED: bool = False 
    TRIAL_NOTIFICATION_ENABLED: bool = True  
    TRIAL_NOTIFICATION_HOURS_AFTER: int = 2  
    TRIAL_NOTIFICATION_HOURS_WINDOW: int = 22 

    LUCKY_GAME_ENABLED: bool = True
    LUCKY_GAME_REWARD: float = 50.0 
    LUCKY_GAME_NUMBERS: int = 30    
    LUCKY_GAME_WINNING_COUNT: int = 3 
    
    STARS_ENABLED: bool = True
    STARS_RATES: Dict[int, float] = None  

def load_config() -> Config:
    
    def parse_admin_ids(admin_ids_str: str) -> List[int]:
        if not admin_ids_str:
            return []
        try:
            return [int(id_str.strip()) for id_str in admin_ids_str.split(',') if id_str.strip()]
        except (ValueError, AttributeError):
            return []
    
    def get_bool(key: str, default: bool = False) -> bool:
        value = os.getenv(key, '').lower()
        return value in ('true', '1', 'yes', 'on')
    
    def get_int(key: str, default: int = 0) -> int:
        try:
            return int(os.getenv(key, str(default)))
        except (ValueError, TypeError):
            return default
    
    def get_float(key: str, default: float = 0.0) -> float:
        try:
            return float(os.getenv(key, str(default)))
        except (ValueError, TypeError):
            return default
    
    def parse_stars_rates() -> Dict[int, float]:
        default_rates = {
            100: 150.0,
            150: 220.0,
            250: 400.0,
            350: 500.0,
            500: 800.0,
            750: 1150.0,
            1000: 1500.0
        }
        
        custom_rates = {}
        
        for stars in [100, 150, 250, 350, 500, 750, 1000]:
            env_key = f"STARS_{stars}_RATE"
            rate = get_float(env_key, 0.0)
            if rate > 0:
                custom_rates[stars] = rate
        
        if custom_rates:
            return custom_rates
        
        return default_rates
    
    return Config(
        BOT_TOKEN=os.getenv('BOT_TOKEN', ''),
        BOT_USERNAME=os.getenv('BOT_USERNAME', ''),
        DATABASE_URL=os.getenv('DATABASE_URL', 'sqlite+aiosqlite:///bot.db'),
        REMNAWAVE_URL=os.getenv('REMNAWAVE_URL', ''),
        REMNAWAVE_TOKEN=os.getenv('REMNAWAVE_TOKEN', ''),
        SUBSCRIPTION_BASE_URL=os.getenv('SUBSCRIPTION_BASE_URL', ''),
        ADMIN_IDS=parse_admin_ids(os.getenv('ADMIN_IDS', '')),
        SUPPORT_USERNAME=os.getenv('SUPPORT_USERNAME', 'support'),
        DEFAULT_LANGUAGE=os.getenv('DEFAULT_LANGUAGE', 'ru'),
        TRIAL_ENABLED=get_bool('TRIAL_ENABLED', False),
        TRIAL_DURATION_DAYS=get_int('TRIAL_DURATION_DAYS', 3),
        TRIAL_TRAFFIC_GB=get_int('TRIAL_TRAFFIC_GB', 2),
        TRIAL_SQUAD_UUID=os.getenv('TRIAL_SQUAD_UUID', ''),
        REFERRAL_FIRST_REWARD=get_float('REFERRAL_FIRST_REWARD', 150.0),
        REFERRAL_REFERRED_BONUS=get_float('REFERRAL_REFERRED_BONUS', 150.0), 
        REFERRAL_THRESHOLD=get_float('REFERRAL_THRESHOLD', 300.0),
        REFERRAL_PERCENTAGE=get_float('REFERRAL_PERCENTAGE', 0.25),
        MONITOR_ENABLED=get_bool('MONITOR_ENABLED', True),
        MONITOR_CHECK_INTERVAL=get_int('MONITOR_CHECK_INTERVAL', 3600),  
        MONITOR_DAILY_CHECK_HOUR=get_int('MONITOR_DAILY_CHECK_HOUR', 10),  
        MONITOR_WARNING_DAYS=get_int('MONITOR_WARNING_DAYS', 3),  
        DELETE_EXPIRED_TRIAL_DAYS=get_int('DELETE_EXPIRED_TRIAL_DAYS', 1),
        DELETE_EXPIRED_REGULAR_DAYS=get_int('DELETE_EXPIRED_REGULAR_DAYS', 7),
        AUTO_DELETE_ENABLED=get_bool('AUTO_DELETE_ENABLED', False),
        TRIAL_NOTIFICATION_ENABLED=get_bool('TRIAL_NOTIFICATION_ENABLED', True),
        TRIAL_NOTIFICATION_HOURS_AFTER=get_int('TRIAL_NOTIFICATION_HOURS_AFTER', 2),
        TRIAL_NOTIFICATION_HOURS_WINDOW=get_int('TRIAL_NOTIFICATION_HOURS_WINDOW', 22),
        LUCKY_GAME_ENABLED=get_bool('LUCKY_GAME_ENABLED', True),
        LUCKY_GAME_REWARD=get_float('LUCKY_GAME_REWARD', 50.0),
        LUCKY_GAME_NUMBERS=get_int('LUCKY_GAME_NUMBERS', 30),
        LUCKY_GAME_WINNING_COUNT=get_int('LUCKY_GAME_WINNING_COUNT', 3),
        STARS_ENABLED=get_bool('STARS_ENABLED', True),
        STARS_RATES=parse_stars_rates()
    )

def debug_environment():
    env_vars = [
        'BOT_TOKEN', 'BOT_USERNAME', 'DATABASE_URL',
        'REMNAWAVE_URL', 'REMNAWAVE_TOKEN', 'ADMIN_IDS',
        'REFERRAL_FIRST_REWARD', 'REFERRAL_THRESHOLD',
        'MONITOR_ENABLED', 'MONITOR_CHECK_INTERVAL',
        'DELETE_EXPIRED_TRIAL_DAYS', 'DELETE_EXPIRED_REGULAR_DAYS', 'AUTO_DELETE_ENABLED',
        'LUCKY_GAME_ENABLED', 'LUCKY_GAME_REWARD',
        'STARS_ENABLED', 'STARS_100_RATE', 'STARS_500_RATE', 'STARS_1000_RATE'
    ]
    
    print("📋 Environment variables:")
    for var in env_vars:
        value = os.getenv(var, 'NOT SET')
        if 'TOKEN' in var and value != 'NOT SET':
            value = value[:10] + "..." if len(value) > 10 else value
        print(f"   {var}: {value}")
