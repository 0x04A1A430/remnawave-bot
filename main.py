import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# Import our modules
from config import load_config
from database import Database
from remnawave_api import RemnaWaveAPI
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

async def main():
    """Main function"""
    try:
        # Load configuration
        config = load_config()
        
        # Validate required environment variables
        if not config.BOT_TOKEN:
            logger.error("BOT_TOKEN is required")
            return
        
        if not config.REMNAWAVE_URL or not config.REMNAWAVE_TOKEN:
            logger.error("REMNAWAVE_URL and REMNAWAVE_TOKEN are required")
            return
        
        logger.info("Starting RemnaWave Bot...")
        logger.info(f"RemnaWave URL: {config.REMNAWAVE_URL}")
        logger.info(f"Admin IDs: {config.ADMIN_IDS}")
        
        # Initialize database
        db = Database(config.DATABASE_URL)
        
        # Try to initialize database with retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await db.init_db()
                logger.info("Database initialized successfully")
                break
            except Exception as e:
                logger.error(f"Database initialization attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error("Failed to initialize database after all retries")
                    return
                await asyncio.sleep(2)  # Wait before retry
        
        # Initialize RemnaWave API
        api = RemnaWaveAPI(config.REMNAWAVE_URL, config.REMNAWAVE_TOKEN, config.SUBSCRIPTION_BASE_URL)
        logger.info("RemnaWave API initialized")
        
        # Test API connection (optional - don't fail if it doesn't work)
        try:
            system_stats = await api.get_system_stats()
            if system_stats:
                logger.info("RemnaWave API connection successful")
            else:
                logger.warning("RemnaWave API connection test failed - continuing anyway")
        except Exception as e:
            logger.warning(f"RemnaWave API connection error: {e} - continuing anyway")
        
        # Initialize bot and dispatcher
        bot = Bot(
            token=config.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        
        storage = MemoryStorage()
        dp = Dispatcher(storage=storage)
        
        # Store config, api, and db in dispatcher workflow_data for access in handlers
        dp.workflow_data.update({
            "config": config,
            "api": api,
            "db": db
        })
        
        # Setup middlewares in correct order
        dp.message.middleware(LoggingMiddleware())
        dp.callback_query.middleware(LoggingMiddleware())
        
        dp.message.middleware(ThrottlingMiddleware(rate_limit=0.5))
        dp.callback_query.middleware(ThrottlingMiddleware(rate_limit=0.3))
        
        dp.message.middleware(WorkflowDataMiddleware())
        dp.callback_query.middleware(WorkflowDataMiddleware())
        
        dp.message.middleware(BotMiddleware(bot))
        dp.callback_query.middleware(BotMiddleware(bot))
        
        dp.message.middleware(DatabaseMiddleware(db))
        dp.callback_query.middleware(DatabaseMiddleware(db))
        
        dp.message.middleware(UserMiddleware(db, config))
        dp.callback_query.middleware(UserMiddleware(db, config))
        
        # Register routers
        dp.include_router(router)
        dp.include_router(admin_router)
        
        # Setup shutdown handler
        async def on_shutdown():
            logger.info("Shutting down bot...")
            try:
                await api.close()
            except Exception as e:
                logger.error(f"Error closing API: {e}")
            
            try:
                await db.close()
            except Exception as e:
                logger.error(f"Error closing database: {e}")
            
            logger.info("Bot shutdown complete")
        
        # Test bot token before starting
        try:
            bot_info = await bot.get_me()
            logger.info(f"Bot started: @{bot_info.username} ({bot_info.first_name})")
        except Exception as e:
            logger.error(f"Invalid bot token or network error: {e}")
            return
        
        # Start polling
        logger.info("Bot polling started successfully")
        try:
            await dp.start_polling(bot)
        finally:
            await on_shutdown()
            
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"Critical error in main: {e}")
        import traceback
        traceback.print_exc()
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
