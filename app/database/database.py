import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool
from app.config import settings
from app.database.models import Base

logger = logging.getLogger(__name__)

if settings.get_database_url().startswith("sqlite"):
    poolclass = NullPool
    pool_kwargs = {}
else:
    poolclass = AsyncAdaptedQueuePool
    pool_kwargs = {
        "pool_size": 20,           # Базовый пул (увеличено для высокой нагрузки)
        "max_overflow": 30,        # Дополнительные соединения при пиках
        "pool_timeout": 30,        # Таймаут получения соединения
        "pool_recycle": 3600,      # Обновление соединений каждый час
        "pool_pre_ping": True,     # Проверка соединения перед использованием
    }

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
        },
        "command_timeout": 60,
        "timeout": 10,
    } if not settings.get_database_url().startswith("sqlite") else {},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    logger.info("Создание таблиц базы данных...")
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("✅ База данных успешно инициализирована")


async def close_db():
    await engine.dispose()
    logger.info("✅ Подключение к базе данных закрыто")
