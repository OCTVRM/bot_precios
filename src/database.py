import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import uuid
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from src.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Clase base declarativa para los modelos SQLAlchemy."""
    pass


# Construcción del motor asíncrono
# Compatible tanto con sqlite+aiosqlite como con postgresql+asyncpg (Supabase)
connect_args = {}
engine_kwargs = {
    "echo": False,
    "future": True,
    "pool_pre_ping": True,  # Verifica conexiones vivas (crucial para Supabase / PostgreSQL)
}

db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+asyncpg://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

if db_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
elif "asyncpg" in db_url or "postgres" in db_url:
    # Crucial para PgBouncer / Supabase Transaction Pooler (puerto 6543):
    # 1. Deshabilita el caché de sentencias preparadas en el driver asyncpg para evitar
    #    conflictos cuando el pooler multiplexa conexiones físicas.
    connect_args["statement_cache_size"] = 0
    connect_args["prepared_statement_cache_size"] = 0
    # 2. Genera nombres únicos UUID para cualquier sentencia preparada interna,
    #    evitando colisiones con '__asyncpg_stmt_1__'.
    connect_args["prepared_statement_name_func"] = lambda: f"__asyncpg_{uuid.uuid4().hex}__"
    # 3. Usa NullPool con PgBouncer en transaction mode: evita el doble pool (SQLAlchemy + PgBouncer)
    #    y previene estados huérfanos entre transacciones.
    engine_kwargs["poolclass"] = NullPool

engine: AsyncEngine = create_async_engine(
    db_url,
    connect_args=connect_args,
    **engine_kwargs,
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
                    is_sqlite = db_url.startswith("sqlite")
                    default_bool = "0" if is_sqlite else "FALSE"
                    sync_conn.execute(text(f"ALTER TABLE products ADD COLUMN es_top_categoria BOOLEAN DEFAULT {default_bool}"))

        await conn.run_sync(check_columns)
    logger.info("Base de datos inicializada correctamente.")


async def close_db() -> None:
    """Cierra las conexiones del pool de la base de datos."""
    logger.info("Cerrando pool de conexiones de base de datos...")
    await engine.dispose()
