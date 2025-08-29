import logging
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.database import engine

logger = logging.getLogger(__name__)

async def get_database_type():
    return engine.dialect.name

async def check_table_exists(table_name: str) -> bool:
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'sqlite':
                result = await conn.execute(text(f"""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='{table_name}'
                """))
                return result.fetchone() is not None
                
            elif db_type == 'postgresql':
                result = await conn.execute(text("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = 'public' AND table_name = :table_name
                """), {"table_name": table_name})
                return result.fetchone() is not None
                
            elif db_type == 'mysql':
                result = await conn.execute(text("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = DATABASE() AND table_name = :table_name
                """), {"table_name": table_name})
                return result.fetchone() is not None
                
            return False
            
    except Exception as e:
        logger.error(f"Ошибка проверки существования таблицы {table_name}: {e}")
        return False

async def check_column_exists(table_name: str, column_name: str) -> bool:
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'sqlite':
                result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
                columns = result.fetchall()
                return any(col[1] == column_name for col in columns)
                
            elif db_type == 'postgresql':
                result = await conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = :table_name 
                    AND column_name = :column_name
                """), {"table_name": table_name, "column_name": column_name})
                return result.fetchone() is not None
                
            elif db_type == 'mysql':
                result = await conn.execute(text("""
                    SELECT COLUMN_NAME 
                    FROM information_schema.COLUMNS 
                    WHERE TABLE_NAME = :table_name 
                    AND COLUMN_NAME = :column_name
                """), {"table_name": table_name, "column_name": column_name})
                return result.fetchone() is not None
                
            return False
            
    except Exception as e:
        logger.error(f"Ошибка проверки существования колонки {column_name}: {e}")
        return False

async def create_yookassa_payments_table():
    
    table_exists = await check_table_exists('yookassa_payments')
    if table_exists:
        logger.info("Таблица yookassa_payments уже существует")
        return True
    
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'sqlite':
                create_sql = """
                CREATE TABLE yookassa_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    yookassa_payment_id VARCHAR(255) UNIQUE NOT NULL,
                    amount_kopeks INTEGER NOT NULL,
                    currency VARCHAR(3) DEFAULT 'RUB' NOT NULL,
                    description TEXT NULL,
                    status VARCHAR(50) NOT NULL,
                    is_paid BOOLEAN DEFAULT 0,
                    is_captured BOOLEAN DEFAULT 0,
                    confirmation_url TEXT NULL,
                    metadata_json TEXT NULL,
                    transaction_id INTEGER NULL,
                    payment_method_type VARCHAR(50) NULL,
                    refundable BOOLEAN DEFAULT 0,
                    test_mode BOOLEAN DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    yookassa_created_at DATETIME NULL,
                    captured_at DATETIME NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (transaction_id) REFERENCES transactions(id)
                );
                
                CREATE INDEX idx_yookassa_payments_user_id ON yookassa_payments(user_id);
                CREATE INDEX idx_yookassa_payments_yookassa_id ON yookassa_payments(yookassa_payment_id);
                CREATE INDEX idx_yookassa_payments_status ON yookassa_payments(status);
                """
                
            elif db_type == 'postgresql':
                create_sql = """
                CREATE TABLE yookassa_payments (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    yookassa_payment_id VARCHAR(255) UNIQUE NOT NULL,
                    amount_kopeks INTEGER NOT NULL,
                    currency VARCHAR(3) DEFAULT 'RUB' NOT NULL,
                    description TEXT NULL,
                    status VARCHAR(50) NOT NULL,
                    is_paid BOOLEAN DEFAULT FALSE,
                    is_captured BOOLEAN DEFAULT FALSE,
                    confirmation_url TEXT NULL,
                    metadata_json JSONB NULL,
                    transaction_id INTEGER NULL,
                    payment_method_type VARCHAR(50) NULL,
                    refundable BOOLEAN DEFAULT FALSE,
                    test_mode BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    yookassa_created_at TIMESTAMP NULL,
                    captured_at TIMESTAMP NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (transaction_id) REFERENCES transactions(id)
                );
                
                CREATE INDEX idx_yookassa_payments_user_id ON yookassa_payments(user_id);
                CREATE INDEX idx_yookassa_payments_yookassa_id ON yookassa_payments(yookassa_payment_id);
                CREATE INDEX idx_yookassa_payments_status ON yookassa_payments(status);
                """
                
            elif db_type == 'mysql':
                create_sql = """
                CREATE TABLE yookassa_payments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    yookassa_payment_id VARCHAR(255) UNIQUE NOT NULL,
                    amount_kopeks INT NOT NULL,
                    currency VARCHAR(3) DEFAULT 'RUB' NOT NULL,
                    description TEXT NULL,
                    status VARCHAR(50) NOT NULL,
                    is_paid BOOLEAN DEFAULT FALSE,
                    is_captured BOOLEAN DEFAULT FALSE,
                    confirmation_url TEXT NULL,
                    metadata_json JSON NULL,
                    transaction_id INT NULL,
                    payment_method_type VARCHAR(50) NULL,
                    refundable BOOLEAN DEFAULT FALSE,
                    test_mode BOOLEAN DEFAULT FALSE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    yookassa_created_at DATETIME NULL,
                    captured_at DATETIME NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (transaction_id) REFERENCES transactions(id)
                );
                
                CREATE INDEX idx_yookassa_payments_user_id ON yookassa_payments(user_id);
                CREATE INDEX idx_yookassa_payments_yookassa_id ON yookassa_payments(yookassa_payment_id);
                CREATE INDEX idx_yookassa_payments_status ON yookassa_payments(status);
                """
            else:
                logger.error(f"Неподдерживаемый тип БД для создания таблицы: {db_type}")
                return False
            
            await conn.execute(text(create_sql))
            logger.info("Таблица yookassa_payments успешно создана")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка создания таблицы yookassa_payments: {e}")
        return False

async def add_remnawave_v2_columns():
    
    columns_to_add = {
        'lifetime_used_traffic_bytes': 'BIGINT DEFAULT 0',
        'last_remnawave_sync': 'TIMESTAMP NULL',
        'trojan_password': 'VARCHAR(255) NULL',
        'vless_uuid': 'VARCHAR(255) NULL',
        'ss_password': 'VARCHAR(255) NULL'
    }
    
    logger.info("=== ПРОВЕРКА КОЛОНОК REMNAWAVE V2.1.5 ===")
    
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            columns_added = 0
            
            for column_name, column_def in columns_to_add.items():
                exists = await check_column_exists('users', column_name)
                
                if not exists:
                    logger.info(f"Добавление колонки {column_name} в таблицу users")
                    
                    if db_type == 'sqlite':
                        if column_def.startswith('BIGINT'):
                            column_def = column_def.replace('BIGINT', 'INTEGER')
                        column_def = column_def.replace('TIMESTAMP', 'DATETIME')
                    elif db_type == 'mysql':
                        column_def = column_def.replace('TIMESTAMP', 'DATETIME')
                    
                    try:
                        await conn.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_def}"))
                        columns_added += 1
                        logger.info(f"Колонка {column_name} успешно добавлена")
                    except Exception as e:
                        logger.error(f"Ошибка добавления колонки {column_name}: {e}")
                        continue
                        
                else:
                    logger.debug(f"Колонка {column_name} уже существует")
            
            if columns_added > 0:
                logger.info(f"Добавлено {columns_added} новых колонок для RemnaWave v2.1.5")
            else:
                logger.info("Все колонки RemnaWave v2.1.5 уже существуют")
                
            return columns_added
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении колонок RemnaWave v2.1.5: {e}")
        return 0

async def fix_subscription_duplicates_universal():
    
    async with engine.begin() as conn:
        db_type = await get_database_type()
        logger.info(f"Обнаружен тип базы данных: {db_type}")
        
        try:
            result = await conn.execute(text("""
                SELECT user_id, COUNT(*) as count 
                FROM subscriptions 
                GROUP BY user_id 
                HAVING COUNT(*) > 1
            """))
            
            duplicates = result.fetchall()
            
            if not duplicates:
                logger.info("Дублирующихся подписок не найдено")
                return 0
                
            logger.info(f"Найдено {len(duplicates)} пользователей с дублирующимися подписками")
            
            total_deleted = 0
            
            for user_id_row, count in duplicates:
                user_id = user_id_row
                
                if db_type == 'sqlite':
                    delete_result = await conn.execute(text("""
                        DELETE FROM subscriptions 
                        WHERE user_id = :user_id AND id NOT IN (
                            SELECT MAX(id) 
                            FROM subscriptions 
                            WHERE user_id = :user_id
                        )
                    """), {"user_id": user_id})
                    
                elif db_type in ['postgresql', 'mysql']:
                    delete_result = await conn.execute(text("""
                        DELETE FROM subscriptions 
                        WHERE user_id = :user_id AND id NOT IN (
                            SELECT max_id FROM (
                                SELECT MAX(id) as max_id
                                FROM subscriptions 
                                WHERE user_id = :user_id
                            ) as subquery
                        )
                    """), {"user_id": user_id})
                
                else:
                    subs_result = await conn.execute(text("""
                        SELECT id FROM subscriptions 
                        WHERE user_id = :user_id 
                        ORDER BY created_at DESC, id DESC
                    """), {"user_id": user_id})
                    
                    sub_ids = [row[0] for row in subs_result.fetchall()]
                    
                    if len(sub_ids) > 1:
                        ids_to_delete = sub_ids[1:]
                        for sub_id in ids_to_delete:
                            await conn.execute(text("""
                                DELETE FROM subscriptions WHERE id = :id
                            """), {"id": sub_id})
                        delete_result = type('Result', (), {'rowcount': len(ids_to_delete)})()
                    else:
                        delete_result = type('Result', (), {'rowcount': 0})()
                
                deleted_count = delete_result.rowcount
                total_deleted += deleted_count
                logger.info(f"Удалено {deleted_count} дублирующихся подписок для пользователя {user_id}")
            
            logger.info(f"Всего удалено дублирующихся подписок: {total_deleted}")
            return total_deleted
            
        except Exception as e:
            logger.error(f"Ошибка при очистке дублирующихся подписок: {e}")
            raise

async def run_universal_migration():
    
    logger.info("=== НАЧАЛО УНИВЕРСАЛЬНОЙ МИГРАЦИИ ===")
    
    try:
        db_type = await get_database_type()
        logger.info(f"Тип базы данных: {db_type}")
        
        await add_remnawave_v2_columns()
        
        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ YOOKASSA ===")
        yookassa_created = await create_yookassa_payments_table()
        if yookassa_created:
            logger.info("✅ Таблица YooKassa payments готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей YooKassa payments")
        
        async with engine.begin() as conn:
            total_subs = await conn.execute(text("SELECT COUNT(*) FROM subscriptions"))
            unique_users = await conn.execute(text("SELECT COUNT(DISTINCT user_id) FROM subscriptions"))
            
            total_count = total_subs.fetchone()[0]
            unique_count = unique_users.fetchone()[0]
            
            logger.info(f"Всего подписок: {total_count}")
            logger.info(f"Уникальных пользователей: {unique_count}")
            
            if total_count == unique_count:
                logger.info("База данных уже в корректном состоянии")
                return True
        
        deleted_count = await fix_subscription_duplicates_universal()
        
        async with engine.begin() as conn:
            final_check = await conn.execute(text("""
                SELECT user_id, COUNT(*) as count 
                FROM subscriptions 
                GROUP BY user_id 
                HAVING COUNT(*) > 1
            """))
            
            remaining_duplicates = final_check.fetchall()
            
            if remaining_duplicates:
                logger.warning(f"Остались дубликаты у {len(remaining_duplicates)} пользователей")
                return False
            else:
                logger.info("=== МИГРАЦИЯ ЗАВЕРШЕНА УСПЕШНО ===")
                return True
                
    except Exception as e:
        logger.error(f"=== ОШИБКА ВЫПОЛНЕНИЯ МИГРАЦИИ: {e} ===")
        return False