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
        normal_price = None
        discount_percent = None
        in_stock = True
        image_url = None

        # Estrategia 1: Schema.org JSON-LD si la tienda lo tiene habilitado
        if self.rule.use_json_ld:
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
                            # Validar pertenencia a la URL actual
                            item_url = None
                            main_entity = item.get("mainEntityOfPage")
                            if isinstance(main_entity, dict):
                                item_url = main_entity.get("@id") or main_entity.get("url")
                            elif isinstance(main_entity, str):
                                item_url = main_entity
                            elif "url" in item:
                                item_url = item.get("url")

                            if item_url and target_path:
                                item_path = urlparse(item_url).path.rstrip("/")
                                # En Sodimac/Falabella, el @id a menudo incluye el SKU al final
                                # o difiere levemente en el slug. Verificamos coincidencia flexible
                                match_path = (target_path == item_path)
                                if not match_path and item_path:
                                    if target_path.startswith(item_path) or item_path.startswith(target_path):
                                        match_path = True
                                    else:
                                        t_ids = [p for p in target_path.split("/") if p.isdigit() and len(p) >= 5]
                                        i_ids = [p for p in item_path.split("/") if p.isdigit() and len(p) >= 5]
                                        if t_ids and i_ids and set(t_ids).intersection(set(i_ids)):
                                            match_path = True
                                if not match_path:
                                    continue

                            title = item.get("name", title)
                            offers = item.get("offers", {})
                            if isinstance(offers, dict) and "price" in offers:
                                price = float(offers["price"])
                                if offers.get("availability") == "https://schema.org/OutOfStock":
                                    in_stock = False
                            elif isinstance(offers, list) and len(offers) > 0:
                                valid_prices = []
                                for off in offers:
                                    if isinstance(off, dict) and "price" in off:
                                        try:
                                            valid_prices.append(float(off["price"]))
                                        except Exception:
                                            pass
                                        if off.get("availability") == "https://schema.org/OutOfStock":
                                            in_stock = False
                                if valid_prices:
                                    price = min(valid_prices)
                                    if len(valid_prices) > 1 and max(valid_prices) > price:
                                        normal_price = max(valid_prices)
                            elif isinstance(offers, list) and len(offers) == 0:
                                in_stock = False

                            if "image" in item:
                                img = item["image"]
                                image_url = img if isinstance(img, str) else (img[0] if isinstance(img, list) else None)
                            found_product = True
                            break
                except Exception as ex:
                    logger.debug(f"[{self.rule.name}] Error leyendo JSON-LD: {ex}")

        # Estrategia 2: Extracción en páginas Next.js (__NEXT_DATA__)
        next_data_script = soup.find("script", id="__NEXT_DATA__", type="application/json")
        if next_data_script and next_data_script.string:
            try:
                nd = json.loads(next_data_script.string)
                page_props = nd.get("props", {}).get("pageProps", {})
                prod_data = (
                    page_props.get("product")
                    or page_props.get("productData")
                    or page_props.get("initialData", {}).get("product")
                )
                if isinstance(prod_data, dict):
                    if not title:
                        title = prod_data.get("name") or prod_data.get("displayName") or prod_data.get("title")

                    if prod_data.get("isOutOfStock") is True or prod_data.get("isPurchaseable") is False:
                        in_stock = False

                    # Estructura Falabella / Sodimac con variants
                    variants = prod_data.get("variants") or []
                    if isinstance(variants, list) and len(variants) > 0:
                        cur_id = str(prod_data.get("currentVariant") or prod_data.get("primaryVariantId") or "")
                        target_var = next((v for v in variants if isinstance(v, dict) and str(v.get("id")) == cur_id), None)
                        if not target_var and isinstance(variants[0], dict):
                            target_var = variants[0]

                        if target_var:
                            if target_var.get("availability") == "out_of_stock":
                                in_stock = False
                            var_prices = target_var.get("prices") or []
                            for p_entry in var_prices:
                                if not isinstance(p_entry, dict):
                                    continue
                                p_type = (p_entry.get("type") or "").lower()
                                raw_val = p_entry.get("price")
                                val_str = raw_val[0] if isinstance(raw_val, list) and raw_val else str(raw_val or "")
                                if not val_str:
                                    continue
                                parsed_p = self.clean_price(val_str)
                                if "normal" in p_type or "list" in p_type:
                                    if normal_price is None or parsed_p > normal_price:
                                        normal_price = parsed_p
                                elif "event" in p_type or "offer" in p_type or "sale" in p_type or "cmr" in p_type:
                                    if price is None or parsed_p < price:
                                        price = parsed_p
                                elif price is None:
                                    price = parsed_p

                            badge = target_var.get("discountBadge") or prod_data.get("discountBadge")
                            if isinstance(badge, dict) and badge.get("label"):
                                lbl = badge["label"].replace("%", "").replace("-", "").strip()
                                try:
                                    discount_percent = float(lbl)
                                except Exception:
                                    pass

                    # Fallback de propiedades directas de precio
                    if price is None:
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

        # Calcular porcentaje de descuento si tenemos precio normal y de oferta
        if normal_price and price and normal_price > price and discount_percent is None:
            discount_percent = round(((normal_price - price) / normal_price) * 100, 1)

        # Si no hay precio pero el producto está agotado
        if price is None and not in_stock:
            raise ValueError(f"[{self.rule.name}] El producto se encuentra agotado (sin stock) en la tienda: {url}")

        if price is None:
            raise ValueError(f"[{self.rule.name}] No fue posible extraer el precio en la URL: {url}")

        affiliate_url = self.build_affiliate_url(url)

        return ScrapedItem(
            title=title.strip() if title else f"Producto {self.rule.name}",
            price=price,
            normal_price=normal_price,
            discount_percent=discount_percent,
            in_stock=in_stock,
            image_url=image_url,
            affiliate_url=affiliate_url,
        )
