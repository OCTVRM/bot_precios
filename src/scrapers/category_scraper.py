import asyncio
import datetime
import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models import Product
from src.scrapers.base import BaseScraper
from src.scrapers.category_config import CategoryItem, load_categories_catalog
from src.scrapers.store_config import find_store_rule_by_id, find_store_rule_for_url

logger = logging.getLogger(__name__)


@dataclass
class ScrapedCategoryProduct:
    """Producto descubierto en un listado de categoría / más vendidos."""
    url: str
    title: str
    price: Optional[float] = None
    normal_price: Optional[float] = None
    discount_percent: Optional[float] = None
    image_url: Optional[str] = None
    position: int = 1


class CategoryScraper:
    """Extrae los productos Top de una página de categoría o ranking de ventas."""

    def __init__(self):
        self._user_agents = settings.USER_AGENTS

    def get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(self._user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "es-CL,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    async def fetch_html(self, url: str) -> str:
        headers = self.get_headers()
        async with httpx.AsyncClient(
            headers=headers,
            timeout=settings.REQUEST_TIMEOUT,
            follow_redirects=True,
            verify=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    def parse_category_page(
        self, html: str, page_url: str, store_id: Optional[str] = None, max_items: int = 50
    ) -> List[ScrapedCategoryProduct]:
        """
        Extrae hasta max_items productos desde el HTML usando estrategia multinivel optimizada:
        1. JSON-LD ItemList (si trae precios completos)
        2. Next.js Pages Router (__NEXT_DATA__) incluyendo soporte para Ripley / Falabella
        3. Next.js App Router (Streaming self.__next_f.push) para Paris / Cencosud
        4. Selectores CSS declarados en stores.json (con precios)
        5. Fallback JSON-LD (si no traía precios pero tiene URLs)
        6. Fallback genérico de enlaces de productos
        """
        soup = BeautifulSoup(html, "html.parser")
        products: List[ScrapedCategoryProduct] = []

        store_rule = find_store_rule_by_id(store_id) if store_id else find_store_rule_for_url(page_url)

        # 1. Estrategia JSON-LD ItemList (si contiene precios válidos)
        json_ld_prods = self._extract_from_json_ld(soup, page_url, max_items)
        if len(json_ld_prods) >= max_items and any(p.price is not None for p in json_ld_prods):
            return json_ld_prods[:max_items]

        # 2. Estrategia Next.js (__NEXT_DATA__)
        products = self._extract_from_next_data(soup, page_url, max_items)
        if len(products) >= max_items and any(p.price is not None for p in products):
            return products[:max_items]

        # 3. Estrategia Next.js App Router (Streamed self.__next_f.push / productData)
        if not products or not any(p.price is not None for p in products):
            stream_prods = self._extract_from_next_stream(html, page_url, max_items)
            if stream_prods and any(p.price is not None for p in stream_prods):
                products = stream_prods
                if len(products) >= max_items:
                    return products[:max_items]

        # 4. Estrategia Selectores CSS de stores.json
        if not products or not any(p.price is not None for p in products):
            if store_rule and store_rule.category_selectors:
                css_prods = self._extract_from_css(soup, page_url, store_rule, max_items)
                if css_prods and any(p.price is not None for p in css_prods):
                    products = css_prods
                    if len(products) >= max_items:
                        return products[:max_items]

        # 5. Si JSON-LD encontró productos aunque sin precio, preferirlos antes del fallback genérico
        if not products and json_ld_prods:
            products = json_ld_prods

        # 6. Estrategia de Fallback Genérico
        if not products:
            products = self._extract_generic_fallback(soup, page_url, max_items)

        return products[:max_items]

    def _extract_from_json_ld(
        self, soup: BeautifulSoup, page_url: str, max_items: int
    ) -> List[ScrapedCategoryProduct]:
        """Busca ItemList o colecciones en Schema.org JSON-LD, soportando anidación y grafos."""
        items: List[ScrapedCategoryProduct] = []

        def collect_nodes(data) -> list:
            nodes = []
            if isinstance(data, list):
                for el in data:
                    nodes.extend(collect_nodes(el))
            elif isinstance(data, dict):
                nodes.append(data)
                if "mainEntity" in data:
                    nodes.extend(collect_nodes(data["mainEntity"]))
                if "@graph" in data:
                    nodes.extend(collect_nodes(data["@graph"]))
            return nodes

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                raw_list = collect_nodes(data)
                for node in raw_list:
                    if node.get("@type") == "ItemList" and "itemListElement" in node:
                        elements = node["itemListElement"]
                        for idx, el in enumerate(elements, start=1):
                            raw_url = el.get("url")
                            title = ""
                            if not raw_url and isinstance(el.get("item"), dict):
                                raw_url = el["item"].get("url")
                                title = el["item"].get("name") or el.get("name") or ""
                            else:
                                title = el.get("name") or ""

                            if raw_url:
                                full_url = urljoin(page_url, raw_url)
                                # Si el título vino vacío o genérico, derivarlo de la URL
                                if not title or title.lower() in ["producto", "item", "default"]:
                                    path_segment = urlparse(full_url).path.rstrip("/").split("/")[-1]
                                    clean_segment = path_segment.replace(".html", "").replace(".p", "")
                                    # Quitar prefijos numéricos como 451081-
                                    clean_segment = re.sub(r'^\d+[-_]', '', clean_segment)
                                    # Reemplazar guiones y guiones bajos por espacios
                                    clean_title = re.sub(r'[-_]+', ' ', clean_segment).strip().title()
                                    title = clean_title if clean_title else "Producto"

                                price_val = None
                                normal_val = None
                                image_val = None
                                item_obj = el.get("item") if isinstance(el.get("item"), dict) else el

                                raw_img = item_obj.get("image")
                                if isinstance(raw_img, list) and raw_img:
                                    image_val = raw_img[0] if isinstance(raw_img[0], str) else raw_img[0].get("url")
                                elif isinstance(raw_img, dict):
                                    image_val = raw_img.get("url")
                                elif isinstance(raw_img, str):
                                    image_val = raw_img

                                offers = item_obj.get("offers")
                                if isinstance(offers, list) and offers:
                                    offers = offers[0]
                                if isinstance(offers, dict):
                                    raw_p = offers.get("price") or offers.get("lowPrice")
                                    if raw_p:
                                        try:
                                            price_val = BaseScraper.clean_price(str(raw_p))
                                        except Exception:
                                            pass
                                    raw_norm = offers.get("highPrice")
                                    if raw_norm:
                                        try:
                                            normal_val = BaseScraper.clean_price(str(raw_norm))
                                        except Exception:
                                            pass

                                discount_pct = None
                                if price_val and normal_val and normal_val > price_val:
                                    discount_pct = round(((normal_val - price_val) / normal_val) * 100, 1)

                                items.append(
                                    ScrapedCategoryProduct(
                                        url=full_url,
                                        title=title.strip(),
                                        price=price_val,
                                        normal_price=normal_val,
                                        discount_percent=discount_pct,
                                        image_url=image_val,
                                        position=idx,
                                    )
                                )
                                if len(items) >= max_items:
                                    return items
            except Exception as ex:
                logger.debug(f"Error procesando JSON-LD de categoría: {ex}")
        return items

    def _extract_from_next_data(
        self, soup: BeautifulSoup, page_url: str, max_items: int
    ) -> List[ScrapedCategoryProduct]:
        """Extrae productos hidratados en páginas Next.js."""
        items: List[ScrapedCategoryProduct] = []
        script = soup.find("script", id="__NEXT_DATA__", type="application/json")
        if not script or not script.string:
            return items

        try:
            nd = json.loads(script.string)
            page_props = nd.get("props", {}).get("pageProps", {})

            # Buscar listas comunes de productos en Next.js
            raw_prods = (
                page_props.get("products")
                or page_props.get("results")
                or page_props.get("searchResult", {}).get("products")
                or page_props.get("initialData", {}).get("products")
                or page_props.get("findabilityProps", {}).get("data", {}).get("products")
                or page_props.get("catalog", {}).get("products")
            )

            if isinstance(raw_prods, list):
                for idx, prod in enumerate(raw_prods, start=1):
                    if not isinstance(prod, dict):
                        continue
                    title_val = (
                        prod.get("displayName")
                        or prod.get("name")
                        or prod.get("title")
                        or "Producto"
                    )

                    url_val = prod.get("url") or prod.get("slug") or prod.get("targetUrl")
                    if not url_val:
                        pid = prod.get("productId") or prod.get("id") or prod.get("parentProductID") or prod.get("sku")
                        if pid and "falabella.com" in page_url:
                            url_val = f"/falabella-cl/product/{pid}"
                        elif pid and "ripley.cl" in page_url:
                            pid_str = str(pid)
                            for a in soup.find_all("a", href=True):
                                href = a["href"].split("?")[0]
                                if href.endswith(f"-{pid_str}") or href.endswith(f"-{pid_str}p"):
                                    url_val = href
                                    break
                            if not url_val:
                                clean_n = re.sub(r'[^a-zA-Z0-9]+', '-', title_val.lower()).strip('-')
                                url_val = f"/{clean_n}-{pid_str}"

                    price_val = None
                    normal_val = None
                    discount_val = None
                    prices_list = prod.get("prices") or []
                    for p_item in prices_list:
                        if not isinstance(p_item, dict):
                            continue
                        p_type = (p_item.get("type") or "").lower()
                        raw_p = p_item.get("price") or p_item.get("originalPrice")
                        if isinstance(raw_p, list) and raw_p:
                            raw_p = raw_p[0]
                        if not raw_p:
                            continue
                        try:
                            clean_p = BaseScraper.clean_price(str(raw_p))
                            if "normal" in p_type or "list" in p_type or p_item.get("crossed") is True:
                                if normal_val is None or clean_p > normal_val:
                                    normal_val = clean_p
                            elif "event" in p_type or "offer" in p_type or "sale" in p_type or "cmr" in p_type:
                                if price_val is None or clean_p < price_val:
                                    price_val = clean_p
                            elif price_val is None:
                                price_val = clean_p
                        except Exception:
                            pass

                    # Fallback si no hubo tipos explícitos
                    if price_val is None and prices_list:
                        raw_p = prices_list[0].get("price") or prices_list[0].get("originalPrice")
                        if isinstance(raw_p, list) and raw_p:
                            raw_p = raw_p[0]
                        if raw_p:
                            try:
                                price_val = BaseScraper.clean_price(str(raw_p))
                            except Exception:
                                pass

                    # Soporte Ripley específico de propiedades de precio
                    if price_val is None:
                        rp = prod.get("ripleyPriceNumber") or prod.get("priceNumber")
                        if rp:
                            try:
                                price_val = float(rp)
                            except Exception:
                                pass
                    if normal_val is None:
                        mp = prod.get("masterPriceNumber")
                        if mp:
                            try:
                                normal_val = float(mp)
                            except Exception:
                                pass
                    if discount_val is None:
                        d_num = prod.get("discount")
                        if d_num:
                            try:
                                discount_val = float(d_num)
                            except Exception:
                                pass

                    # Badge de descuento
                    badge = prod.get("discountBadge")
                    if isinstance(badge, dict) and badge.get("label"):
                        lbl = badge["label"].replace("%", "").replace("-", "").strip()
                        try:
                            discount_val = float(lbl)
                        except Exception:
                            pass

                    if normal_val and price_val and normal_val > price_val and discount_val is None:
                        discount_val = round(((normal_val - price_val) / normal_val) * 100, 1)

                    img_url = None
                    media_list = prod.get("media") or prod.get("mediaUrls") or []
                    if isinstance(media_list, list) and media_list:
                        m0 = media_list[0]
                        img_url = m0.get("url") if isinstance(m0, dict) else str(m0)
                    if not img_url and prod.get("primaryImage"):
                        img_url = prod.get("primaryImage")

                    if url_val:
                        full_url = urljoin(page_url, url_val)
                        items.append(
                            ScrapedCategoryProduct(
                                url=full_url,
                                title=title_val.strip(),
                                price=price_val,
                                normal_price=normal_val,
                                discount_percent=discount_val,
                                image_url=img_url,
                                position=idx,
                            )
                        )
                        if len(items) >= max_items:
                            break
        except Exception as ex:
            logger.debug(f"Error procesando __NEXT_DATA__ de categoría: {ex}")

        return items

    def _extract_from_next_stream(
        self, html: str, page_url: str, max_items: int
    ) -> List[ScrapedCategoryProduct]:
        """Extrae productos transmitidos en Next.js App Router (self.__next_f.push) o payloads serializados."""
        items: List[ScrapedCategoryProduct] = []
        if "productData" not in html and "initialState" not in html:
            return items

        match = re.search(r'(?:\\"|")productData(?:\\"|")\s*:\s*\{(?:.*?)(?:\\"|")products(?:\\"|")\s*:\s*\[', html)
        if not match:
            return items

        start_idx = match.end() - 1
        bracket_count = 0
        end_idx = start_idx
        for i in range(start_idx, min(start_idx + 500000, len(html))):
            if html[i] == '[':
                bracket_count += 1
            elif html[i] == ']':
                bracket_count -= 1
                if bracket_count == 0:
                    end_idx = i + 1
                    break

        if end_idx <= start_idx:
            return items

        raw_array = html[start_idx:end_idx]
        clean_json = raw_array.replace(r'\"', '"').replace(r'\\', '\\')
        try:
            products = json.loads(clean_json)
            for idx, p in enumerate(products, start=1):
                if not isinstance(p, dict):
                    continue
                name = p.get("name") or p.get("displayName") or "Producto"
                slug = p.get("slug") or p.get("url") or p.get("key")
                if not slug:
                    continue

                prod_url = urljoin(
                    page_url,
                    f"/{slug}.html" if not str(slug).endswith(".html") and not str(slug).startswith("http") else str(slug),
                )

                mv = p.get("masterVariant", {})
                prices = mv.get("prices", {}) if isinstance(mv, dict) else {}
                reg_val = prices.get("regular", {}).get("value", {}).get("centAmount")
                off_val = prices.get("offer", {}).get("value", {}).get("centAmount")
                card_val = prices.get("paymentMethod", {}).get("value", {}).get("centAmount")

                price = float(card_val or off_val or reg_val) if (card_val or off_val or reg_val) else None
                normal_price = float(reg_val) if (reg_val and price and reg_val > price) else None
                disc = None
                if normal_price and price:
                    disc = round(((normal_price - price) / normal_price) * 100, 1)

                images = mv.get("images", []) if isinstance(mv, dict) else []
                img = images[0].get("url") if images and isinstance(images[0], dict) else None

                items.append(
                    ScrapedCategoryProduct(
                        url=prod_url,
                        title=name.strip(),
                        price=price,
                        normal_price=normal_price,
                        discount_percent=disc,
                        image_url=img,
                        position=idx,
                    )
                )
                if len(items) >= max_items:
                    break
        except Exception as ex:
            logger.debug(f"Error parseando streaming Next.js de categoría: {ex}")

        return items

    def _extract_from_css(
        self, soup: BeautifulSoup, page_url: str, store_rule, max_items: int
    ) -> List[ScrapedCategoryProduct]:
        """Extrae productos utilizando selectores CSS específicos de la tienda."""
        items: List[ScrapedCategoryProduct] = []
        sel = store_rule.category_selectors

        cards = []
        for card_sel in sel.card:
            found = soup.select(card_sel)
            if found:
                cards = found
                break

        if not cards:
            return items

        seen_urls = set()
        for idx, card in enumerate(cards, start=1):
            # Enlace
            link_elem = None
            for l_sel in sel.link:
                link_elem = card.select_one(l_sel)
                if link_elem and link_elem.get("href"):
                    break

            if not link_elem or not link_elem.get("href"):
                if card.name == "a" and card.get("href"):
                    link_elem = card
                else:
                    link_elem = card.find("a", href=True)

            if not link_elem or not link_elem.get("href"):
                continue

            raw_href = link_elem["href"].strip()
            # Ignorar enlaces a filtros, JavaScript o vacíos
            if raw_href.startswith(("#", "javascript:", "mailto:")):
                continue

            full_url = urljoin(page_url, raw_href)
            # Limpiar anclas
            if "#" in full_url:
                full_url = full_url.split("#")[0]

            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            # Título
            title = None
            for t_sel in sel.title:
                t_elem = card.select_one(t_sel)
                if t_elem and t_elem.get_text(strip=True):
                    title = t_elem.get_text(strip=True)
                    break

            if not title:
                title = link_elem.get_text(strip=True) or link_elem.get("title") or "Producto"

            # Precio
            price = None
            for p_sel in sel.price:
                p_elem = card.select_one(p_sel)
                if p_elem and p_elem.get_text(strip=True):
                    raw_p = p_elem.get_text(strip=True)
                    try:
                        price = BaseScraper.clean_price(raw_p)
                        if store_rule.id == "amazon":
                            raw_l = raw_p.lower()
                            if "usd" in raw_l or "us$" in raw_l or price < 500.0:
                                price = float(round(price * 960.0))
                        break
                    except Exception:
                        continue

            items.append(
                ScrapedCategoryProduct(
                    url=full_url,
                    title=title,
                    price=price,
                    position=len(items) + 1,
                )
            )
            if len(items) >= max_items:
                break

        return items

    def _extract_generic_fallback(
        self, soup: BeautifulSoup, page_url: str, max_items: int
    ) -> List[ScrapedCategoryProduct]:
        """Estrategia de respaldo genérica basada en heurísticas de enlaces a productos."""
        items: List[ScrapedCategoryProduct] = []
        seen = set()
        product_indicators = [
            "/product/",
            "/products/",
            "/producto/",
            "/articulo/",
            "/p/",
            "/dp/",
            "/gp/product/",
            "articulo.mercadolibre",
            ".html",
            "/equipos/",
            "/item/",
        ]

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if any(ind in href.lower() for ind in product_indicators):
                full_url = urljoin(page_url, href).split("#")[0]
                # Filtrar enlaces de paginación o categorías
                if full_url in seen or "category" in full_url.lower():
                    continue
                seen.add(full_url)
                title = a.get_text(strip=True) or a.get("title") or "Producto"
                if len(title) < 5:
                    continue
                items.append(
                    ScrapedCategoryProduct(
                        url=full_url,
                        title=title,
                        position=len(items) + 1,
                    )
                )
                if len(items) >= max_items:
                    break

        return items


class CategoryCrawlerService:
    """Orquestador para descubrir y registrar en la BD los Top 10 productos por categoría."""

    def __init__(self, scraper: Optional[CategoryScraper] = None, notifier=None):
        self.scraper = scraper or CategoryScraper()
        if notifier is not None:
            self.notifier = notifier
        else:
            try:
                from src.services.notifier import TelegramNotifier
                self.notifier = TelegramNotifier()
            except Exception:
                self.notifier = None

    @staticmethod
    def _get_page_url(base_url: str, page_num: int) -> str:
        """Construye la URL paginada según el formato de cada tienda."""
        if "mercadolibre.cl" in base_url:
            offset = (page_num - 1) * 50 + 1
            if "_Desde_" in base_url:
                return re.sub(r'_Desde_\d+', f'_Desde_{offset}', base_url)
            return base_url.rstrip("/") + f"_Desde_{offset}"
        parsed = urlparse(base_url)
        qs = parse_qs(parsed.query)
        qs["page"] = [str(page_num)]
        new_query = urlencode(qs, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

    async def crawl_category_store(
        self, store_id: str, category_item: CategoryItem, max_products: int = 50
    ) -> List[ScrapedCategoryProduct]:
        """
        Rastrea el listado de una tienda para una categoría dada,
        soportando paginación para alcanzar hasta max_products (o el límite ampliado para macro-tiendas).
        """
        url = category_item.stores.get(store_id.lower())
        if not url:
            logger.debug(f"La tienda '{store_id}' no está configurada para '{category_item.name}'.")
            return []

        # Determinar límite efectivo: si es macro-tienda con catálogo amplio, ampliar si está configurado
        major_stores = {"falabella", "sodimac", "ripley", "paris", "mercadolibre", "easy"}
        major_limit = getattr(settings, "MAJOR_STORES_PRODUCTS_LIMIT", 80)
        effective_limit = max(max_products, major_limit) if store_id.lower() in major_stores else max_products

        logger.info(
            f"Escaneando Top {effective_limit} para [{store_id.upper()}] en '{category_item.name}': {url}"
        )
        items: List[ScrapedCategoryProduct] = []
        seen_urls = set()

        # Paginación inteligente: si 1 página no es suficiente para alcanzar effective_limit, consultar pág 2 y 3
        current_page = 1
        max_pages = 3 if effective_limit > 30 else 1

        while current_page <= max_pages and len(items) < effective_limit:
            page_url = url if current_page == 1 else self._get_page_url(url, current_page)
            try:
                html = await self.scraper.fetch_html(page_url)
                # Delegar el parseo pesado de BeautifulSoup a un hilo secundario
                # para no bloquear el bucle de eventos ni retrasar healthchecks de Render
                page_items = await asyncio.to_thread(
                    self.scraper.parse_category_page,
                    html=html,
                    page_url=page_url,
                    store_id=store_id,
                    max_items=effective_limit,
                )
                if not page_items:
                    break

                # Pausa cooperativa para permitir atención inmediata de healthchecks
                await asyncio.sleep(0.3)

                new_count = 0
                for p in page_items:
                    if p.url not in seen_urls:
                        seen_urls.add(p.url)
                        p.position = len(items) + 1
                        items.append(p)
                        new_count += 1
                        if len(items) >= effective_limit:
                            break

                # Si una página subsiguiente no aporta nuevos productos, detener paginación
                if new_count == 0:
                    break

                current_page += 1
            except Exception as ex:
                logger.error(
                    f"Error al rastrear categoría '{category_item.name}' (pág {current_page}) en tienda '{store_id}': {ex}"
                )
                break

        logger.info(
            f"[{store_id.upper()}] Detectados {len(items)} productos en '{category_item.name}'."
        )
        return items

    async def sync_category(
        self, session: AsyncSession, category_id: str, max_products: int = 50
    ) -> Tuple[int, int]:
        """
        Sincroniza los productos de una categoría específica en todas sus tiendas configuradas.
        Inserta nuevos productos o actualiza los existentes con la categoría.
        Alerta inmediatamente si un producto nuevo o actualizado presenta un descuento relevante de catálogo.
        Retorna (agregados, actualizados).
        """
        catalog = load_categories_catalog()
        cat_item = next(
            (c for c in catalog.categories if c.id.lower() == category_id.lower()), None
        )
        if not cat_item:
            logger.warning(f"Categoría con ID '{category_id}' no encontrada.")
            return 0, 0

        if not cat_item.active:
            logger.info(f"Categoría '{cat_item.name}' está inactiva. Omitiendo.")
            return 0, 0

        total_added = 0
        total_updated = 0

        seen_batch_urls = set()
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        for store_id in cat_item.stores.keys():
            store_rule = find_store_rule_by_id(store_id)
            store_name = store_rule.name if store_rule else store_id.capitalize()

            products = await self.crawl_category_store(
                store_id=store_id, category_item=cat_item, max_products=max_products
            )
            # Ceder el event loop entre tiendas para evitar saturación de CPU
            await asyncio.sleep(0.5)

            for prod in products:
                if not prod.url or prod.url in seen_batch_urls:
                    continue
                seen_batch_urls.add(prod.url)

                stmt = select(Product).where(Product.url_original == prod.url)
                existing = (await session.execute(stmt)).scalar_one_or_none()

                # Calcular descuento de catálogo si está disponible
                catalog_discount = prod.discount_percent
                if catalog_discount is None and prod.normal_price and prod.price and prod.normal_price > prod.price:
                    catalog_discount = round(((prod.normal_price - prod.price) / prod.normal_price) * 100, 1)

                if existing:
                    # Actualizar metadatos si era necesario
                    modified = False
                    if not existing.categoria:
                        existing.categoria = cat_item.name
                        modified = True
                    if not existing.es_top_categoria:
                        existing.es_top_categoria = True
                        modified = True
                    if prod.price and existing.precio_actual is None:
                        existing.precio_actual = prod.price
                        existing.precio_minimo = prod.price
                        modified = True
                    elif prod.price and existing.precio_actual is not None and prod.price < existing.precio_actual:
                        # Bajada de precio en producto ya monitoreado
                        old_price = existing.precio_actual
                        reduction = old_price - prod.price
                        pct = (reduction / old_price) * 100
                        is_all_time_low = (
                            existing.precio_minimo is not None and prod.price < existing.precio_minimo
                        )

                        existing.precio_actual = prod.price
                        if existing.precio_minimo is None or prod.price < existing.precio_minimo:
                            existing.precio_minimo = prod.price
                        modified = True

                        if pct >= cat_item.default_threshold_percent and self.notifier:
                            # Comprobación estricta de cooldown anti-spam (settings.ALERT_COOLDOWN_HOURS)
                            in_cooldown = False
                            if existing.ultima_alerta_en is not None:
                                last_alert = existing.ultima_alerta_en
                                if last_alert.tzinfo is None:
                                    last_alert = last_alert.replace(tzinfo=datetime.timezone.utc)
                                time_since = now_utc - last_alert
                                cooldown_delta = datetime.timedelta(hours=settings.ALERT_COOLDOWN_HOURS)
                                if time_since < cooldown_delta:
                                    in_cooldown = True

                            # Regla anti-spam estricta: NO alertar repetidamente durante el cooldown
                            # salvo que rompa un mínimo histórico absoluto previo
                            if in_cooldown and not is_all_time_low:
                                logger.info(
                                    f"Alerta en categoría omitida para [{store_name}] '{existing.nombre or prod.title}' "
                                    f"por cooldown anti-spam ({settings.ALERT_COOLDOWN_HOURS}h)."
                                )
                            else:
                                aff_url = prod.url
                                if store_rule:
                                    from src.scrapers.configurable import ConfigurableScraper
                                    aff_url = ConfigurableScraper(store_rule).build_affiliate_url(prod.url)
                                from src.services.notifier import AlertPayload
                                payload = AlertPayload(
                                    title=existing.nombre or prod.title,
                                    store=store_name,
                                    old_price=old_price,
                                    new_price=prod.price,
                                    discount_percent=pct,
                                    affiliate_url=aff_url,
                                    is_all_time_low=is_all_time_low,
                                    image_url=prod.image_url,
                                    category=cat_item.name,
                                    is_price_error=(pct >= settings.ERROR_DISCOUNT_THRESHOLD_PERCENT),
                                )
                                try:
                                    await self.notifier.send_alert(payload)
                                    existing.ultima_alerta_en = now_utc
                                except Exception as ex:
                                    logger.warning(f"Error despachando alerta de actualización: {ex}")

                    if modified:
                        total_updated += 1
                else:
                    new_prod = Product(
                        url_original=prod.url,
                        nombre=prod.title,
                        tienda=store_name,
                        precio_actual=prod.price,
                        precio_minimo=prod.price,
                        umbral_descuento_porcentaje=cat_item.default_threshold_percent,
                        categoria=cat_item.name,
                        es_top_categoria=True,
                        activo=True,
                    )
                    session.add(new_prod)
                    total_added += 1

                    # ¡Alerta instantánea para ofertas de catálogo en productos nuevos!
                    if catalog_discount and prod.price and catalog_discount >= cat_item.default_threshold_percent:
                        ref_price = prod.normal_price or (prod.price / (1 - (catalog_discount / 100)))
                        is_price_error = (catalog_discount >= settings.ERROR_DISCOUNT_THRESHOLD_PERCENT)
                        new_prod.ultima_alerta_en = now_utc

                        if self.notifier:
                            aff_url = prod.url
                            if store_rule:
                                from src.scrapers.configurable import ConfigurableScraper
                                aff_url = ConfigurableScraper(store_rule).build_affiliate_url(prod.url)
                            from src.services.notifier import AlertPayload
                            payload = AlertPayload(
                                title=prod.title,
                                store=store_name,
                                old_price=ref_price,
                                new_price=prod.price,
                                discount_percent=catalog_discount,
                                affiliate_url=aff_url,
                                is_all_time_low=True,
                                image_url=prod.image_url,
                                category=cat_item.name,
                                is_price_error=is_price_error,
                            )
                            try:
                                await self.notifier.send_alert(payload)
                                logger.info(
                                    f"¡Alerta de oferta de catálogo despachada! [{store_name}] {prod.title} (-{catalog_discount:.1f}%)"
                                )
                            except Exception as alert_err:
                                logger.warning(f"Error despachando alerta de catálogo para {prod.title}: {alert_err}")

        await session.commit()
        logger.info(
            f"Categoría '{cat_item.name}' sincronizada: {total_added} agregados, {total_updated} actualizados."
        )
        return total_added, total_updated

    async def sync_all_categories(
        self, session: AsyncSession, max_products: int = 50
    ) -> Dict[str, Tuple[int, int]]:
        """
        Sincroniza los Top productos de todas las categorías activas registradas en categories.json.
        """
        catalog = load_categories_catalog()
        results: Dict[str, Tuple[int, int]] = {}

        logger.info(f"=== Iniciando sincronización de {len(catalog.categories)} categorías ===")
        for cat in catalog.categories:
            if not cat.active:
                continue
            try:
                added, updated = await self.sync_category(
                    session=session, category_id=cat.id, max_products=max_products
                )
                results[cat.id] = (added, updated)
                # Liberar objetos del identity map de SQLAlchemy y limpiar memoria RAM
                session.expunge_all()
                import gc
                gc.collect()
                # Pausa cooperativa entre categorías para que el servidor responda healthchecks
                await asyncio.sleep(1.0)
            except Exception as ex:
                logger.error(
                    f"Error sincronizando categoría '{cat.name}' ({cat.id}): {ex}"
                )
                await session.rollback()
                session.expunge_all()
                results[cat.id] = (0, 0)

        logger.info("=== Sincronización de todas las categorías completada exitosamente ===")
        return results
