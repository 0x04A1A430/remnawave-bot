import os
from dataclasses import dataclass
from typing import List

@dataclass
class Config:
    # Bot configuration
    BOT_TOKEN: str
    BOT_USERNAME: str
    
    # Database
    DATABASE_URL: str
    
    # RemnaWave API
    REMNAWAVE_URL: str
    REMNAWAVE_TOKEN: str
    SUBSCRIPTION_BASE_URL: str
    
    # Admin configuration
    ADMIN_IDS: List[int]
    SUPPORT_USERNAME: str
    DEFAULT_LANGUAGE: str
    
    # Trial subscription
    TRIAL_ENABLED: bool
    TRIAL_DURATION_DAYS: int
    TRIAL_TRAFFIC_GB: int
    TRIAL_SQUAD_UUID: str
    
    # Referral system 
    REFERRAL_FIRST_REWARD: float
    REFERRAL_REFERRED_BONUS: float
    REFERRAL_THRESHOLD: float
    REFERRAL_PERCENTAGE: float
    
    # Конфигурация мониторинга подписок
    MONITOR_ENABLED: bool = True
    MONITOR_CHECK_INTERVAL: int = 3600  # 1 час
    MONITOR_DAILY_CHECK_HOUR: int = 10  # 10:00 утра
    MONITOR_WARNING_DAYS: int = 3  # предупреждать за 3 дня

    LUCKY_GAME_ENABLED: bool = True
    LUCKY_GAME_REWARD: float = 50.0  # Размер награды за выигрыш
    LUCKY_GAME_NUMBERS: int = 30     # Общее количество чисел (1-30)
    LUCKY_GAME_WINNING_COUNT: int = 3  # Количество выигрышных чисел

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
    
    return Config(
        # Bot configuration
        BOT_TOKEN=os.getenv('BOT_TOKEN', ''),
        BOT_USERNAME=os.getenv('BOT_USERNAME', ''),
        
        # Database
        DATABASE_URL=os.getenv('DATABASE_URL', 'sqlite+aiosqlite:///bot.db'),
        
        # RemnaWave API
        REMNAWAVE_URL=os.getenv('REMNAWAVE_URL', ''),
        REMNAWAVE_TOKEN=os.getenv('REMNAWAVE_TOKEN', ''),
        SUBSCRIPTION_BASE_URL=os.getenv('SUBSCRIPTION_BASE_URL', ''),
        
        # Admin configuration
        ADMIN_IDS=parse_admin_ids(os.getenv('ADMIN_IDS', '')),
        SUPPORT_USERNAME=os.getenv('SUPPORT_USERNAME', 'support'),
        DEFAULT_LANGUAGE=os.getenv('DEFAULT_LANGUAGE', 'ru'),
        
        # Trial subscription
        TRIAL_ENABLED=get_bool('TRIAL_ENABLED', False),
        TRIAL_DURATION_DAYS=get_int('TRIAL_DURATION_DAYS', 3),
        TRIAL_TRAFFIC_GB=get_int('TRIAL_TRAFFIC_GB', 2),
        TRIAL_SQUAD_UUID=os.getenv('TRIAL_SQUAD_UUID', ''),
        
        # Referral system
        REFERRAL_FIRST_REWARD=get_float('REFERRAL_FIRST_REWARD', 150.0),
        REFERRAL_REFERRED_BONUS=get_float('REFERRAL_REFERRED_BONUS', 150.0), 
        REFERRAL_THRESHOLD=get_float('REFERRAL_THRESHOLD', 300.0),
        REFERRAL_PERCENTAGE=get_float('REFERRAL_PERCENTAGE', 0.25),
        
        MONITOR_ENABLED=get_bool('MONITOR_ENABLED', True),
        MONITOR_CHECK_INTERVAL=get_int('MONITOR_CHECK_INTERVAL', 3600),  # 1 час по умолчанию
        MONITOR_DAILY_CHECK_HOUR=get_int('MONITOR_DAILY_CHECK_HOUR', 10),  # 10 утра
        MONITOR_WARNING_DAYS=get_int('MONITOR_WARNING_DAYS', 3),  # за 3 дня

        # Игра удачи
        LUCKY_GAME_ENABLED=get_bool('LUCKY_GAME_ENABLED', True),
        LUCKY_GAME_REWARD=get_float('LUCKY_GAME_REWARD', 50.0),
        LUCKY_GAME_NUMBERS=get_int('LUCKY_GAME_NUMBERS', 30),
        LUCKY_GAME_WINNING_COUNT=get_int('LUCKY_GAME_WINNING_COUNT', 3)
    )

def debug_environment():
    env_vars = [
        'BOT_TOKEN', 'BOT_USERNAME', 'DATABASE_URL',
        'REMNAWAVE_URL', 'REMNAWAVE_TOKEN', 'ADMIN_IDS',
        'REFERRAL_FIRST_REWARD', 'REFERRAL_THRESHOLD',
        'MONITOR_ENABLED', 'MONITOR_CHECK_INTERVAL',
        'LUCKY_GAME_ENABLED', 'LUCKY_GAME_REWARD' 
    ]
    
    print("📋 Environment variables:")
    for var in env_vars:
        value = os.getenv(var, 'NOT SET')
        if 'TOKEN' in var and value != 'NOT SET':
            value = value[:10] + "..." if len(value) > 10 else value
        print(f"   {var}: {value}")
