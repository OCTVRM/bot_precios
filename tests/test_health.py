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
    finally:
        await client.close()
