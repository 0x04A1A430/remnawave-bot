import logging
from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import (
    AsyncSession, 
    create_async_engine, 
    async_sessionmaker,
    AsyncEngine
)
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool
from sqlalchemy import event, text, bindparam
from sqlalchemy.engine import Engine
import time
from app.config import settings
from app.database.models import Base

logger = logging.getLogger(__name__)

# ============================================================================
# PRODUCTION-GRADE CONNECTION POOLING
# ============================================================================

if settings.get_database_url().startswith("sqlite"):
    poolclass = NullPool
    pool_kwargs = {}
else:
    poolclass = AsyncAdaptedQueuePool
    pool_kwargs = {
        "pool_size": 20,
        "max_overflow": 30,
        "pool_timeout": 30,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
        # 🔥 Агрессивная очистка мертвых соединений
        "pool_reset_on_return": "rollback",
    }

# ============================================================================
# ENGINE WITH ADVANCED OPTIMIZATIONS
# ============================================================================

engine = create_async_engine(
    settings.get_database_url(),
    poolclass=poolclass,
    echo=settings.DEBUG,
    future=True,
    **pool_kwargs,
    
    connect_args={
        "server_settings": {
            "application_name": "remnawave_bot",
            "jit": "on",
            "statement_timeout": "60000",  # 60 секунд
            "idle_in_transaction_session_timeout": "300000",  # 5 минут
        },
        "command_timeout": 60,
        "timeout": 10,
    } if not settings.get_database_url().startswith("sqlite") else {},
    
    execution_options={
        "isolation_level": "READ COMMITTED",  # Оптимальный для большинства случаев
        "compiled_cache_size": 500,  # Кеш скомпилированных запросов
    }
)

# ============================================================================
# SESSION FACTORY WITH OPTIMIZATIONS
# ============================================================================

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,  # 🔥 Критично для производительности
    autocommit=False,
)

# ============================================================================
# QUERY PERFORMANCE MONITORING
# ============================================================================

if settings.DEBUG:
    @event.listens_for(Engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("query_start_time", []).append(time.time())
        logger.debug(f"🔍 Executing query: {statement[:100]}...")

    @event.listens_for(Engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        total = time.time() - conn.info["query_start_time"].pop(-1)
        if total > 0.1:  # Логируем медленные запросы > 100ms
            logger.warning(f"🐌 Slow query ({total:.3f}s): {statement[:100]}...")
        else:
            logger.debug(f"⚡ Query executed in {total:.3f}s")

# ============================================================================
# ADVANCED SESSION MANAGER WITH READ REPLICAS
# ============================================================================

class DatabaseManager:
    """Продвинутый менеджер БД с поддержкой реплик и кеширования"""
    
    def __init__(self):
        self.engine = engine
        self.read_replica_engine: Optional[AsyncEngine] = None
        
        if hasattr(settings, 'DATABASE_READ_REPLICA_URL') and settings.DATABASE_READ_REPLICA_URL:
            self.read_replica_engine = create_async_engine(
                settings.DATABASE_READ_REPLICA_URL,
                poolclass=poolclass,
                pool_size=30,  # Больше для read операций
                max_overflow=50,
                pool_pre_ping=True,
                echo=False,
            )
    
    @asynccontextmanager
    async def session(self, read_only: bool = False):
        target_engine = self.read_replica_engine if (read_only and self.read_replica_engine) else self.engine
        
        async_session = async_sessionmaker(
            bind=target_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        
        async with async_session() as session:
            try:
                yield session
                if not read_only:
                    await session.commit()
            except Exception:
                await session.rollback()
                raise
    
    async def health_check(self) -> dict:
        pool = self.engine.pool

        try:
            async with AsyncSessionLocal() as session:
                start = time.time()
                await session.execute(text("SELECT 1"))
                latency = (time.time() - start) * 1000
            status = "healthy"
        except Exception as e:
            logger.error(f"❌ Database health check failed: {e}")
            status = "unhealthy"
            latency = None

        return {
            "status": status,
            "latency_ms": round(latency, 2) if latency else None,
            "pool": _collect_health_pool_metrics(pool),
        }

db_manager = DatabaseManager()

# ============================================================================
# SESSION DEPENDENCY FOR FASTAPI/AIOGRAM
# ============================================================================

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Стандартная dependency для FastAPI"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

async def get_db_read_only() -> AsyncGenerator[AsyncSession, None]:
    """Read-only dependency для тяжелых SELECT запросов"""
    async with db_manager.session(read_only=True) as session:
        yield session

# ============================================================================
# BATCH OPERATIONS FOR PERFORMANCE
# ============================================================================

class BatchOperations:
    """Утилиты для массовых операций"""
    
    @staticmethod
    async def bulk_insert(session: AsyncSession, model, data: list[dict], chunk_size: int = 1000):
        """Массовая вставка с чанками"""
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            session.add_all([model(**item) for item in chunk])
            await session.flush()
        await session.commit()
    
    @staticmethod
    async def bulk_update(session: AsyncSession, model, data: list[dict], chunk_size: int = 1000):
        """Массовое обновление с чанками"""
        if not data:
            return

        primary_keys = [column.name for column in model.__table__.primary_key.columns]
        if not primary_keys:
            raise ValueError("Model must have a primary key for bulk_update")

        updatable_columns = [
            column.name
            for column in model.__table__.columns
            if column.name not in primary_keys
        ]

        if not updatable_columns:
            raise ValueError("No columns available for update in bulk_update")

        stmt = (
            model.__table__.update()
            .where(
                *[
                    getattr(model.__table__.c, pk) == bindparam(pk)
                    for pk in primary_keys
                ]
            )
            .values(
                **{
                    column: bindparam(column, required=False)
                    for column in updatable_columns
                }
            )
        )

        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            filtered_chunk = []
            for item in chunk:
                missing_keys = [pk for pk in primary_keys if pk not in item]
                if missing_keys:
                    raise ValueError(
                        f"Missing primary key values {missing_keys} for bulk_update"
                    )

                filtered_item = {
                    key: value
                    for key, value in item.items()
                    if key in primary_keys or key in updatable_columns
                }
                filtered_chunk.append(filtered_item)

            await session.execute(stmt, filtered_chunk)
        await session.commit()

batch_ops = BatchOperations()

# ============================================================================
# INITIALIZATION AND CLEANUP
# ============================================================================

async def init_db():
    """Инициализация БД с оптимизациями"""
    logger.info("🚀 Создание таблиц базы данных...")
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        if not settings.get_database_url().startswith("sqlite"):
            logger.info("📊 Создание индексов для оптимизации...")
            
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)",
                "CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status) WHERE status = 'active'",
                "CREATE INDEX IF NOT EXISTS idx_payments_created_at ON payments(created_at DESC)",
            ]
            
            for index_sql in indexes:
                try:
                    await conn.execute(text(index_sql))
                except Exception as e:
                    logger.debug(f"Index creation skipped: {e}")
    
    logger.info("✅ База данных успешно инициализирована")
    
    health = await db_manager.health_check()
    logger.info(f"📊 Database health: {health}")

async def close_db():
    """Корректное закрытие всех соединений"""
    logger.info("🔄 Закрытие соединений с БД...")
    
    await engine.dispose()
    
    if db_manager.read_replica_engine:
        await db_manager.read_replica_engine.dispose()
    
    logger.info("✅ Все подключения к базе данных закрыты")

# ============================================================================
# CONNECTION POOL METRICS (для мониторинга)
# ============================================================================

def _pool_counters(pool):
    """Return basic pool counters or ``None`` when unsupported."""

    required_methods = ("size", "checkedin", "checkedout", "overflow")

    for method_name in required_methods:
        method = getattr(pool, method_name, None)
        if method is None or not callable(method):
            return None

    size = pool.size()
    checked_in = pool.checkedin()
    checked_out = pool.checkedout()
    overflow = pool.overflow()

    total_connections = size + overflow

    return {
        "size": size,
        "checked_in": checked_in,
        "checked_out": checked_out,
        "overflow": overflow,
        "total_connections": total_connections,
        "utilization_percent": (checked_out / total_connections * 100) if total_connections else 0.0,
    }


def _collect_health_pool_metrics(pool) -> dict:
    counters = _pool_counters(pool)

    if counters is None:
        return {
            "metrics_available": False,
            "size": 0,
            "checked_in": 0,
            "checked_out": 0,
            "overflow": 0,
            "total_connections": 0,
            "utilization": "0.0%",
        }

    return {
        "metrics_available": True,
        "size": counters["size"],
        "checked_in": counters["checked_in"],
        "checked_out": counters["checked_out"],
        "overflow": counters["overflow"],
        "total_connections": counters["total_connections"],
        "utilization": f"{counters['utilization_percent']:.1f}%",
    }


async def get_pool_metrics() -> dict:
    """Детальные метрики пула для Prometheus/Grafana"""
    pool = engine.pool

    counters = _pool_counters(pool)

    if counters is None:
        return {
            "metrics_available": False,
            "pool_size": 0,
            "checked_in_connections": 0,
            "checked_out_connections": 0,
            "overflow_connections": 0,
            "total_connections": 0,
            "max_possible_connections": 0,
            "pool_utilization_percent": 0.0,
        }

    return {
        "metrics_available": True,
        "pool_size": counters["size"],
        "checked_in_connections": counters["checked_in"],
        "checked_out_connections": counters["checked_out"],
        "overflow_connections": counters["overflow"],
        "total_connections": counters["total_connections"],
        "max_possible_connections": counters["total_connections"] + (getattr(pool, "_max_overflow", 0) or 0),
        "pool_utilization_percent": round(counters["utilization_percent"], 2),
    }
