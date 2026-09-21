import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from src.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Clase base declarativa para los modelos SQLAlchemy."""
    pass


# Construcción del motor asíncrono
# Compatible tanto con sqlite+aiosqlite como con postgresql+asyncpg (Supabase)
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    connect_args=connect_args,
    pool_pre_ping=True,  # Verifica conexiones vivas (crucial para Supabase / PostgreSQL)
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provee una sesión de base de datos asíncrona transaccional."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as ex:
            await session.rollback()
            logger.error(f"Error en transacción de base de datos: {ex}", exc_info=True)
            raise


async def init_db() -> None:
    """Inicializa la base de datos creando las tablas si no existen."""
    logger.info("Inicializando esquema de base de datos...")
    async with engine.begin() as conn:
        # Importar modelos aquí para asegurar su registro en Base.metadata
        from src import models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)

        # Migración ligera para agregar columnas categoria y es_top_categoria si no existen
        def check_columns(sync_conn):
            from sqlalchemy import inspect, text
            inspector = inspect(sync_conn)
            if "products" in inspector.get_table_names():
                columns = [col["name"] for col in inspector.get_columns("products")]
                if "categoria" not in columns:
                    logger.info("Agregando columna faltante 'categoria' a la tabla products...")
                    sync_conn.execute(text("ALTER TABLE products ADD COLUMN categoria VARCHAR(100)"))
                if "es_top_categoria" not in columns:
                    logger.info("Agregando columna faltante 'es_top_categoria' a la tabla products...")
                    sync_conn.execute(text("ALTER TABLE products ADD COLUMN es_top_categoria BOOLEAN DEFAULT 0"))

        await conn.run_sync(check_columns)
    logger.info("Base de datos inicializada correctamente.")


async def close_db() -> None:
    """Cierra las conexiones del pool de la base de datos."""
    logger.info("Cerrando pool de conexiones de base de datos...")
    await engine.dispose()
