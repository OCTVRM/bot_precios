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

    def build_affiliate_url(self, url: str) -> str:
        """Inyecta el tag de asociado de Amazon en la URL."""
        if not settings.AMAZON_AFFILIATE_TAG:
            return url

        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        # Inyectar o sobrescribir el tag de afiliado
        query_params["tag"] = [settings.AMAZON_AFFILIATE_TAG]
        
        # Eliminar parámetros efímeros o de sesión que puedan romper el rastreo
        for drop_key in ["ref_", "ref", "pd_rd_r", "pd_rd_w", "pd_rd_wg", "pf_rd_r", "pf_rd_p"]:
            query_params.pop(drop_key, None)

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
        html = await self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        # Detección de CAPTCHA o bloqueo de Amazon
        title_tag = soup.find("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
        if "Robot Check" in page_title or "Amazon CAPTCHA" in page_title:
            logger.warning("Amazon presentó un CAPTCHA/Robot Check. Intentando con scraping dinámico...")
            html = await self.fetch_html_dynamic(url)
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
