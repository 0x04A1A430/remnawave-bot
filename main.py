import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ДОБАВЛЯЕМ ДЕБАГ ЗАГРУЗКИ .env ПЕРЕД ИМПОРТОМ CONFIG
print("🚀 Запуск бота...")
print(f"📍 Рабочая директория: {os.getcwd()}")
print(f"📁 Файлы в директории: {os.listdir('.')}")

# Проверяем наличие .env файла
if os.path.exists('.env'):
    print("✅ Файл .env найден")
else:
    print("❌ Файл .env НЕ НАЙДЕН!")
    print("💡 Создайте файл .env в корне проекта")

# Import our modules ПОСЛЕ проверки .env
from config import load_config, debug_environment
from database import Database
from remnawave_api import RemnaWaveAPI
from subscription_monitor import create_subscription_monitor
from middlewares import DatabaseMiddleware, UserMiddleware, LoggingMiddleware, ThrottlingMiddleware, WorkflowDataMiddleware, BotMiddleware
from handlers import router
from admin_handlers import admin_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class BotApplication:
    """Main bot application class"""
    
    def __init__(self):
        self.config = None
        self.db = None
        self.api = None
        self.bot = None
        self.dp = None
        self.monitor_service = None
        
    async def initialize(self):
        """Initialize all components"""
        
        # ДЕБАГ: проверяем переменные окружения
        debug_environment()
        
        # Load configuration
        self.config = load_config()
        
        # ДЕБАГ: проверяем что загрузилось
        print(f"🔧 Загруженная конфигурация:")
        print(f"   BOT_USERNAME: '{self.config.BOT_USERNAME}'")
        print(f"   REFERRAL_FIRST_REWARD: {self.config.REFERRAL_FIRST_REWARD}")
        print(f"   ADMIN_IDS: {self.config.ADMIN_IDS}")
        
        # Validate required environment variables
        if not self.config.BOT_TOKEN:
            logger.error("BOT_TOKEN is required")
            raise ValueError("BOT_TOKEN is required")
        
        if not self.config.REMNAWAVE_URL or not self.config.REMNAWAVE_TOKEN:
            logger.error("REMNAWAVE_URL and REMNAWAVE_TOKEN are required")
            raise ValueError("REMNAWAVE_URL and REMNAWAVE_TOKEN are required")
        
        # ПРЕДУПРЕЖДЕНИЕ если BOT_USERNAME не установлен
        if not self.config.BOT_USERNAME:
            logger.warning("⚠️  BOT_USERNAME не установлен! Реферальные ссылки работать не будут!")
            print("💡 Добавьте BOT_USERNAME=your_bot_username в .env файл")
        
        logger.info("Starting RemnaWave Bot...")
        logger.info(f"RemnaWave URL: {self.config.REMNAWAVE_URL}")
        logger.info(f"Admin IDs: {self.config.ADMIN_IDS}")
        logger.info(f"Bot Username: {self.config.BOT_USERNAME}")
        
        # Initialize database
        self.db = Database(self.config.DATABASE_URL)
        await self._init_database()
        
        # Initialize RemnaWave API
        self.api = RemnaWaveAPI(
            self.config.REMNAWAVE_URL, 
            self.config.REMNAWAVE_TOKEN, 
            self.config.SUBSCRIPTION_BASE_URL
        )
        logger.info("RemnaWave API initialized")
        
        # Test API connection (optional - don't fail if it doesn't work)
        await self._test_api_connection()
        
        # Initialize bot and dispatcher
        self.bot = Bot(
            token=self.config.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        
        # Test bot token
        await self._test_bot_token()
        
        # Initialize dispatcher
        self._setup_dispatcher()
        
        # Initialize subscription monitor service
        await self._init_monitor_service()
        
    async def _init_database(self):
        """Initialize database with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self.db.init_db()
                logger.info("Database initialized successfully")
                break
            except Exception as e:
                logger.error(f"Database initialization attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error("Failed to initialize database after all retries")
                    raise
                await asyncio.sleep(2)  # Wait before retry
                
    async def _test_api_connection(self):
        """Test API connection"""
        try:
            system_stats = await self.api.get_system_stats()
            if system_stats:
                logger.info("RemnaWave API connection successful")
            else:
                logger.warning("RemnaWave API connection test failed - continuing anyway")
        except Exception as e:
            logger.warning(f"RemnaWave API connection error: {e} - continuing anyway")
            
    async def _test_bot_token(self):
        """Test bot token before starting"""
        try:
            bot_info = await self.bot.get_me()
            logger.info(f"Bot started: @{bot_info.username} ({bot_info.first_name})")
            
            # АВТОМАТИЧЕСКИ обновляем BOT_USERNAME если он не был установлен
            if not self.config.BOT_USERNAME and bot_info.username:
                self.config.BOT_USERNAME = bot_info.username
                logger.info(f"✅ BOT_USERNAME автоматически установлен: {bot_info.username}")
                print("💡 Добавьте BOT_USERNAME в .env файл для постоянного сохранения")
            
        except Exception as e:
            logger.error(f"Invalid bot token or network error: {e}")
            raise
            
    def _setup_dispatcher(self):
        """Setup dispatcher with middlewares and routers"""
        storage = MemoryStorage()
        self.dp = Dispatcher(storage=storage)
        
        # Store config, api, db, and monitor_service in dispatcher workflow_data for access in handlers
        self.dp.workflow_data.update({
            "config": self.config,
            "api": self.api,
            "db": self.db,
            "monitor_service": None  # Will be updated after monitor service is created
        })
        
        # Setup middlewares in correct order
        self.dp.message.middleware(LoggingMiddleware())
        self.dp.callback_query.middleware(LoggingMiddleware())
        
        self.dp.message.middleware(ThrottlingMiddleware(rate_limit=0.5))
        self.dp.callback_query.middleware(ThrottlingMiddleware(rate_limit=0.3))
        
        self.dp.message.middleware(WorkflowDataMiddleware())
        self.dp.callback_query.middleware(WorkflowDataMiddleware())
        
        self.dp.message.middleware(BotMiddleware(self.bot))
        self.dp.callback_query.middleware(BotMiddleware(self.bot))
        
        self.dp.message.middleware(DatabaseMiddleware(self.db))
        self.dp.callback_query.middleware(DatabaseMiddleware(self.db))
        
        self.dp.message.middleware(UserMiddleware(self.db, self.config))
        self.dp.callback_query.middleware(UserMiddleware(self.db, self.config))
        
        # Register routers
        self.dp.include_router(router)
        self.dp.include_router(admin_router)
        
    async def _init_monitor_service(self):
        """Initialize subscription monitor service"""
        try:
            self.monitor_service = await create_subscription_monitor(
                self.bot, self.db, self.config, self.api
            )
            
            # Update workflow_data with monitor service
            self.dp.workflow_data["monitor_service"] = self.monitor_service
            
            # Start the monitor service
            await self.monitor_service.start()
            logger.info("Subscription monitor service started successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize monitor service: {e}")
            # Don't fail the entire application if monitor service fails
            logger.warning("Continuing without monitor service")
            self.monitor_service = None
            
    async def start(self):
        """Start bot polling"""
        logger.info("Bot polling started successfully")
        
        # ФИНАЛЬНАЯ ПРОВЕРКА конфигурации
        if self.config.BOT_USERNAME:
            logger.info(f"🎁 Реферальная система активна! Ссылки: https://t.me/{self.config.BOT_USERNAME}?start=ref_USERID")
        else:
            logger.warning("⚠️  Реферальная система неактивна! Установите BOT_USERNAME")
        
        try:
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"Error during polling: {e}")
            raise
        finally:
            await self.shutdown()
            
    async def shutdown(self):
        """Shutdown all services"""
        logger.info("Shutting down bot...")
        
        # Stop monitor service first
        if self.monitor_service:
            try:
                await self.monitor_service.stop()
                logger.info("Monitor service stopped")
            except Exception as e:
                logger.error(f"Error stopping monitor service: {e}")
        
        # Close API connection
        if self.api:
            try:
                await self.api.close()
                logger.info("API connection closed")
            except Exception as e:
                logger.error(f"Error closing API: {e}")
        
        # Close database connection
        if self.db:
            try:
                await self.db.close()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database: {e}")
        
        # Close bot session
        if self.bot:
            try:
                await self.bot.session.close()
                logger.info("Bot session closed")
            except Exception as e:
                logger.error(f"Error closing bot session: {e}")
        
        logger.info("Bot shutdown complete")

async def main():
    """Main function"""
    app = None
    try:
        app = BotApplication()
        await app.initialize()
        await app.start()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"Critical error in main: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        if app:
            await app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
