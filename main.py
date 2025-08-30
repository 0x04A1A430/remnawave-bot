import asyncio
import logging
import sys
import os
import signal
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from app.bot import setup_bot
from app.config import settings
from app.database.database import init_db
from app.services.monitoring_service import monitoring_service
from app.services.maintenance_service import maintenance_service
from app.external.webhook_server import WebhookServer
from app.database.universal_migration import run_universal_migration


class GracefulExit:
    """Класс для корректного завершения работы бота"""
    
    def __init__(self):
        self.exit = False
        
    def exit_gracefully(self, signum, frame):
        logging.getLogger(__name__).info(f"Получен сигнал {signum}. Корректное завершение работы...")
        self.exit = True


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
    
    # Настройка обработчика сигналов
    killer = GracefulExit()
    signal.signal(signal.SIGINT, killer.exit_gracefully)
    signal.signal(signal.SIGTERM, killer.exit_gracefully)
    
    webhook_server = None
    monitoring_task = None
    maintenance_task = None
    polling_task = None
    
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
        
        # Устанавливаем ссылку на бота в сервисы
        monitoring_service.bot = bot
        
        if settings.TRIBUTE_ENABLED:
            logger.info("🌐 Запуск webhook сервера для Tribute...")
            webhook_server = WebhookServer(bot)
            await webhook_server.start()
        else:
            logger.info("ℹ️ Tribute отключен, webhook сервер не запускается")
        
        logger.info("🔍 Запуск службы мониторинга...")
        monitoring_task = asyncio.create_task(monitoring_service.start_monitoring())
        
        logger.info("🔧 Запуск службы техработ...")
        maintenance_task = asyncio.create_task(maintenance_service.start_monitoring())
        
        logger.info("🔄 Запуск polling...")
        polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
        
        # Ожидание сигнала завершения или исключения
        try:
            while not killer.exit:
                await asyncio.sleep(1)
                
                # Проверяем, не завершились ли задачи с ошибкой
                if monitoring_task.done():
                    exception = monitoring_task.exception()
                    if exception:
                        logger.error(f"Служба мониторинга завершилась с ошибкой: {exception}")
                        monitoring_task = asyncio.create_task(monitoring_service.start_monitoring())
                        
                if maintenance_task.done():
                    exception = maintenance_task.exception()
                    if exception:
                        logger.error(f"Служба техработ завершилась с ошибкой: {exception}")
                        maintenance_task = asyncio.create_task(maintenance_service.start_monitoring())
                        
                if polling_task.done():
                    exception = polling_task.exception()
                    if exception:
                        logger.error(f"Polling завершился с ошибкой: {exception}")
                        break
                        
        except Exception as e:
            logger.error(f"Ошибка в основном цикле: {e}")
            
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске: {e}")
        raise
        
    finally:
        logger.info("🛑 Начинается корректное завершение работы...")
        
        # Останавливаем службы
        if monitoring_task and not monitoring_task.done():
            logger.info("⏹️ Остановка службы мониторинга...")
            monitoring_service.stop_monitoring()
            monitoring_task.cancel()
            try:
                await monitoring_task
            except asyncio.CancelledError:
                pass
        
        if maintenance_task and not maintenance_task.done():
            logger.info("⏹️ Остановка службы техработ...")
            await maintenance_service.stop_monitoring()
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
        
        if polling_task and not polling_task.done():
            logger.info("⏹️ Остановка polling...")
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
        
        if webhook_server:
            logger.info("⏹️ Остановка webhook сервера...")
            await webhook_server.stop()
        
        # Закрываем сессию бота
        if 'bot' in locals():
            try:
                await bot.session.close()
                logger.info("✅ Сессия бота закрыта")
            except Exception as e:
                logger.error(f"Ошибка закрытия сессии бота: {e}")
        
        logger.info("✅ Завершение работы бота завершено")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)