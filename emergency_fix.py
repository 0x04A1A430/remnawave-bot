"""
Экстренное исправление проблемы с отображением подписок
Этот патч добавляет недостающие поля в таблицу user_subscriptions
"""

import asyncio
import sys
import os
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config
from database import Database
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def check_and_add_column(db, column_name, column_definition):
    """Проверяет и добавляет колонку в таблицу"""
    try:
        # Отдельная транзакция для проверки
        async with db.engine.begin() as conn:
            await conn.execute(text(f"SELECT {column_name} FROM user_subscriptions LIMIT 1"))
            logger.info(f"✅ Поле {column_name} уже существует")
            return True
    except Exception:
        # Отдельная транзакция для добавления колонки
        try:
            async with db.engine.begin() as conn:
                logger.info(f"➕ Добавляю поле {column_name}...")
                await conn.execute(text(f"""
                    ALTER TABLE user_subscriptions 
                    ADD COLUMN {column_name} {column_definition}
                """))
                logger.info(f"✅ Поле {column_name} добавлено")
                return True
        except Exception as e:
            logger.error(f"❌ Ошибка при добавлении {column_name}: {e}")
            return False

async def emergency_fix():
    """Экстренное исправление базы данных"""
    
    try:
        # Загружаем конфигурацию
        config = load_config()
        
        # Подключаемся к базе данных  
        db = Database(config.DATABASE_URL)
        
        logger.info("🔧 Выполняю экстренное исправление базы данных...")
        
        # Проверяем существование таблицы user_subscriptions
        try:
            async with db.engine.begin() as conn:
                result = await conn.execute(text("SELECT COUNT(*) FROM user_subscriptions"))
                count = result.scalar()
                logger.info(f"📊 Найдено {count} подписок в таблице user_subscriptions")
        except Exception as e:
            logger.error(f"❌ Таблица user_subscriptions не найдена: {e}")
            await db.close()
            return

        # Добавляем поля по одному в отдельных транзакциях
        success1 = await check_and_add_column(db, "auto_pay_enabled", "BOOLEAN DEFAULT FALSE")
        success2 = await check_and_add_column(db, "auto_pay_days_before", "INTEGER DEFAULT 3")
        
        # Финальная проверка в отдельной транзакции
        if success1 and success2:
            try:
                async with db.engine.begin() as conn:
                    result = await conn.execute(text("""
                        SELECT id, auto_pay_enabled, auto_pay_days_before 
                        FROM user_subscriptions LIMIT 1
                    """))
                    row = result.fetchone()
                    if row:
                        logger.info("✅ Все поля доступны для чтения")
                        logger.info(f"🔍 Пример записи: id={row[0]}, auto_pay_enabled={row[1]}, auto_pay_days_before={row[2]}")
                    else:
                        logger.info("✅ Все поля доступны, но таблица пуста")
                        
            except Exception as e:
                logger.error(f"❌ Поля все еще недоступны: {e}")
        else:
            logger.error("❌ Не удалось добавить все необходимые поля")
                
        await db.close()
        logger.info("🎉 Экстренное исправление завершено!")
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(emergency_fix())
