import os
from typing import List, Optional, Union
from pydantic_settings import BaseSettings
from pydantic import field_validator, Field
from pathlib import Path


class Settings(BaseSettings):
    
    BOT_TOKEN: str
    ADMIN_IDS: str = ""
    SUPPORT_USERNAME: str = "@support"
    
    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379/0"
    
    REMNAWAVE_API_URL: str
    REMNAWAVE_API_KEY: str
    
    TRIAL_DURATION_DAYS: int = 3
    TRIAL_TRAFFIC_LIMIT_GB: int = 10
    TRIAL_DEVICE_LIMIT: int = 2
    TRIAL_SQUAD_UUID: str
    DEFAULT_TRAFFIC_RESET_STRATEGY: str = "MONTH"
    
    TRIAL_WARNING_HOURS: int = 2 
    ENABLE_NOTIFICATIONS: bool = True 
    NOTIFICATION_RETRY_ATTEMPTS: int = 3 
    
    MONITORING_LOGS_RETENTION_DAYS: int = 30 
    NOTIFICATION_CACHE_HOURS: int = 24  
    
    BASE_SUBSCRIPTION_PRICE: int = 50000
    
    PRICE_14_DAYS: int = 50000
    PRICE_30_DAYS: int = 99000
    PRICE_60_DAYS: int = 189000
    PRICE_90_DAYS: int = 269000
    PRICE_180_DAYS: int = 499000
    PRICE_360_DAYS: int = 899000
    
    PRICE_TRAFFIC_5GB: int = 10000
    PRICE_TRAFFIC_10GB: int = 19000
    PRICE_TRAFFIC_25GB: int = 45000
    PRICE_TRAFFIC_50GB: int = 85000
    PRICE_TRAFFIC_100GB: int = 159000
    PRICE_TRAFFIC_250GB: int = 369000
    PRICE_TRAFFIC_UNLIMITED: int = 0
    
    PRICE_PER_DEVICE: int = 5000
    
    TRAFFIC_SELECTION_MODE: str = "selectable" 
    FIXED_TRAFFIC_LIMIT_GB: int = 100 
    
    REFERRAL_REGISTRATION_REWARD: int = 5000
    REFERRED_USER_REWARD: int = 2500
    REFERRAL_COMMISSION_PERCENT: int = 10
    
    AUTOPAY_WARNING_DAYS: str = "3,1"
    
    DEFAULT_AUTOPAY_DAYS_BEFORE: int = 3 
    MIN_BALANCE_FOR_AUTOPAY_KOPEKS: int = 10000  
    
    MONITORING_INTERVAL: int = 60
    INACTIVE_USER_DELETE_MONTHS: int = 3
    
    TELEGRAM_STARS_ENABLED: bool = True
    
    TRIBUTE_ENABLED: bool = False
    TRIBUTE_API_KEY: Optional[str] = None
    TRIBUTE_WEBHOOK_SECRET: Optional[str] = None
    TRIBUTE_DONATE_LINK: Optional[str] = None
    TRIBUTE_WEBHOOK_PATH: str = "/tribute-webhook"
    TRIBUTE_WEBHOOK_PORT: int = 8081

    CONNECT_BUTTON_MODE: str = "guide"
    MINIAPP_CUSTOM_URL: str = ""
    
    DEFAULT_LANGUAGE: str = "ru"
    AVAILABLE_LANGUAGES: str = "ru,en"
    
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/bot.log"
    
    DEBUG: bool = False
    WEBHOOK_URL: Optional[str] = None
    WEBHOOK_PATH: str = "/webhook"
    
    APP_CONFIG_PATH: str = "app-config.json"
    ENABLE_DEEP_LINKS: bool = True
    APP_CONFIG_CACHE_TTL: int = 3600 
    
    @field_validator('LOG_FILE', mode='before')
    @classmethod
    def ensure_log_dir(cls, v):
        log_path = Path(v)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return str(log_path)
    
    def is_admin(self, user_id: int) -> bool:
        return user_id in self.get_admin_ids()
    
    def get_admin_ids(self) -> List[int]:
        try:
            admin_ids = self.ADMIN_IDS
            
            if isinstance(admin_ids, str):
                if not admin_ids.strip():
                    return []
                return [int(x.strip()) for x in admin_ids.split(',') if x.strip()]
            
            return []
            
        except (ValueError, AttributeError):
            return []
    
    def get_autopay_warning_days(self) -> List[int]:
        try:
            days = self.AUTOPAY_WARNING_DAYS
            if isinstance(days, str):
                if not days.strip():
                    return [3, 1]
                return [int(x.strip()) for x in days.split(',') if x.strip()]
            return [3, 1]
        except (ValueError, AttributeError):
            return [3, 1]
    
    def get_available_languages(self) -> List[str]:
        try:
            langs = self.AVAILABLE_LANGUAGES
            if isinstance(langs, str):
                if not langs.strip():
                    return ["ru", "en"]
                return [x.strip() for x in langs.split(',') if x.strip()]
            return ["ru", "en"]
        except AttributeError:
            return ["ru", "en"]
    
    def format_price(self, price_kopeks: int) -> str:
        rubles = price_kopeks / 100
        return f"{rubles:.2f} ₽"
    
    def kopeks_to_rubles(self, kopeks: int) -> float:
        return kopeks / 100
    
    def rubles_to_kopeks(self, rubles: float) -> int:
        return int(rubles * 100)
    
    def get_trial_warning_hours(self) -> int:
        return self.TRIAL_WARNING_HOURS
    
    def is_notifications_enabled(self) -> bool:
        return self.ENABLE_NOTIFICATIONS
    
    def get_app_config_path(self) -> str:
        if os.path.isabs(self.APP_CONFIG_PATH):
            return self.APP_CONFIG_PATH
        
        project_root = Path(__file__).parent.parent
        return str(project_root / self.APP_CONFIG_PATH)
    
    def is_deep_links_enabled(self) -> bool:
        return self.ENABLE_DEEP_LINKS
    
    def get_app_config_cache_ttl(self) -> int:
        return self.APP_CONFIG_CACHE_TTL
    
    def is_traffic_selectable(self) -> bool:
        return self.TRAFFIC_SELECTION_MODE.lower() == "selectable"
    
    def is_traffic_fixed(self) -> bool:
        return self.TRAFFIC_SELECTION_MODE.lower() == "fixed"
    
    def get_fixed_traffic_limit(self) -> int:
        return self.FIXED_TRAFFIC_LIMIT_GB
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8"
    }


settings = Settings()


PERIOD_PRICES = {
    14: settings.PRICE_14_DAYS,
    30: settings.PRICE_30_DAYS,
    60: settings.PRICE_60_DAYS,
    90: settings.PRICE_90_DAYS,
    180: settings.PRICE_180_DAYS,
    360: settings.PRICE_360_DAYS,
}

TRAFFIC_PRICES = {
    5: settings.PRICE_TRAFFIC_5GB,
    10: settings.PRICE_TRAFFIC_10GB,
    25: settings.PRICE_TRAFFIC_25GB,
    50: settings.PRICE_TRAFFIC_50GB,
    100: settings.PRICE_TRAFFIC_100GB,
    250: settings.PRICE_TRAFFIC_250GB,
    0: settings.PRICE_TRAFFIC_UNLIMITED, 
}
