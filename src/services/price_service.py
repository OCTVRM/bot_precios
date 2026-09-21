import datetime
import logging
from typing import Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.config import settings
from src.models import PriceHistory, Product
from src.scrapers.base import ScrapedItem
from src.scrapers.registry import get_scraper_for_url
from src.services.notifier import AlertPayload, TelegramNotifier

logger = logging.getLogger(__name__)


class PriceTrackingService:
    """Servicio orquestador del ciclo de vida de rastreo, análisis de descuentos y persistencia."""

    def __init__(self, notifier: Optional[TelegramNotifier] = None):
        self.notifier = notifier or TelegramNotifier()

    async def process_product(
        self, session: AsyncSession, product: Product
    ) -> Tuple[bool, Optional[AlertPayload]]:
        """
        Ejecuta el ciclo completo para un producto:
        1. Raspado web del precio actual.
        2. Registro en historial de precios.
        3. Evaluación de reglas de descuento y precio objetivo.
        4. Control de spam / cooldown.
        5. Envío de notificación a Telegram si corresponde.
        """
        scraper = get_scraper_for_url(product.url_original)
        logger.info(f"Iniciando verificación para [{product.tienda}] ID {product.id}: {product.url_original}")

        try:
            item: ScrapedItem = await scraper.scrape(product.url_original)
        except Exception as ex:
            logger.error(f"Error raspando producto ID {product.id} ({product.url_original}): {ex}")
            return False, None

        current_price = item.price
        old_price = product.precio_actual
        now = datetime.datetime.now(datetime.timezone.utc)

        # Salvaguarda de consistencia de divisa (USD vs CLP en Amazon):
        # Si la base de datos tenía un precio en pesos chilenos (old_price >= 1000) y el nuevo precio es < 500 (en USD):
        if product.tienda.lower() == "amazon" and current_price < 500.0 and old_price is not None and old_price >= 1000.0:
            current_price = float(round(current_price * 960.0))
            item.price = current_price

        # Actualizar título si no estaba definido o cambió
        if item.title and (not product.nombre or product.nombre == "Pendiente"):
            product.nombre = item.title

        # Registrar historial de precio
        history_entry = PriceHistory(
            product_id=product.id,
            precio=current_price,
            timestamp=now,
        )
        session.add(history_entry)

        alert_payload: Optional[AlertPayload] = None
        should_alert = False

        if old_price is not None and old_price > 0:
            reduction = old_price - current_price
            discount_percent = (reduction / old_price) * 100

            # Condición 1: El precio bajó y supera el umbral de descuento configurado
            threshold_met = (
                current_price < old_price
                and discount_percent >= product.umbral_descuento_porcentaje
            )

            # Condición 2: Alcanzó el precio objetivo y es menor al anterior
            target_met = (
                product.precio_objetivo is not None
                and current_price <= product.precio_objetivo
                and current_price < old_price
            )

            if threshold_met or target_met:
                # Comprobación de anti-spam y cooldown
                in_cooldown = False
                if product.ultima_alerta_en is not None:
                    # Asegurar comparación timezone-aware
                    last_alert = product.ultima_alerta_en
                    if last_alert.tzinfo is None:
                        last_alert = last_alert.replace(tzinfo=datetime.timezone.utc)

                    time_since_alert = now - last_alert
                    cooldown_delta = datetime.timedelta(hours=settings.ALERT_COOLDOWN_HOURS)
                    if time_since_alert < cooldown_delta:
                        in_cooldown = True

                # Excepción del cooldown: Si el precio es menor al mínimo histórico anterior, se alerta de inmediato
                is_all_time_low = (
                    product.precio_minimo is None or current_price < product.precio_minimo
                )

                if not in_cooldown or is_all_time_low:
                    should_alert = True
                    affiliate_url = item.affiliate_url or scraper.build_affiliate_url(product.url_original)
                    alert_payload = AlertPayload(
                        title=product.nombre or item.title,
                        store=product.tienda,
                        old_price=old_price,
                        new_price=current_price,
                        discount_percent=discount_percent,
                        affiliate_url=affiliate_url,
                        is_all_time_low=is_all_time_low,
                        target_price=product.precio_objetivo,
                        image_url=item.image_url,
                        category=product.categoria,
                        is_price_error=(discount_percent >= settings.ERROR_DISCOUNT_THRESHOLD_PERCENT),
                    )
                else:
                    logger.info(
                        f"Descuento detectado en ID {product.id} pero retenido por cooldown anti-spam "
                        f"({settings.ALERT_COOLDOWN_HOURS}h)."
                    )

        # Actualizar estado del producto
        product.precio_actual = current_price
        if product.precio_minimo is None or current_price < product.precio_minimo:
            product.precio_minimo = current_price

        if should_alert and alert_payload:
            product.ultima_alerta_en = now
            logger.info(
                f"Disparando alerta para ID {product.id} - Precio: {old_price} -> {current_price} "
                f"(-{alert_payload.discount_percent:.1f}%)"
            )
            # Enviar mensaje a Telegram
            await self.notifier.send_alert(alert_payload)

        await session.flush()
        return should_alert, alert_payload

    async def get_active_products(self, session: AsyncSession):
        """Retorna la lista de productos activos para monitoreo."""
        stmt = select(Product).where(Product.activo.is_(True))
        result = await session.execute(stmt)
        return result.scalars().all()

    async def sync_monitored_urls_file(
        self, session: AsyncSession, file_path: Optional[str] = None
    ) -> Tuple[int, int]:
        """
        Lee el archivo JSON de URLs monitoreadas (por defecto monitored_urls.json)
        y sincroniza los registros en la base de datos (inserta nuevos o actualiza metas).
        Retorna (agregados, actualizados).
        """
        import json
        from pathlib import Path

        path = Path(file_path) if file_path else (Path(__file__).resolve().parent.parent.parent / "monitored_urls.json")
        if not path.exists():
            logger.warning(f"Archivo de URLs {path} no encontrado. Omitiendo sincronización.")
            return 0, 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception as ex:
            logger.error(f"Error leyendo {path}: {ex}")
            return 0, 0

        if not isinstance(items, list):
            logger.error(f"El archivo {path} debe contener una lista JSON de objetos.")
            return 0, 0

        added = 0
        updated = 0

        for item in items:
            url = item.get("url")
            if not url:
                continue

            url = url.strip()
            name = item.get("name")
            target_price = item.get("target_price")
            threshold = item.get("threshold_percent", settings.DEFAULT_DISCOUNT_THRESHOLD_PERCENT)
            active = item.get("active", True)

            stmt = select(Product).where(Product.url_original == url)
            product = (await session.execute(stmt)).scalar_one_or_none()

            if product:
                # Actualizar metadatos si vienen provistos
                modified = False
                if name and product.nombre != name:
                    product.nombre = name
                    modified = True
                if target_price is not None and product.precio_objetivo != target_price:
                    product.precio_objetivo = target_price
                    modified = True
                if threshold is not None and product.umbral_descuento_porcentaje != threshold:
                    product.umbral_descuento_porcentaje = threshold
                    modified = True
                if active is not None and product.activo != active:
                    product.activo = active
                    modified = True
                if modified:
                    updated += 1
            else:
                # Nuevo producto
                scraper = get_scraper_for_url(url)
                store = scraper.store_name
                new_prod = Product(
                    url_original=url,
                    nombre=name or f"Producto {store}",
                    tienda=store,
                    precio_actual=None,
                    precio_minimo=None,
                    precio_objetivo=target_price,
                    umbral_descuento_porcentaje=threshold,
                    activo=active,
                )
                session.add(new_prod)
                added += 1

        await session.flush()
        logger.info(f"Sincronización de URLs finalizada: {added} agregados, {updated} actualizados.")
        return added, updated

