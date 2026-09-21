import json
import logging
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

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
    image_url: Optional[str] = None
    position: int = 1


class CategoryScraper:
    """Extrae los productos Top de una página de categoría o ranking de ventas."""

    def __init__(self):
        self._user_agents = settings.USER_AGENTS

    def get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(self._user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
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
        self, html: str, page_url: str, store_id: Optional[str] = None, max_items: int = 10
    ) -> List[ScrapedCategoryProduct]:
        """
        Extrae hasta max_items productos desde el HTML usando estrategia multinivel:
        1. JSON-LD ItemList
        2. Next.js __NEXT_DATA__
        3. Selectores CSS declarados en stores.json
        4. Fallback genérico de enlaces de productos
        """
        soup = BeautifulSoup(html, "html.parser")
        products: List[ScrapedCategoryProduct] = []

        store_rule = find_store_rule_by_id(store_id) if store_id else find_store_rule_for_url(page_url)

        # 1. Estrategia JSON-LD ItemList
        products = self._extract_from_json_ld(soup, page_url, max_items)
        if len(products) >= max_items:
            return products[:max_items]

        # 2. Estrategia Next.js (__NEXT_DATA__)
        if not products:
            products = self._extract_from_next_data(soup, page_url, max_items)
            if len(products) >= max_items:
                return products[:max_items]

        # 3. Estrategia Selectores CSS de stores.json
        if not products and store_rule and store_rule.category_selectors:
            products = self._extract_from_css(soup, page_url, store_rule, max_items)
            if len(products) >= max_items:
                return products[:max_items]

        # 4. Estrategia de Fallback Genérico
        if not products:
            products = self._extract_generic_fallback(soup, page_url, max_items)

        return products[:max_items]

    def _extract_from_json_ld(
        self, soup: BeautifulSoup, page_url: str, max_items: int
    ) -> List[ScrapedCategoryProduct]:
        """Busca ItemList o colecciones en Schema.org JSON-LD."""
        items: List[ScrapedCategoryProduct] = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                raw_list = data if isinstance(data, list) else [data]
                for node in raw_list:
                    if node.get("@type") == "ItemList" and "itemListElement" in node:
                        elements = node["itemListElement"]
                        for idx, el in enumerate(elements, start=1):
                            raw_url = el.get("url")
                            if not raw_url and isinstance(el.get("item"), dict):
                                raw_url = el["item"].get("url")
                                title = el["item"].get("name") or el.get("name") or "Producto"
                            else:
                                title = el.get("name") or "Producto"

                            if raw_url:
                                full_url = urljoin(page_url, raw_url)
                                items.append(
                                    ScrapedCategoryProduct(
                                        url=full_url,
                                        title=title.strip(),
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
            )

            if isinstance(raw_prods, list):
                for idx, prod in enumerate(raw_prods, start=1):
                    if not isinstance(prod, dict):
                        continue
                    url_val = prod.get("url") or prod.get("slug") or prod.get("targetUrl")
                    if not url_val:
                        pid = prod.get("productId") or prod.get("id")
                        if pid and "falabella.com" in page_url:
                            url_val = f"/falabella-cl/product/{pid}"

                    title_val = (
                        prod.get("displayName")
                        or prod.get("name")
                        or prod.get("title")
                        or "Producto"
                    )

                    price_val = None
                    prices_list = prod.get("prices") or []
                    if isinstance(prices_list, list) and len(prices_list) > 0:
                        raw_p = prices_list[0].get("price") or prices_list[0].get("originalPrice")
                        if raw_p:
                            try:
                                price_val = BaseScraper.clean_price(str(raw_p))
                            except Exception:
                                pass

                    if url_val:
                        full_url = urljoin(page_url, url_val)
                        items.append(
                            ScrapedCategoryProduct(
                                url=full_url,
                                title=title_val.strip(),
                                price=price_val,
                                position=idx,
                            )
                        )
                        if len(items) >= max_items:
                            break
        except Exception as ex:
            logger.debug(f"Error procesando __NEXT_DATA__ de categoría: {ex}")

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

    def __init__(self, scraper: Optional[CategoryScraper] = None):
        self.scraper = scraper or CategoryScraper()

    async def crawl_category_store(
        self, store_id: str, category_item: CategoryItem, max_products: int = 10
    ) -> List[ScrapedCategoryProduct]:
        """Rastrea el ranking/listado de una tienda para una categoría dada."""
        url = category_item.stores.get(store_id.lower())
        if not url:
            logger.debug(f"La tienda '{store_id}' no está configurada para '{category_item.name}'.")
            return []

        logger.info(
            f"Escaneando Top {max_products} para [{store_id.upper()}] en '{category_item.name}': {url}"
        )
        try:
            html = await self.scraper.fetch_html(url)
            items = self.scraper.parse_category_page(
                html=html, page_url=url, store_id=store_id, max_items=max_products
            )
            logger.info(
                f"[{store_id.upper()}] Detectados {len(items)} productos en '{category_item.name}'."
            )
            return items
        except Exception as ex:
            logger.error(
                f"Error al rastrear categoría '{category_item.name}' en tienda '{store_id}': {ex}"
            )
            return []

    async def sync_category(
        self, session: AsyncSession, category_id: str, max_products: int = 10
    ) -> Tuple[int, int]:
        """
        Sincroniza los productos de una categoría específica en todas sus tiendas configuradas.
        Inserta nuevos productos o actualiza los existentes con la categoría.
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

        for store_id in cat_item.stores.keys():
            store_rule = find_store_rule_by_id(store_id)
            store_name = store_rule.name if store_rule else store_id.capitalize()

            products = await self.crawl_category_store(
                store_id=store_id, category_item=cat_item, max_products=max_products
            )

            for prod in products:
                if not prod.url or prod.url in seen_batch_urls:
                    continue
                seen_batch_urls.add(prod.url)

                stmt = select(Product).where(Product.url_original == prod.url)
                existing = (await session.execute(stmt)).scalar_one_or_none()

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

        await session.commit()
        logger.info(
            f"Categoría '{cat_item.name}' sincronizada: {total_added} agregados, {total_updated} actualizados."
        )
        return total_added, total_updated

    async def sync_all_categories(
        self, session: AsyncSession, max_products: int = 10
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
            except Exception as ex:
                logger.error(
                    f"Error sincronizando categoría '{cat.name}' ({cat.id}): {ex}"
                )
                await session.rollback()
                results[cat.id] = (0, 0)

        logger.info("=== Sincronización de todas las categorías completada exitosamente ===")
        return results
