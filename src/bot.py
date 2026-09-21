import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from sqlalchemy import func, select
from src.config import settings
from src.database import get_db_session
from src.models import Product
from src.services.notifier import TelegramNotifier, AlertPayload

logger = logging.getLogger(__name__)

# Instancia global del despachador
dp = Dispatcher()
notifier = TelegramNotifier()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Mensaje de bienvenida y estado del bot."""
    welcome_text = (
        "🤖 <b>Bot Rastreador de Precios y Ofertas</b>\n\n"
        "Este bot monitorea automáticamente caídas de precio en tiendas online "
        "y publica ofertas con enlaces de afiliado en el canal oficial.\n\n"
        "<b>Comandos disponibles:</b>\n"
        "/status - Consulta el estado del sistema y productos en seguimiento\n"
        "/help - Instrucciones y ayuda"
    )
    await message.answer(welcome_text, parse_mode=ParseMode.HTML)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Instrucciones de uso."""
    help_text = (
        "📖 <b>Guía de Administración</b>\n\n"
        "Para agregar y gestionar productos usa la interfaz CLI en el servidor:\n"
        "<code>python -m src.cli add --url &lt;URL&gt; --threshold 15</code>\n"
        "<code>python -m src.cli list</code>\n"
        "<code>python -m src.cli check</code>\n\n"
        f"Frecuencia de escaneo: Cada {settings.CHECK_INTERVAL_MINUTES} minutos.\n"
        f"Cooldown anti-spam: {settings.ALERT_COOLDOWN_HOURS} horas."
    )
    await message.answer(help_text, parse_mode=ParseMode.HTML)


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Muestra el total de productos activos e historial en base de datos."""
    async with get_db_session() as session:
        count_stmt = select(func.count(Product.id)).where(Product.activo.is_(True))
        total_active = (await session.execute(count_stmt)).scalar() or 0

        total_stmt = select(func.count(Product.id))
        total_products = (await session.execute(total_stmt)).scalar() or 0

    status_msg = (
        "📊 <b>Estado del Sistema de Rastreo</b>\n\n"
        f"🟢 <b>Productos Activos:</b> {total_active}\n"
        f"📦 <b>Total en Catálogo:</b> {total_products}\n"
        f"⏱️ <b>Intervalo:</b> {settings.CHECK_INTERVAL_MINUTES} min\n"
        f"🛡️ <b>Anti-Spam:</b> {settings.ALERT_COOLDOWN_HOURS}h\n"
        f"📢 <b>Canal Destino:</b> <code>{settings.TELEGRAM_CHAT_ID or 'No configurado'}</code>"
    )
    await message.answer(status_msg, parse_mode=ParseMode.HTML)


async def start_bot_polling(bot: Bot) -> None:
    """Inicia el consumo de comandos de Telegram en segundo plano."""
    try:
        logger.info("Iniciando polling de comandos de Telegram...")
        await dp.start_polling(bot)
    except Exception as ex:
        logger.error(f"Error en polling de Telegram: {ex}")
