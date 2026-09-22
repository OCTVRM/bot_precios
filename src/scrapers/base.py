import abc
import logging
import random
import re
from dataclasses import dataclass
from typing import Dict, Optional
import httpx
from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ScrapedItem:
    """Estructura de datos normalizada para el resultado de un scraper."""
    title: str
    price: float
    normal_price: Optional[float] = None
    discount_percent: Optional[float] = None
    currency: str = "CLP"
    in_stock: bool = True
    image_url: Optional[str] = None
    affiliate_url: Optional[str] = None


class BaseScraper(abc.ABC):
    """Clase base abstracta para scrapers de tiendas."""

    def __init__(self, store_name: str):
        self.store_name = store_name

    def get_headers(self) -> Dict[str, str]:
        """Genera cabeceras HTTP realistas rotando User-Agent y Sec-Ch-Ua."""
        ua = random.choice(settings.USER_AGENTS)
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        return headers

    async def fetch_html(self, url: str, use_dynamic: bool = False) -> str:
        """
        Descarga el HTML de la página objetivo vía HTTPx.
        Si use_dynamic es True o la página requiere renderizado JS,
        prepara la invocación con Playwright.
        """
        if use_dynamic:
            return await self.fetch_html_dynamic(url)

        headers = self.get_headers()
        async with httpx.AsyncClient(
            headers=headers,
            timeout=settings.REQUEST_TIMEOUT,
            follow_redirects=True,
            verify=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    async def fetch_html_dynamic(self, url: str) -> str:
        """
        Método de respaldo para páginas dinámicas con Playwright.
        Requiere tener instalado playwright ('pip install playwright && playwright install chromium').
        """
        try:
            from playwright.async_api import async_playwright
            logger.info(f"[{self.store_name}] Usando Playwright para renderizado dinámico: {url}")
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent=random.choice(settings.USER_AGENTS))
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=settings.REQUEST_TIMEOUT * 1000)
                content = await page.content()
                await browser.close()
                return content
        except ImportError:
            logger.warning(
                "Playwright no está instalado. Para activar scraping dinámico ejecute: "
                "'pip install playwright && playwright install chromium'. Recurriendo a HTTPx..."
            )
            return await self.fetch_html(url, use_dynamic=False)

    @staticmethod
    def clean_price(price_raw: str) -> float:
        """
        Limpia y normaliza cadenas de precios heterogéneas a un valor float preciso.
        
        Soporta:
        - "$ 1.299,99" -> 1299.99 (Formato hispano/europeo con punto de miles y coma decimal)
        - "$ 1,299.99" -> 1299.99 (Formato anglosajón con coma de miles y punto decimal)
        - "$ 12.500"   -> 12500.0 (Monedas de miles enteros como ARS/CLP/COP)
        - "49,90 €"    -> 49.90
        - "1200"       -> 1200.0
        """
        if not price_raw:
            raise ValueError("La cadena de precio no puede estar vacía")

        # Limpiar espacios en blanco especiales (\xa0, saltos de línea)
        cleaned = price_raw.replace("\xa0", " ").strip()

        # Extraer solo caracteres válidos para formato de precio: dígitos, puntos y comas
        # Primero quitamos símbolos de moneda o texto
        match = re.search(r"[\d.,]+", cleaned)
        if not match:
            raise ValueError(f"No se encontraron números válidos en el precio: '{price_raw}'")

        number_str = match.group(0).strip()

        # Si no tiene ni punto ni coma, es un entero simple
        if "." not in number_str and "," not in number_str:
            return float(number_str)

        # Si tiene ambos (ej: 1.299,99 o 1,299.99)
        if "." in number_str and "," in number_str:
            last_dot = number_str.rfind(".")
            last_comma = number_str.rfind(",")
            if last_comma > last_dot:
                # Coma es el decimal (1.299,99 -> 1299.99)
                number_str = number_str.replace(".", "").replace(",", ".")
            else:
                # Punto es el decimal (1,299.99 -> 1299.99)
                number_str = number_str.replace(",", "")
            return float(number_str)

        # Si tiene solo punto o solo coma
        # Ejemplos: "12,50", "12.50", "12.500", "12,500"
        separator = "." if "." in number_str else ","
        parts = number_str.split(separator)

        # Si hay múltiples separadores (ej: 1.250.000 o 1,250,000), todos son miles
        if len(parts) > 2:
            return float("".join(parts))

        # Hay exactamente un separador: parts[0] y parts[1]
        integer_part, fraction_part = parts[0], parts[1]
        # Si la parte fraccionaria tiene exactamente 3 dígitos y el entero >= 1, es probable separador de miles
        # (ej: 12.500 o 150.000 en CLP/COP/ARS sin centavos)
        if len(fraction_part) == 3 and len(integer_part) >= 1:
            return float(integer_part + fraction_part)
        else:
            # 1 o 2 dígitos (o 4+ decimales): es decimal
            return float(f"{integer_part}.{fraction_part}")

    @abc.abstractmethod
    def build_affiliate_url(self, url: str) -> str:
        """Enriquece la URL original con los parámetros de afiliado configurados."""
        pass

    @abc.abstractmethod
    async def scrape(self, url: str) -> ScrapedItem:
        """Ejecuta la extracción y normalización de datos del producto."""
        pass
