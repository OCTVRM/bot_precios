import logging
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from bs4 import BeautifulSoup
from src.config import settings
from src.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class AmazonScraper(BaseScraper):
    """Scraper especializado para Amazon (soporta dominios .es, .com, .com.mx, etc.)."""

    def __init__(self):
        super().__init__(store_name="Amazon")

    @staticmethod
    def get_canonical_url(url: str) -> str:
        """Normaliza cualquier URL de Amazon a su formato canónico limpio /dp/{ASIN} con moneda CLP."""
        match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        parsed = urlparse(url)
        netloc = parsed.netloc.lower() if parsed.netloc else "www.amazon.com"
        if match:
            asin = match.group(1)
            return f"https://{netloc}/dp/{asin}?currency=CLP&language=es_US"

        # Si no tiene ASIN estándar, al menos inyectar currency=CLP
        query_params = parse_qs(parsed.query)
        if "currency" not in query_params:
            query_params["currency"] = ["CLP"]
        if "language" not in query_params:
            query_params["language"] = ["es_US"]
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query_params, doseq=True),
            parsed.fragment,
        ))

    def build_affiliate_url(self, url: str) -> str:
        """Inyecta el tag de asociado de Amazon en la URL canónica."""
        canonical = self.get_canonical_url(url)
        if not settings.AMAZON_AFFILIATE_TAG:
            return canonical

        parsed = urlparse(canonical)
        query_params = parse_qs(parsed.query)
        query_params["tag"] = [settings.AMAZON_AFFILIATE_TAG]
        query_params["currency"] = ["CLP"]
        query_params["language"] = ["es_US"]

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
        """Extrae el precio, título y disponibilidad de un producto en Amazon."""
        target_url = self.get_canonical_url(url)
        html = await self.fetch_html(target_url)
        soup = BeautifulSoup(html, "html.parser")

        # Detección de CAPTCHA o bloqueo de Amazon
        title_tag = soup.find("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
        if "Robot Check" in page_title or "Amazon CAPTCHA" in page_title:
            logger.warning("Amazon presentó un CAPTCHA/Robot Check. Intentando con scraping dinámico...")
            html = await self.fetch_html_dynamic(target_url)
            soup = BeautifulSoup(html, "html.parser")

        # 1. Extracción de Título
        title = None
        for selector in ["#productTitle", "#title", "h1.a-size-large"]:
            elem = soup.select_one(selector)
            if elem and elem.get_text(strip=True):
                title = elem.get_text(strip=True)
                break

        if not title:
            # Fallback a OpenGraph o Twitter Card
            meta_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "twitter:title"})
            if meta_title and meta_title.get("content"):
                title = meta_title["content"].strip()
            else:
                title = page_title.replace("Amazon.com: ", "").replace("Amazon.es: ", "").strip()

        # 2. Extracción de Precio
        price_raw = None
        price_selectors = [
            "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
            "#corePrice_feature_div .a-price .a-offscreen",
            ".apexPriceToPay .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "#priceblock_saleprice",
            "#price_inside_buybox",
            ".a-price.priceToPay .a-offscreen",
            "span.a-price span.a-offscreen",
        ]

        for selector in price_selectors:
            price_elem = soup.select_one(selector)
            if price_elem and price_elem.get_text(strip=True):
                price_raw = price_elem.get_text(strip=True)
                break

        # Fallback a enteros y decimales separados (.a-price-whole y .a-price-fraction)
        if not price_raw:
            whole = soup.select_one("span.a-price-whole")
            fraction = soup.select_one("span.a-price-fraction")
            if whole:
                frac_text = fraction.get_text(strip=True) if fraction else "00"
                price_raw = f"{whole.get_text(strip=True)}.{frac_text}"

        if not price_raw:
            raise ValueError(f"No se pudo localizar el precio en la página de Amazon: {url}")

        price = self.clean_price(price_raw)

        # Conversión automática USD -> CLP si el servidor recibe el precio en dólares
        # En Chile ningún producto de las categorías monitoreadas cuesta < $500 pesos.
        raw_lower = price_raw.lower()
        if "usd" in raw_lower or "us$" in raw_lower or price < 500.0:
            usd_val = price
            price = float(round(price * 960.0))
            logger.info(
                f"[Amazon] Detectado precio en USD ({usd_val}). Convertido a CLP: ${price:,.0f} CLP"
            )

        # 3. Disponibilidad / Stock
        in_stock = True
        avail_elem = soup.select_one("#availability")
        if avail_elem:
            avail_text = avail_elem.get_text(strip=True).lower()
            if any(term in avail_text for term in ["no disponible", "currently unavailable", "agotado", "out of stock"]):
                in_stock = False

        # 4. Imagen principal (opcional para enriquecer Telegram)
        image_url = None
        img_elem = soup.select_one("#landingImage, #imgBlkFront, #main-image")
        if img_elem and img_elem.get("src"):
            image_url = img_elem["src"]

        affiliate_url = self.build_affiliate_url(url)

        return ScrapedItem(
            title=title,
            price=price,
            in_stock=in_stock,
            image_url=image_url,
            affiliate_url=affiliate_url,
        )
