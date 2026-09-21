import json
import logging
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from bs4 import BeautifulSoup
from src.scrapers.base import BaseScraper, ScrapedItem
from src.scrapers.store_config import StoreRule

logger = logging.getLogger(__name__)


class ConfigurableScraper(BaseScraper):
    """
    Scraper flexible que extrae datos a partir de las reglas declaradas en stores.json.
    Soporta JSON-LD (Schema.org), Next.js (__NEXT_DATA__), selectores CSS y metadatos OpenGraph.
    """

    def __init__(self, rule: StoreRule):
        super().__init__(store_name=rule.name)
        self.rule = rule

    def build_affiliate_url(self, url: str) -> str:
        """Inyecta el parámetro de afiliado configurado en la regla de la tienda."""
        param_name = self.rule.affiliate_param
        param_value = self.rule.affiliate_tag

        if not param_name or not param_value:
            return url

        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        query_params[param_name] = [param_value]
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
        """Ejecuta la extracción de datos siguiendo una estrategia multinivel."""
        html = await self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        title = None
        price = None
        in_stock = True
        image_url = None

        # Estrategia 1: Schema.org JSON-LD si la tienda lo tiene habilitado
        if self.rule.use_json_ld:
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if item.get("@type") == "Product":
                            title = item.get("name", title)
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
                            break
                except Exception as ex:
                    logger.debug(f"[{self.rule.name}] Error leyendo JSON-LD: {ex}")

        # Estrategia 2: Extracción en páginas Next.js (__NEXT_DATA__)
        if price is None or not title:
            next_data_script = soup.find("script", id="__NEXT_DATA__", type="application/json")
            if next_data_script and next_data_script.string:
                try:
                    nd = json.loads(next_data_script.string)
                    page_props = nd.get("props", {}).get("pageProps", {})
                    # Buscar recursivamente o en propiedades comunes de producto
                    prod_data = (
                        page_props.get("product")
                        or page_props.get("productData")
                        or page_props.get("initialData", {}).get("product")
                    )
                    if isinstance(prod_data, dict):
                        if not title:
                            title = prod_data.get("name") or prod_data.get("displayName") or prod_data.get("title")
                        if price is None:
                            # Precios pueden venir en prices, price, o priceSpecification
                            prices_list = prod_data.get("prices") or []
                            if isinstance(prices_list, list) and len(prices_list) > 0:
                                price_val = prices_list[0].get("price") or prices_list[0].get("originalPrice")
                                if price_val:
                                    price = self.clean_price(str(price_val))
                            elif "price" in prod_data:
                                price = self.clean_price(str(prod_data["price"]))
                except Exception as ex:
                    logger.debug(f"[{self.rule.name}] Error leyendo __NEXT_DATA__: {ex}")

        # Estrategia 3: Selectores CSS configurados en stores.json
        if not title:
            for sel in self.rule.selectors.title:
                elem = soup.select_one(sel)
                if elem and elem.get_text(strip=True):
                    title = elem.get_text(strip=True)
                    break

        if price is None:
            for sel in self.rule.selectors.price:
                elem = soup.select_one(sel)
                if elem and elem.get_text(strip=True):
                    try:
                        price = self.clean_price(elem.get_text(strip=True))
                        break
                    except ValueError:
                        continue

        # Estrategia 4: Metadatos OpenGraph / Meta tags como respaldo final
        if not title:
            og_title = soup.find("meta", property="og:title")
            title = og_title["content"].strip() if og_title and og_title.get("content") else (soup.title.string if soup.title else None)

        if price is None:
            for meta_name in ["product:price:amount", "price", "og:price:amount"]:
                meta = soup.find("meta", property=meta_name) or soup.find("meta", attrs={"name": meta_name})
                if meta and meta.get("content"):
                    price = self.clean_price(meta["content"])
                    break

        if not image_url:
            for sel in self.rule.selectors.image:
                elem = soup.select_one(sel)
                if elem:
                    image_url = elem.get("src") or elem.get("content")
                    if image_url:
                        break

        if not image_url:
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                image_url = og_img["content"]

        if price is None:
            raise ValueError(f"[{self.rule.name}] No fue posible extraer el precio en la URL: {url}")

        affiliate_url = self.build_affiliate_url(url)

        return ScrapedItem(
            title=title.strip() if title else f"Producto {self.rule.name}",
            price=price,
            in_stock=in_stock,
            image_url=image_url,
            affiliate_url=affiliate_url,
        )
