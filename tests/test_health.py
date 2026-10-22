import pytest
from aiohttp.test_utils import TestClient, TestServer
from src.health import create_health_app


@pytest.mark.asyncio
async def test_health_endpoints():
    """Verifica que los endpoints / y /health respondan status 200 y json con status 'healthy'."""
    app = create_health_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # Test ruta raíz /
        resp_root = await client.get("/")
        assert resp_root.status == 200
        data_root = await resp_root.json()
        assert data_root["status"] == "healthy"
        assert data_root["service"] == "bot_precios"
        assert "uptime_seconds" in data_root

        # Test ruta /health
        resp_health = await client.get("/health")
        assert resp_health.status == 200
        data_health = await resp_health.json()
        assert data_health["status"] == "healthy"
        assert "timestamp" in data_health
        assert "database" in data_health
        assert "telegram" in data_health
        assert "is_sqlite" in data_health["database"]

        # Test método HEAD en / y /health
        resp_head_root = await client.head("/")
        assert resp_head_root.status == 200

        resp_head_health = await client.head("/health")
        assert resp_head_health.status == 200

        # Test rutas adicionales /healthz y /ping
        resp_healthz = await client.get("/healthz")
        assert resp_healthz.status == 200

        resp_ping = await client.get("/ping")
        assert resp_ping.status == 200

        # Test endpoint diagnóstico de Telegram
        resp_tg = await client.get("/health/test-telegram")
        assert resp_tg.status in (200, 400, 500)
    finally:
        await client.close()
