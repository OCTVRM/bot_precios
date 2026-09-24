import pytest
from unittest.mock import patch
from sqlalchemy.pool import NullPool
from src.config import Settings


def test_database_url_normalization():
    """Verifica que URLs de tipo postgres:// o postgresql:// se transformen a postgresql+asyncpg://."""
    s1 = Settings(DATABASE_URL="postgres://user:pass@host:6543/db")
    assert s1.DATABASE_URL == "postgresql+asyncpg://user:pass@host:6543/db"

    s2 = Settings(DATABASE_URL="postgresql://user:pass@host:6543/db")
    assert s2.DATABASE_URL == "postgresql+asyncpg://user:pass@host:6543/db"

    s3 = Settings(DATABASE_URL="postgresql+asyncpg://user:pass@host:6543/db")
    assert s3.DATABASE_URL == "postgresql+asyncpg://user:pass@host:6543/db"

    s4 = Settings(DATABASE_URL="sqlite+aiosqlite:///./test.db")
    assert s4.DATABASE_URL == "sqlite+aiosqlite:///./test.db"


def test_pgbouncer_nullpool_configuration():
    """Verifica que para conexiones asyncpg se configure NullPool y prepared_statement_name_func única."""
    import importlib
    import src.database

    test_url = "postgresql+asyncpg://user:pass@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
    with patch("src.config.settings.DATABASE_URL", test_url):
        reloaded = importlib.reload(src.database)
        # Debe usar NullPool para PgBouncer
        assert isinstance(reloaded.engine.pool, NullPool)
        # Debe tener configurada la función de nombres únicos para sentencias preparadas
        name_func = reloaded.connect_args.get("prepared_statement_name_func")
        assert name_func is not None
        assert callable(name_func)
        name1 = name_func()
        name2 = name_func()
        assert name1 != name2
        assert name1.startswith("__asyncpg_")
        assert reloaded.connect_args.get("statement_cache_size") == 0
        assert reloaded.connect_args.get("prepared_statement_cache_size") == 0

    # Restaurar reload con la configuración original
    importlib.reload(src.database)
