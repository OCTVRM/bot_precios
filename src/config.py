import logging
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]


class Settings(BaseSettings):
    """Configuración global de la aplicación cargada desde variables de entorno y archivo .env."""

    # Base de datos (Compatible con SQLite y Supabase/PostgreSQL)
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./bot_precios.db",
        description="URL de conexión SQLAlchemy asíncrona (ej: sqlite+aiosqlite:///... o postgresql+asyncpg://...)",
    )

    # Telegram
    TELEGRAM_BOT_TOKEN: str = Field(
        default="",
        description="Token de la API de Telegram proporcionado por @BotFather",
    )
    TELEGRAM_CHAT_ID: str = Field(
        default="",
        description="ID del canal o grupo de Telegram donde se emitirán las alertas",
    )
    TELEGRAM_ADMIN_ID: Optional[int] = Field(
        default=None,
        description="ID numérico de Telegram del administrador para aprobar comprobantes de pago",
    )
    SUBSCRIPTION_PRICE_CLP: int = Field(
        default=7990,
        description="Precio mensual de la suscripción VIP en pesos chilenos (CLP)",
    )
    BANK_TRANSFER_DETAILS: str = Field(
        default=(
            "🏦 <b>Datos de Transferencia Bancaria:</b>\n"
            "• <b>Banco:</b> BancoEstado / Cualquier Banco\n"
            "• <b>Tipo de Cuenta:</b> Cuenta RUT / Vista / Corriente\n"
            "• <b>Monto:</b> $7.990 CLP\n"
            "• <b>Asunto:</b> Canal VIP Ofertas\n\n"
            "📸 <i>Una vez realizada la transferencia, envía la foto o captura del comprobante directamente a este chat.</i>"
        ),
        description="Instrucciones con los datos de cuenta bancaria que el bot envía al usuario para el pago",
    )

    # Scheduler & Detección
    CHECK_INTERVAL_MINUTES: int = Field(
        default=30,
        description="Frecuencia en minutos para verificar los precios de los productos",
    )
    CATEGORY_SYNC_INTERVAL_HOURS: int = Field(
        default=12,
        description="Frecuencia en horas para redescubrir y sincronizar el catálogo de categorías",
    )
    CATEGORY_PRODUCTS_LIMIT: int = Field(
        default=50,
        description="Cantidad máxima de productos a extraer por tienda en cada categoría",
    )
    MAJOR_STORES_PRODUCTS_LIMIT: int = Field(
        default=80,
        description="Cantidad máxima de productos para macro-tiendas con catálogos masivos (Sodimac, Falabella, Paris, Ripley, Mercado Libre)",
    )
    ALERT_COOLDOWN_HOURS: int = Field(
        default=12,
        description="Horas mínimas antes de enviar otra alerta del mismo producto para evitar saturación",
    )
    DEFAULT_DISCOUNT_THRESHOLD_PERCENT: float = Field(
        default=15.0,
        description="Porcentaje mínimo de reducción de precio para disparar alerta de oferta",
    )
    ERROR_DISCOUNT_THRESHOLD_PERCENT: float = Field(
        default=50.0,
        description="Porcentaje mínimo de reducción de precio para alertar como posible error o bug de precio",
    )
    MAX_CONCURRENT_SCRAPES: int = Field(
        default=3,
        description="Concurrencia máxima para tareas de scraping simultáneas",
    )
    REQUEST_TIMEOUT: int = Field(
        default=20,
        description="Tiempo de espera máximo en segundos para peticiones HTTP",
    )
    PORT: int = Field(
        default=10000,
        description="Puerto HTTP para el servidor de healthcheck en plataformas cloud (Render, Koyeb, etc.)",
    )
    ENABLE_HEALTHCHECK: bool = Field(
        default=True,
        description="Habilitar servidor web HTTP mínimo para healthcheck y pings anti-suspensión",
    )

    # Afiliados
    AMAZON_AFFILIATE_TAG: Optional[str] = Field(
        default=None,
        description="ID/Tag de afiliado para enlaces de Amazon (ej: 'mitag-21')",
    )
    MERCADOLIBRE_AFFILIATE_TAG: Optional[str] = Field(
        default=None,
        description="Parámetro o identificador de afiliado de Mercado Libre",
    )

    # User-Agents
    USER_AGENTS: List[str] = Field(
        default=DEFAULT_USER_AGENTS,
        description="Lista de User-Agents para rotación en peticiones HTTP",
    )

    # Logging
    LOG_LEVEL: str = Field(default="INFO", description="Nivel de log (DEBUG, INFO, WARNING, ERROR)")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def setup_logging(level: str = "INFO") -> None:
    """Configura el formateo de logs estructurados para el proyecto."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# Instancia singleton de configuración
settings = Settings()
setup_logging(settings.LOG_LEVEL)
