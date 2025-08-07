import os
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class Config:
    BOT_TOKEN: str
    REMNAWAVE_URL: str
    REMNAWAVE_TOKEN: str
    REMNAWAVE_MODE: str
    DATABASE_URL: str
    ADMIN_IDS: List[int]
    DEFAULT_LANGUAGE: str
    SUPPORT_USERNAME: str
    
    # Subscription URL settings - ТЕПЕРЬ ОПЦИОНАЛЬНЫЙ
    SUBSCRIPTION_BASE_URL: Optional[str]
    
    # Trial subscription settings
    TRIAL_ENABLED: bool
    TRIAL_DURATION_DAYS: int
    TRIAL_TRAFFIC_GB: int
    TRIAL_SQUAD_UUID: str
    TRIAL_PRICE: float
    
    # Monitor service settings
    MONITOR_CHECK_INTERVAL: int
    MONITOR_DAILY_CHECK_HOUR: int
    MONITOR_WARNING_DAYS: int

def load_config() -> Config:
    """Load configuration from environment variables"""
    # Parse admin IDs
    admin_ids_str = os.getenv('ADMIN_IDS', '')
    admin_ids = []
    if admin_ids_str:
        try:
            admin_ids = [int(x.strip()) for x in admin_ids_str.split(',') if x.strip()]
        except ValueError:
            admin_ids = []
    
    subscription_base_url = os.getenv('SUBSCRIPTION_BASE_URL')
    
    return Config(
        BOT_TOKEN=os.getenv('BOT_TOKEN', ''),
        REMNAWAVE_URL=os.getenv('REMNAWAVE_URL', ''),
        REMNAWAVE_TOKEN=os.getenv('REMNAWAVE_TOKEN', ''),
        REMNAWAVE_MODE=os.getenv('REMNAWAVE_MODE', 'local'),
        DATABASE_URL=os.getenv('DATABASE_URL', 'sqlite+aiosqlite:///bot.db'),
        ADMIN_IDS=admin_ids,
        DEFAULT_LANGUAGE=os.getenv('DEFAULT_LANGUAGE', 'ru'),
        SUPPORT_USERNAME=os.getenv('SUPPORT_USERNAME', 'support'),
        
        # Subscription URL - ТЕПЕРЬ МОЖЕТ БЫТЬ None
        SUBSCRIPTION_BASE_URL=subscription_base_url,
        
        # Trial subscription settings
        TRIAL_ENABLED=os.getenv('TRIAL_ENABLED', 'true').lower() == 'true',
        TRIAL_DURATION_DAYS=int(os.getenv('TRIAL_DURATION_DAYS', '3')),
        TRIAL_TRAFFIC_GB=int(os.getenv('TRIAL_TRAFFIC_GB', '2')),
        TRIAL_SQUAD_UUID=os.getenv('TRIAL_SQUAD_UUID', '19bd5bde-5eea-4368-809c-6ba1ffb93897'),
        TRIAL_PRICE=float(os.getenv('TRIAL_PRICE', '0.0')),
        
        # Monitor service settings
        MONITOR_CHECK_INTERVAL=int(os.getenv('MONITOR_CHECK_INTERVAL', '3600')),
        MONITOR_DAILY_CHECK_HOUR=int(os.getenv('MONITOR_DAILY_CHECK_HOUR', '10')),
        MONITOR_WARNING_DAYS=int(os.getenv('MONITOR_WARNING_DAYS', '2'))
    )
