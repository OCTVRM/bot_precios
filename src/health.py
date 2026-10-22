import datetime
import logging
from typing import Optional
from aiohttp import web
from src.config import settings

logger = logging.getLogger(__name__)

_START_TIME = datetime.datetime.now(datetime.timezone.utc)


async def handle_health(request: web.Request) -> web.Response:
    """Endpoint HTTP para monitores de salud y diagnóstico (UptimeRobot, Railway, etc.)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    uptime_seconds = int((now - _START_TIME).total_seconds())

    # Máscara segura de URL de Base de Datos
    db_raw = settings.DATABASE_URL
    if "@" in db_raw:
        scheme_part = db_raw.split("://")[0]
        host_part = db_raw.split("@")[-1]
        masked_db = f"{scheme_part}://***:***@{host_part}"
    else:
        masked_db = db_raw.split("?")[0]

    db_info = {
        "url_masked": masked_db,
        "is_sqlite": db_raw.startswith("sqlite"),
    }

    try:
        from sqlalchemy import text
        from src.database import get_db_session
        async with get_db_session() as session:
            r_act = await session.execute(text("SELECT count(id) FROM products WHERE activo = true"))
            db_info["active_products"] = r_act.scalar() or 0
            r_tot = await session.execute(text("SELECT count(id) FROM products"))
            db_info["total_products"] = r_tot.scalar() or 0
            r_ts = await session.execute(text("SELECT max(timestamp) FROM price_history"))
            max_ts = r_ts.scalar()
            db_info["last_price_history_timestamp"] = max_ts.isoformat() if max_ts else None
    except Exception as ex:
        db_info["error"] = str(ex)

    telegram_info = {
        "token_configured": bool(settings.TELEGRAM_BOT_TOKEN),
        "chat_id_configured": bool(settings.TELEGRAM_CHAT_ID),
        "chat_id": settings.TELEGRAM_CHAT_ID,
    }

    return web.json_response({
        "status": "healthy",
        "service": "bot_precios",
        "uptime_seconds": uptime_seconds,
        "timestamp": now.isoformat(),
        "database": db_info,
        "telegram": telegram_info,
    })


async def handle_test_telegram(request: web.Request) -> web.Response:
    """Endpoint de diagnóstico para probar el envío de alertas directas a Telegram."""
    from src.services.notifier import AlertPayload, TelegramNotifier
    notifier = TelegramNotifier()
    if not notifier.token or not notifier.chat_id:
        return web.json_response({
            "ok": False,
            "error": "TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados en las variables de entorno."
        }, status=400)

    try:
        me = await notifier.bot.get_me()
        test_alert = AlertPayload(
            title="🔔 Prueba de Conectividad - Bot de Precios",
            store="Sistema",
            old_price=99990.0,
            new_price=49990.0,
            discount_percent=50.0,
            affiliate_url="https://github.com/octvrm/bot_precios",
            is_all_time_low=True,
            category="Diagnóstico",
        )
        sent = await notifier.send_alert(test_alert)
        return web.json_response({
            "ok": sent,
            "bot": f"@{me.username}",
            "chat_id": notifier.chat_id,
            "message": "Mensaje de prueba despachado a Telegram." if sent else "Fallo al despachar."
        })
    except Exception as ex:
        return web.json_response({
            "ok": False,
            "error": str(ex),
        }, status=500)


def create_health_app() -> web.Application:
    """Crea la aplicación web aiohttp para healthcheck (soporta GET y HEAD automáticamente)."""
    app = web.Application()
    for path in ("/", "/health", "/healthz", "/ping"):
        app.router.add_get(path, handle_health)
    app.router.add_get("/health/test-telegram", handle_test_telegram)
    app.router.add_post("/health/test-telegram", handle_test_telegram)
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
