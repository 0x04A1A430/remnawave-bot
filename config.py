import os
from dataclasses import dataclass, field
from typing import List
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env файл загружен успешно")
except ImportError:
    print("⚠️ python-dotenv не установлен. Используются системные переменные окружения.")
except Exception as e:
    print(f"⚠️ Ошибка загрузки .env файла: {e}")

logger = logging.getLogger(__name__)

@dataclass
class Config:
    # Bot settings
    BOT_TOKEN: str = field(default="")
    DATABASE_URL: str = field(default="sqlite+aiosqlite:///bot.db")
    
    # RemnaWave API settings
    REMNAWAVE_URL: str = field(default="")
    REMNAWAVE_TOKEN: str = field(default="")
    SUBSCRIPTION_BASE_URL: str = field(default="")
    
    # Admin settings
    ADMIN_IDS: List[int] = field(default_factory=list)
    SUPPORT_USERNAME: str = field(default="support")
    
    # Bot settings
    DEFAULT_LANGUAGE: str = field(default="ru")
    BOT_USERNAME: str = field(default="")  # ИМЯ БОТА ДЛЯ РЕФЕРАЛЬНЫХ ССЫЛОК
    
    # Trial subscription settings
    TRIAL_ENABLED: bool = field(default=False)
    TRIAL_DURATION_DAYS: int = field(default=3)
    TRIAL_TRAFFIC_GB: int = field(default=2)
    TRIAL_SQUAD_UUID: str = field(default="")
    

    MONITOR_WARNING_DAYS: int = field(default=3)
    MONITOR_CHECK_INTERVAL: int = field(default=3600)  # 1 час
    MONITOR_DAILY_CHECK_HOUR: int = field(default=9)   # 9 утра

    # Referral program settings
    REFERRAL_FIRST_REWARD: float = field(default=150.0)  # Первая награда
    REFERRAL_REFERRED_BONUS: float = field(default=150.0)  # Бонус приглашенному
    REFERRAL_THRESHOLD: float = field(default=300.0)  # Порог для получения бонуса
    REFERRAL_PERCENTAGE: float = field(default=0.25)  # 25% с платежей

def parse_admin_ids(admin_ids_str: str) -> List[int]:
    """Parse admin IDs from string"""
    if not admin_ids_str:
        return []
    
    try:
        # Support both comma and space separated
        ids_str = admin_ids_str.replace(',', ' ').strip()
        return [int(id_str.strip()) for id_str in ids_str.split() if id_str.strip().isdigit()]
    except ValueError as e:
        logger.error(f"Error parsing admin IDs: {e}")
        return []

def str_to_bool(value: str) -> bool:
    """Convert string to boolean"""
    if isinstance(value, bool):
        return value
    return value.lower() in ('true', '1', 'yes', 'on', 'enabled')

def load_config() -> Config:
    """Load configuration from environment variables"""
    
    # Дебаг: проверяем что переменные загружены
    print(f"🔍 BOT_USERNAME из env: '{os.getenv('BOT_USERNAME', 'НЕ НАЙДЕН')}'")
    print(f"🔍 REFERRAL_FIRST_REWARD из env: '{os.getenv('REFERRAL_FIRST_REWARD', 'НЕ НАЙДЕН')}'")
    
    config = Config(
        # Bot settings
        BOT_TOKEN=os.getenv("BOT_TOKEN", ""),
        DATABASE_URL=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot.db"),
        
        # RemnaWave API settings
        REMNAWAVE_URL=os.getenv("REMNAWAVE_URL", ""),
        REMNAWAVE_TOKEN=os.getenv("REMNAWAVE_TOKEN", ""),
        SUBSCRIPTION_BASE_URL=os.getenv("SUBSCRIPTION_BASE_URL", ""),
        
        # Admin settings
        ADMIN_IDS=parse_admin_ids(os.getenv("ADMIN_IDS", "")),
        SUPPORT_USERNAME=os.getenv("SUPPORT_USERNAME", "support"),
        
        # Bot settings
        DEFAULT_LANGUAGE=os.getenv("DEFAULT_LANGUAGE", "ru"),
        BOT_USERNAME=os.getenv("BOT_USERNAME", ""),  # ИСПРАВЛЕНО: берется из env
        
        # Trial settings
        TRIAL_ENABLED=str_to_bool(os.getenv("TRIAL_ENABLED", "false")),
        TRIAL_DURATION_DAYS=int(os.getenv("TRIAL_DURATION_DAYS", "3")),
        TRIAL_TRAFFIC_GB=int(os.getenv("TRIAL_TRAFFIC_GB", "2")),
        TRIAL_SQUAD_UUID=os.getenv("TRIAL_SQUAD_UUID", ""),

        MONITOR_WARNING_DAYS=int(os.getenv("MONITOR_WARNING_DAYS", "3")),
        MONITOR_CHECK_INTERVAL=int(os.getenv("MONITOR_CHECK_INTERVAL", "3600")),
        MONITOR_DAILY_CHECK_HOUR=int(os.getenv("MONITOR_DAILY_CHECK_HOUR", "9")),
        
        # Referral settings
        REFERRAL_FIRST_REWARD=float(os.getenv("REFERRAL_FIRST_REWARD", "150.0")),
        REFERRAL_REFERRED_BONUS=float(os.getenv("REFERRAL_REFERRED_BONUS", "150.0")),
        REFERRAL_THRESHOLD=float(os.getenv("REFERRAL_THRESHOLD", "300.0")),
        REFERRAL_PERCENTAGE=float(os.getenv("REFERRAL_PERCENTAGE", "0.25")),
    )
    
    print(f"✅ Config BOT_USERNAME: '{config.BOT_USERNAME}'")
    print(f"✅ Config REFERRAL_FIRST_REWARD: {config.REFERRAL_FIRST_REWARD}")
    
    return config

def load_config_manual_dotenv() -> Config:
    """Load config with manual .env parsing"""
    
    # Загружаем .env файл вручную если python-dotenv не доступен
    env_path = ".env"
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        # Убираем кавычки если есть
                        value = value.strip('"\'')
                        os.environ[key] = value
            print(f"✅ Файл {env_path} загружен вручную")
        except Exception as e:
            print(f"⚠️ Ошибка загрузки {env_path}: {e}")
    
    return load_config()

def debug_environment():
    """Debug environment variables loading"""
    print("\n=== DEBUG ENVIRONMENT VARIABLES ===")
    
    # Ключевые переменные для проверки
    key_vars = [
        'BOT_TOKEN', 'BOT_USERNAME', 'REMNAWAVE_URL', 'REMNAWAVE_TOKEN',
        'ADMIN_IDS', 'REFERRAL_FIRST_REWARD', 'REFERRAL_THRESHOLD'
    ]
    
    for var in key_vars:
        value = os.getenv(var, 'НЕ УСТАНОВЛЕНА')
        print(f"{var}: {value}")
    
    print("=" * 40)
    
    # Проверяем файл .env
    if os.path.exists('.env'):
        print("📁 Файл .env найден")
        try:
            with open('.env', 'r') as f:
                lines = f.readlines()
                print(f"📄 Строк в .env: {len(lines)}")
                for i, line in enumerate(lines[:5], 1):  # Первые 5 строк
                    if 'TOKEN' not in line:  # Не показываем токены
                        print(f"   {i}: {line.strip()}")
        except Exception as e:
            print(f"❌ Ошибка чтения .env: {e}")
    else:
        print("❌ Файл .env не найден в текущей директории")
        print(f"📍 Текущая директория: {os.getcwd()}")
    
    print("=" * 40 + "\n")
