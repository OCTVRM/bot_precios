import html
import logging
from dataclasses import dataclass
from typing import Optional
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from src.config import settings

logger = logging.getLogger(__name__)


def format_clp(amount: float) -> str:
    """Formatea un monto numérico al estándar de Pesos Chilenos (CLP): $499.990 (sin decimales, con punto de miles)."""
    return f"${int(round(amount)):,}".replace(",", ".")


@dataclass
class AlertPayload:
    """Información completa para despachar una alerta a Telegram."""
    title: str
    store: str
    old_price: float
    new_price: float
    discount_percent: float
    affiliate_url: str
    is_all_time_low: bool = False
    target_price: Optional[float] = None
    image_url: Optional[str] = None
    category: Optional[str] = None
    is_price_error: bool = False


class TelegramNotifier:
    """Servicio de mensajería y emisión de alertas hacia canales/grupos de Telegram."""

    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or settings.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        self._bot: Optional[Bot] = None

    @property
    def bot(self) -> Bot:
        if self._bot is None:
            if not self.token:
                raise ValueError("TELEGRAM_BOT_TOKEN no está configurado.")
            self._bot = Bot(token=self.token)
        return self._bot

    def format_message(self, alert: AlertPayload) -> str:
        """Construye el texto del mensaje en HTML con diseño visual limpio y formato oficial CLP."""
        safe_title = html.escape(alert.title)
        safe_store = html.escape(alert.store.upper())
        savings = alert.old_price - alert.new_price

        # Encabezado visual: Distinción entre Error de Precio (Bug) y Oferta Estándar
        if alert.is_price_error or alert.discount_percent >= settings.ERROR_DISCOUNT_THRESHOLD_PERCENT:
            header = (
                f"🚨🚨 <b>¡POSIBLE ERROR DE PRECIO / BUG EN {safe_store}!</b> 🚨🚨\n"
                f"⚡ <i>¡Descuento anómalo del {alert.discount_percent:.1f}%! Revisa y compra de inmediato antes de corrección.</i>\n"
            )
        else:
            header = f"🔥 <b>¡OFERTA DETECTADA EN {safe_store}!</b> 🔥\n"

        lines = [
            header,
            f"📦 <b>{safe_title}</b>",
        ]

        if alert.category:
            lines.append(f"🏷️ <b>Categoría:</b> {html.escape(alert.category)}")

        lines.extend([
            f"📉 <b>Precio Anterior:</b> <s>{format_clp(alert.old_price)}</s>",
            f"💥 <b>Precio Oferta:</b> <b>{format_clp(alert.new_price)}</b>",
            f"💰 <b>Ahorro:</b> {format_clp(savings)} (<b>-{alert.discount_percent:.1f}%</b>)",
        ])

        if alert.is_all_time_low:
            lines.append("🏆 <b>¡MÍNIMO HISTÓRICO REGISTRADO!</b> 🏆")

        if alert.target_price and alert.new_price <= alert.target_price:
            lines.append(f"🎯 <i>¡Alcanzó tu precio objetivo de {format_clp(alert.target_price)}!</i>")

        lines.append("\n⚡ <i>Aprovecha antes de que se agote o cambie el precio.</i>")

        return "\n".join(lines)

    def build_keyboard(self, alert: AlertPayload) -> InlineKeyboardMarkup:
        """Crea el botón interactivo con el enlace de afiliado."""
        button_text = f"🛒 Ver Oferta en {alert.store}"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=button_text, url=alert.affiliate_url)]
            ]
        )
        return keyboard

    async def send_alert(self, alert: AlertPayload) -> bool:
        """
        Envía la alerta al canal de Telegram configurado.
        Si hay imagen disponible intenta enviarla como foto con pie de página;
        si falla o no hay imagen, envía el mensaje en formato de texto.
        """
        if not self.token or not self.chat_id:
            logger.warning(
                "Notificación omitida: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados."
            )
            return False

        caption = self.format_message(alert)
        keyboard = self.build_keyboard(alert)

        try:
            if alert.image_url:
                try:
                    await self.bot.send_photo(
                        chat_id=self.chat_id,
                        photo=alert.image_url,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard,
                    )
                    logger.info(f"Alerta con foto enviada exitosamente para: '{alert.title}'")
                    return True
                except Exception as photo_err:
                    logger.warning(
                        f"Fallo al enviar foto ({photo_err}), reintentando como mensaje de texto..."
                    )

            # Envío de texto estándar
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=False,
            )
            logger.info(f"Alerta de texto enviada exitosamente para: '{alert.title}'")
            return True

        except Exception as ex:
            logger.error(f"Error al enviar mensaje a Telegram: {ex}", exc_info=True)
            return False

    async def close(self) -> None:
        """Cierra la sesión del bot de Telegram."""
        if self._bot:
            await self._bot.session.close()
            self._bot = None
