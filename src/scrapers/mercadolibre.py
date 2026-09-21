import json
import logging
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from bs4 import BeautifulSoup
from src.config import settings
from src.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class MercadoLibreScraper(BaseScraper):
    """Scraper especializado para Mercado Libre (soporta dominios de toda Latinoamérica)."""

    def __init__(self):
        super().__init__(store_name="Mercado Libre")

    def build_affiliate_url(self, url: str) -> str:
        """Inyecta el identificador de afiliado/tracking en la URL de Mercado Libre."""
        if not settings.MERCADOLIBRE_AFFILIATE_TAG:
            return url

        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        # Parámetro estándar de tracking para afiliados de Mercado Libre
        query_params["matt_tool"] = [settings.MERCADOLIBRE_AFFILIATE_TAG]

        new_query = urlencode(query_params, doseq=True)
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        ))

    async def scrape(self, url: str) -> ScrapedItem:
        """Extrae precio, título y stock utilizando microdatos JSON-LD y selectores CSS."""
        html = await self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        title = None
        price = None
        in_stock = True
        image_url = None

        # 1. Estrategia Primaria: JSON-LD (Schema.org / Product)
        # Mercado Libre incluye bloques JSON-LD con alta confiabilidad
        json_ld_scripts = soup.find_all("script", type="application/ld+json")
        for script in json_ld_scripts:
            try:
                data = json.loads(script.string or "")
                # Puede ser un solo objeto o una lista
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "Product":
                        title = item.get("name", title)
                        offers = item.get("offers", {})
                        if isinstance(offers, dict) and "price" in offers:
                            price = float(offers["price"])
                            if offers.get("availability") == "https://schema.org/OutOfStock":
                                in_stock = False
                        if "image" in item:
                            img = item["image"]
                            image_url = img if isinstance(img, str) else (img[0] if isinstance(img, list) else None)
                        break
            except Exception as ex:
                logger.debug(f"Error parseando script JSON-LD en Mercado Libre: {ex}")

        # 2. Estrategia Secundaria: Selectores CSS si JSON-LD no proveyó precio o título
        if not title:
            title_elem = soup.select_one("h1.ui-pdp-title, .ui-pdp-header__title, h1")
            if title_elem:
                title = title_elem.get_text(strip=True)
            else:
                meta_title = soup.find("meta", property="og:title")
                title = meta_title["content"].strip() if meta_title else "Producto Mercado Libre"

        if price is None:
            # Buscar contenedor de precio actual (la segunda línea suele ser el precio con descuento si existe)
            price_container = soup.select_one(".ui-pdp-price__second-line, .ui-pdp-price")
            fraction_elem = (
                price_container.select_one(".andes-money-amount__fraction")
                if price_container
                else soup.select_one(".andes-money-amount__fraction")
            )
            cents_elem = (
                price_container.select_one(".andes-money-amount__cents")
                if price_container
                else soup.select_one(".andes-money-amount__cents")
            )

            if fraction_elem:
                frac = fraction_elem.get_text(strip=True)
                if cents_elem and cents_elem.get_text(strip=True):
                    cents = cents_elem.get_text(strip=True)
                    price = self.clean_price(f"{frac},{cents}")
                else:
                    price = self.clean_price(frac)
            else:
                # Fallback a meta tags
                meta_price = soup.find("meta", itemprop="price") or soup.find("meta", property="product:price:amount")
                if meta_price and meta_price.get("content"):
                    price = self.clean_price(meta_price["content"])

        if price is None:
            raise ValueError(f"No se pudo extraer el precio del producto en Mercado Libre: {url}")

        # 3. Estado de la publicación (Stock o Pausada)
        paused_banner = soup.select_one(".ui-pdp-promotions-pill-label, .ui-pdp-paused-warning")
        if paused_banner and any(w in paused_banner.get_text(strip=True).lower() for w in ["pausada", "finalizada"]):
            in_stock = False

        if not image_url:
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                image_url = og_img["content"]

        affiliate_url = self.build_affiliate_url(url)

        return ScrapedItem(
            title=title,
            price=price,
            in_stock=in_stock,
            image_url=image_url,
            affiliate_url=affiliate_url,
        )
