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

        # Configuración de permisos GRANT y privilegios por defecto para Supabase Data API
        def configure_supabase_permissions(sync_conn):
            if sync_conn.dialect.name == "postgresql":
                from sqlalchemy import text
                logger.info("Configurando permisos explícitos GRANT y privilegios para Supabase Data API...")
                try:
                    sync_conn.execute(text("""
                        DO $$
                        BEGIN
                            -- Verificar si los roles estándar de Supabase están disponibles
                            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') AND
                               EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') AND
                               EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN

                                -- 1. Uso de esquema public
                                GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

                                -- 2. Permisos y RLS a tablas públicas para Data API (PostgREST / Supabase JS)
                                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'products') THEN
                                    ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
                                    GRANT SELECT ON TABLE public.products TO anon, authenticated;
                                    GRANT ALL ON TABLE public.products TO service_role;
                                END IF;

                                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'price_history') THEN
                                    ALTER TABLE public.price_history ENABLE ROW LEVEL SECURITY;
                                    GRANT SELECT ON TABLE public.price_history TO anon, authenticated;
                                    GRANT ALL ON TABLE public.price_history TO service_role;
                                END IF;

                                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'subscriptions') THEN
                                    ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;
                                    REVOKE ALL ON TABLE public.subscriptions FROM anon, authenticated;
                                    GRANT ALL ON TABLE public.subscriptions TO service_role;
                                END IF;

                                -- 3. Permisos en secuencias
                                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
                                GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;

                                -- 4. Privilegios por defecto para futuras tablas y secuencias creadas en public
                                ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO anon, authenticated;
                                ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO service_role;
                                ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO anon, authenticated;
                                ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO service_role;

                                -- 5. Políticas RLS
                                IF NOT EXISTS (
                                    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'products' AND policyname = 'Allow public read access on products'
                                ) THEN
                                    CREATE POLICY "Allow public read access on products"
                                    ON public.products
                                    FOR SELECT
                                    TO anon, authenticated
                                    USING (true);
                                END IF;

                                IF NOT EXISTS (
                                    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'price_history' AND policyname = 'Allow public read access on price_history'
                                ) THEN
                                    CREATE POLICY "Allow public read access on price_history"
                                    ON public.price_history
                                    FOR SELECT
                                    TO anon, authenticated
                                    USING (true);
                                END IF;

                                IF NOT EXISTS (
                                    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'subscriptions' AND policyname = 'Deny public access to subscriptions'
                                ) THEN
                                    CREATE POLICY "Deny public access to subscriptions"
                                    ON public.subscriptions
                                    FOR ALL
                                    TO anon, authenticated
                                    USING (false)
                                    WITH CHECK (false);
                                END IF;
                            END IF;
                        END $$;
                    """))
                except Exception as e:
                    logger.warning(f"No se pudieron configurar permisos automáticos de Supabase (omitido): {e}")

        await conn.run_sync(configure_supabase_permissions)
    logger.info("Base de datos inicializada correctamente.")


async def close_db() -> None:
    """Cierra las conexiones del pool de la base de datos."""
    logger.info("Cerrando pool de conexiones de base de datos...")
    await engine.dispose()
