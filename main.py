import asyncio
import logging
import sys
import os
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from app.bot import setup_bot
from app.config import settings
from app.database.database import init_db
from app.services.monitoring_service import monitoring_service
from app.external.webhook_server import WebhookServer
from app.database.universal_migration import run_universal_migration


async def main():
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(settings.LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    logger = logging.getLogger(__name__)
    logger.info("🚀 Запуск Bedolaga Remnawave Bot...")
    
    webhook_server = None
    
    try:
        logger.info("📊 Инициализация базы данных...")
        await init_db()
        
        skip_migration = os.getenv('SKIP_MIGRATION', 'false').lower() == 'true'
        
        if not skip_migration:
            logger.info("🔧 Выполняем проверку и миграцию базы данных...")
            try:
                migration_success = await run_universal_migration()
                
                if migration_success:
                    logger.info("✅ Миграция базы данных завершена успешно")
                else:
                    logger.warning("⚠️ Миграция завершилась с предупреждениями, но продолжаем запуск")
                    
            except Exception as migration_error:
                logger.error(f"❌ Ошибка выполнения миграции: {migration_error}")
                logger.warning("⚠️ Продолжаем запуск без миграции")
        else:
            logger.info("ℹ️ Миграция пропущена (SKIP_MIGRATION=true)")
        
        logger.info("🤖 Настройка бота...")
        bot, dp = await setup_bot()
        
        monitoring_service.bot = bot
        
        if settings.TRIBUTE_ENABLED:
            logger.info("🌐 Запуск webhook сервера для Tribute...")
            webhook_server = WebhookServer(bot)
            await webhook_server.start()
        else:
            logger.info("ℹ️ Tribute отключен, webhook сервер не запускается")
        
        logger.info("🔍 Запуск службы мониторинга...")
        monitoring_task = asyncio.create_task(monitoring_service.start_monitoring())
        
        logger.info("🔄 Запуск polling...")
        
        try:
            await asyncio.gather(
                dp.start_polling(bot),
                monitoring_task
            )
        except Exception as e:
            logger.error(f"Ошибка в основном цикле: {e}")
            monitoring_service.stop_monitoring()
            if webhook_server:
                await webhook_server.stop()
            raise
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске: {e}")
        raise
    finally:
        logger.info("🛑 Завершение работы бота")
        monitoring_service.stop_monitoring()
        if webhook_server:
            await webhook_server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)
