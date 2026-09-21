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

        # 1. Buscar en JSON-LD (validando pertenencia a la URL actual)
        target_path = urlparse(url).path.rstrip("/")
        found_product = False
        for script in soup.find_all("script", type="application/ld+json"):
            if found_product:
                break
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "Product":
                        # Validar que no sea un producto relacionado/carrusel de otra URL
                        item_url = None
                        main_entity = item.get("mainEntityOfPage")
                        if isinstance(main_entity, dict):
                            item_url = main_entity.get("@id") or main_entity.get("url")
                        elif isinstance(main_entity, str):
                            item_url = main_entity
                        elif "url" in item:
                            item_url = item.get("url")

                        if item_url:
                            item_path = urlparse(item_url).path.rstrip("/")
                            if target_path and item_path and target_path != item_path:
                                continue

                        title = item.get("name")
                        offers = item.get("offers", {})
                        if isinstance(offers, dict) and "price" in offers:
                            price = float(offers["price"])
                            if offers.get("availability") == "https://schema.org/OutOfStock":
                                in_stock = False
                        elif isinstance(offers, list) and len(offers) > 0 and "price" in offers[0]:
                            price = float(offers[0]["price"])
                        if "image" in item:
                            img = item["image"]
                            image_url = img if isinstance(img, str) else (img[0] if isinstance(img, list) else None)
                        found_product = True
                        break
            except Exception:
                continue

        # 2. Buscar en OpenGraph / Meta tags
        if not title:
            og_title = soup.find("meta", property="og:title")
            h1 = soup.find("h1")
            title = og_title["content"] if og_title and og_title.get("content") else (
                h1.get_text(strip=True) if h1 else (soup.title.string if soup.title else "Producto")
            )

        if price is None:
            for meta_prop in ["product:price:amount", "price", "og:price:amount", "twitter:data1"]:
                meta_elem = soup.find("meta", property=meta_prop) or soup.find("meta", attrs={"name": meta_prop})
                if meta_elem and meta_elem.get("content"):
                    try:
                        price = self.clean_price(meta_elem["content"])
                        break
                    except Exception:
                        continue

        # 3. Fallback a selectores estándar de tiendas (incluyendo Tiendanube / WooCommerce / Shopify)
        if price is None:
            for css_sel in [
                ".js-price-display",
                ".price-item--sale",
                ".price-item",
                ".product-price",
                ".price",
                ".woocommerce-Price-amount",
                ".amount",
            ]:
                elem = soup.select_one(css_sel)
                if elem and elem.get_text(strip=True):
                    try:
                        price = self.clean_price(elem.get_text(strip=True))
                        break
                    except Exception:
                        continue

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
