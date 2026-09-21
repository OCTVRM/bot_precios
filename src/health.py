import datetime
import logging
from typing import Optional
from aiohttp import web
from src.config import settings

logger = logging.getLogger(__name__)

_START_TIME = datetime.datetime.now(datetime.timezone.utc)


async def handle_health(request: web.Request) -> web.Response:
    """Endpoint HTTP ligero para monitores de salud (Render, UptimeRobot, etc.)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    uptime_seconds = int((now - _START_TIME).total_seconds())
    return web.json_response({
        "status": "healthy",
        "service": "bot_precios",
        "uptime_seconds": uptime_seconds,
        "timestamp": now.isoformat(),
    })


def create_health_app() -> web.Application:
    """Crea la aplicación web aiohttp para healthcheck."""
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    return app


async def start_health_server(port: Optional[int] = None) -> web.AppRunner:
    """Inicia el servidor HTTP de healthcheck en segundo plano."""
    http_port = port if port is not None else settings.PORT
    app = create_health_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", http_port)
    await site.start()
    logger.info(f"Servidor HTTP de healthcheck iniciado en http://0.0.0.0:{http_port} (/ y /health)")
    return runner
