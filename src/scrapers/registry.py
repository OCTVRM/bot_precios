import json
import logging
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from src.scrapers.amazon import AmazonScraper
from src.scrapers.base import BaseScraper, ScrapedItem
from src.scrapers.configurable import ConfigurableScraper
from src.scrapers.mercadolibre import MercadoLibreScraper
from src.scrapers.store_config import find_store_rule_for_url

logger = logging.getLogger(__name__)


class GenericScraper(BaseScraper):
    """Scraper genérico de respaldo basado en estándares web (OpenGraph, Schema.org JSON-LD)."""

    def __init__(self):
        super().__init__(store_name="Tienda Genérica")

    def build_affiliate_url(self, url: str) -> str:
        return url

    async def scrape(self, url: str) -> ScrapedItem:
        html = await self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        title = None
        price = None
        in_stock = True
        image_url = None

        # 1. Buscar en JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "Product":
                        title = item.get("name")
                        offers = item.get("offers", {})
                        if isinstance(offers, dict) and "price" in offers:
                            price = float(offers["price"])
                        elif isinstance(offers, list) and len(offers) > 0 and "price" in offers[0]:
                            price = float(offers[0]["price"])
                        break
            except Exception:
                continue

        # 2. Buscar en OpenGraph / Meta tags
        if not title:
            og_title = soup.find("meta", property="og:title")
            title = og_title["content"] if og_title and og_title.get("content") else (soup.title.string if soup.title else "Producto")

        if price is None:
            for meta_prop in ["product:price:amount", "price", "og:price:amount"]:
                meta_elem = soup.find("meta", property=meta_prop) or soup.find("meta", attrs={"name": meta_prop})
                if meta_elem and meta_elem.get("content"):
                    price = self.clean_price(meta_elem["content"])
                    break

        if not image_url:
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                image_url = og_img["content"]

        if price is None:
            raise ValueError(f"No fue posible determinar el precio en la URL genérica: {url}")

        return ScrapedItem(
            title=title.strip() if title else "Producto sin título",
            price=price,
            in_stock=in_stock,
            image_url=image_url,
            affiliate_url=url,
        )


def get_scraper_for_url(url: str) -> BaseScraper:
    """
    Identifica y devuelve la instancia del scraper adecuado.
    Prioriza las tiendas configuradas en stores.json (Falabella, Paris, Ripley, etc.)
    y usa scrapers especializados para Amazon y Mercado Libre.
    """
    domain = urlparse(url).netloc.lower()

    # Manejo especializado de Amazon y Mercado Libre
    if "amazon." in domain:
        return AmazonScraper()
    if "mercadolibre." in domain or "articulo.mercadolibre." in domain:
        return MercadoLibreScraper()

    # Búsqueda en catálogo declarativo stores.json
    store_rule = find_store_rule_for_url(url)
    if store_rule:
        logger.debug(f"Usando scraper configurable para '{store_rule.name}' ({domain})")
        return ConfigurableScraper(store_rule)

    logger.info(f"Dominio no reconocido '{domain}', utilizando scraper genérico.")
    return GenericScraper()
