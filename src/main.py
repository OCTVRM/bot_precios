import asyncio
import logging
import signal
import sys
from src.bot import dp, notifier, start_bot_polling
from src.config import settings
from src.database import close_db, init_db
from src.scheduler import PriceTrackerScheduler

logger = logging.getLogger("price_tracker")


async def main() -> None:
    """Punto de entrada principal del daemon de rastreo de precios."""
    logger.info("==========================================================")
    logger.info("  INICIANDO SISTEMA DE RASTREO Y ALERTAS DE PRECIOS      ")
    logger.info("==========================================================")
    logger.info(f"Base de datos configurada: {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else settings.DATABASE_URL}")
    logger.info(f"Intervalo de escaneo de precios: cada {settings.CHECK_INTERVAL_MINUTES} minutos")
    logger.info(f"Intervalo de Top 10 por categoría: cada {settings.CATEGORY_SYNC_INTERVAL_HOURS} horas")
    logger.info(f"Canal de Telegram destino: {settings.TELEGRAM_CHAT_ID or 'DESACTIVADO (Sin Token/ID)'}")

    # 1. Inicializar esquema de Base de Datos
    await init_db()

    # 2. Sincronizar productos declarados en monitored_urls.json
    from src.database import get_db_session
    from src.services.price_service import PriceTrackingService
    async with get_db_session() as session:
        service = PriceTrackingService()
        await service.sync_monitored_urls_file(session)

    # 3. Instanciar scheduler
    scheduler = PriceTrackerScheduler(price_service=service)
    scheduler.start()

    # 4. Lanzar verificación inicial de precios y categorías en segundo plano
    asyncio.create_task(scheduler.run_check_cycle())
    asyncio.create_task(scheduler.run_category_sync_cycle())

    stop_event = asyncio.Event()

    def handle_signal(sig, frame):
        logger.info(f"Señal de interrupción recibida ({sig}). Iniciando apagado ordenado...")
        stop_event.set()

    # Manejo de señales para Windows y Unix
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_signal)
        except Exception:
            pass

    # 4. Iniciar servidor HTTP de healthcheck (para Render, Koyeb, etc.)
    health_runner = None
    if settings.ENABLE_HEALTHCHECK:
        try:
            from src.health import start_health_server
            health_runner = await start_health_server(settings.PORT)
        except Exception as ex:
            logger.warning(f"No se pudo iniciar el servidor de healthcheck: {ex}")

    # 5. Iniciar Telegram Polling si el token fue provisto
    polling_task = None
    if settings.TELEGRAM_BOT_TOKEN:
        try:
            polling_task = asyncio.create_task(
                dp.start_polling(
                    notifier.bot,
                    allowed_updates=["message", "callback_query", "chat_join_request"],
                )
            )
            logger.info("Telegram Polling activado (mensajes, callbacks y solicitudes de unión).")
        except Exception as ex:
            logger.warning(f"No se pudo iniciar polling de Telegram: {ex}")
    else:
        logger.warning(
            "TELEGRAM_BOT_TOKEN no configurado en .env. El bot operará sin notificaciones directas a Telegram."
        )

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Interrupción por teclado o cancelación recibida.")
    finally:
        logger.info("Cerrando servicios y liberando recursos...")
        scheduler.shutdown()

        if health_runner:
            try:
                await health_runner.cleanup()
                logger.info("Servidor HTTP de healthcheck detenido.")
            except Exception as ex:
                logger.warning(f"Error cerrando healthcheck: {ex}")

        if polling_task and not polling_task.done():
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass

        await notifier.close()
        await close_db()
        logger.info("Sistema detenido correctamente. ¡Hasta pronto!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
