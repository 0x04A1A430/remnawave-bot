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

    MAIN_MENU_PHOTO_ENABLED: bool = False
    MAIN_MENU_PHOTO_URL: Optional[str] = None
    MAIN_MENU_PHOTO_PATH: Optional[str] = None
    
    TRIAL_DURATION_DAYS: int = 3
    TRIAL_TRAFFIC_LIMIT_GB: int = 10
    TRIAL_DEVICE_LIMIT: int = 2
    DEFAULT_TRAFFIC_LIMIT_GB: int = 100
    DEFAULT_DEVICE_LIMIT: int = 1
    TRIAL_SQUAD_UUID: str
    DEFAULT_TRAFFIC_RESET_STRATEGY: str = "MONTH"
    
    TRIAL_WARNING_HOURS: int = 2 
    ENABLE_NOTIFICATIONS: bool = True 
    NOTIFICATION_RETRY_ATTEMPTS: int = 3 
    
    MONITORING_LOGS_RETENTION_DAYS: int = 30 
    NOTIFICATION_CACHE_HOURS: int = 24  
    
    BASE_SUBSCRIPTION_PRICE: int = 50000

    AVAILABLE_SUBSCRIPTION_PERIODS: str = "14,30,60,90,180,360"
    AVAILABLE_RENEWAL_PERIODS: str = "30,90,180"
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

    MAINTENANCE_MODE: bool = False
    MAINTENANCE_CHECK_INTERVAL: int = 30 
    MAINTENANCE_AUTO_ENABLE: bool = True 
    MAINTENANCE_MESSAGE: str = "🔧 Ведутся технические работы. Сервис временно недоступен. Попробуйте позже."
    
    TELEGRAM_STARS_ENABLED: bool = True
    
    TRIBUTE_ENABLED: bool = False
    TRIBUTE_API_KEY: Optional[str] = None
    TRIBUTE_WEBHOOK_SECRET: Optional[str] = None
    TRIBUTE_DONATE_LINK: Optional[str] = None
    TRIBUTE_WEBHOOK_PATH: str = "/tribute-webhook"
    TRIBUTE_WEBHOOK_PORT: int = 8081

    YOOKASSA_ENABLED: bool = False
    YOOKASSA_SHOP_ID: Optional[str] = None
    YOOKASSA_SECRET_KEY: Optional[str] = None
    YOOKASSA_RETURN_URL: Optional[str] = None
    YOOKASSA_DEFAULT_RECEIPT_EMAIL: Optional[str] = None
    YOOKASSA_VAT_CODE: int = 1 
    YOOKASSA_PAYMENT_MODE: str = "full_payment" 
    YOOKASSA_PAYMENT_SUBJECT: str = "service"
    YOOKASSA_WEBHOOK_PATH: str = "/yookassa-webhook"
    YOOKASSA_WEBHOOK_PORT: int = 8082
    YOOKASSA_WEBHOOK_SECRET: Optional[str] = None

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
    
    def is_yookassa_enabled(self) -> bool:
        return (self.YOOKASSA_ENABLED and 
                self.YOOKASSA_SHOP_ID is not None and 
                self.YOOKASSA_SECRET_KEY is not None)
    
    def get_yookassa_return_url(self) -> str:
        if self.YOOKASSA_RETURN_URL:
            return self.YOOKASSA_RETURN_URL
        elif self.WEBHOOK_URL:
            return f"{self.WEBHOOK_URL}/payment-success"
        return "https://t.me/"

    def is_maintenance_mode(self) -> bool:
        return self.MAINTENANCE_MODE
    
    def get_maintenance_message(self) -> str:
        return self.MAINTENANCE_MESSAGE
    
    def get_maintenance_check_interval(self) -> int:
        return self.MAINTENANCE_CHECK_INTERVAL
    
    def is_maintenance_auto_enable(self) -> bool:
        return self.MAINTENANCE_AUTO_ENABLE

    def get_available_subscription_periods(self) -> List[int]:
        try:
            periods_str = self.AVAILABLE_SUBSCRIPTION_PERIODS
            if not periods_str.strip():
                return [30, 90, 180] 
            
            periods = []
            for period_str in periods_str.split(','):
                period_str = period_str.strip()
                if period_str:
                    period = int(period_str)
                    if period in PERIOD_PRICES:
                        periods.append(period)
            
            return periods if periods else [30, 90, 180]
            
        except (ValueError, AttributeError):
            return [30, 90, 180]
    
    def get_available_renewal_periods(self) -> List[int]:
        try:
            periods_str = self.AVAILABLE_RENEWAL_PERIODS
            if not periods_str.strip():
                return [30, 90, 180] 
            
            periods = []
            for period_str in periods_str.split(','):
                period_str = period_str.strip()
                if period_str:
                    period = int(period_str)
                    if period in PERIOD_PRICES:
                        periods.append(period)
            
            return periods if periods else [30, 90, 180]
            
        except (ValueError, AttributeError):
            return [30, 90, 180]

    def is_main_menu_photo_enabled(self) -> bool:
        return self.MAIN_MENU_PHOTO_ENABLED and (
            self.MAIN_MENU_PHOTO_URL is not None or 
            self.MAIN_MENU_PHOTO_PATH is not None
        )
    
    def get_main_menu_photo(self) -> Optional[Union[str, 'FSInputFile']]:
        if not self.is_main_menu_photo_enabled():
            return None
            
        if self.MAIN_MENU_PHOTO_URL:
            return self.MAIN_MENU_PHOTO_URL
            
        if self.MAIN_MENU_PHOTO_PATH and os.path.exists(self.MAIN_MENU_PHOTO_PATH):
            from aiogram.types import FSInputFile
            return FSInputFile(self.MAIN_MENU_PHOTO_PATH)
            
        return None
    
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
