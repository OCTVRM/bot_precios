import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from src.config import settings
from src.database import get_db_session
from src.services.price_service import PriceTrackingService

logger = logging.getLogger(__name__)


class PriceTrackerScheduler:
    """Orquestador de tareas programadas con APScheduler y control de concurrencia."""

    def __init__(self, price_service: PriceTrackingService = None):
        self.scheduler = AsyncIOScheduler()
        self.price_service = price_service or PriceTrackingService()
        self._is_running_job = False
        self._is_running_category_sync = False
        self._semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_SCRAPES)

    async def run_check_cycle(self) -> None:
        """
        Ejecuta un ciclo completo de verificación de todos los productos activos.
        Evita ejecuciones solapadas y utiliza un esquema de cola acotada para mantener
        un uso de memoria mínimo y constante (ideal para contenedores de 512MB como Render).
        """
        if self._is_running_job or self._is_running_category_sync:
            logger.warning("Ciclo de monitoreo o sincronización ya en ejecución. Omitiendo turno para proteger RAM.")
            return

        self._is_running_job = True
        logger.info("=== Iniciando ciclo de verificación de precios ===")

        try:
            import gc
            from src.models import Product

            # 1. Consultar únicamente IDs en lugar de cargar miles de modelos ORM completos en RAM
            async with get_db_session() as session:
                product_ids = await self.price_service.get_active_product_ids(session)
                logger.info(f"Se encontraron {len(product_ids)} productos activos para monitorear.")

            if not product_ids:
                return

            # 2. Cola acotada de trabajo: evita instanciar miles de corrutinas en asyncio.gather
            queue: asyncio.Queue[int] = asyncio.Queue()
            for pid in product_ids:
                queue.put_nowait(pid)

            processed_count = 0

            async def worker():
                nonlocal processed_count
                while not queue.empty():
                    try:
                        product_id = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    try:
                        await asyncio.sleep(0.5)
                        async with get_db_session() as item_session:
                            prod = await item_session.get(Product, product_id)
                            if prod and prod.activo:
                                await self.price_service.process_product(item_session, prod)
                                await item_session.commit()
                    except Exception as ex:
                        logger.error(f"Error procesando producto ID {product_id}: {ex}")
                    finally:
                        queue.task_done()
                        processed_count += 1
                        # Liberar periódicamente memoria y forzar recolección de basura cada 50 productos
                        if processed_count % 50 == 0:
                            gc.collect()

            # Lanzar workers con concurrencia estrictamente controlada
            num_workers = min(settings.MAX_CONCURRENT_SCRAPES, len(product_ids))
            workers = [asyncio.create_task(worker()) for _ in range(num_workers)]
            await asyncio.gather(*workers)
            gc.collect()

            logger.info("=== Ciclo de verificación finalizado exitosamente ===")
        except Exception as ex:
            logger.error(f"Error crítico durante el ciclo de verificación: {ex}", exc_info=True)
        finally:
            self._is_running_job = False

    async def run_category_sync_cycle(self) -> None:
        """Sincroniza periódicamente los Top productos de cada categoría en la base de datos."""
        if self._is_running_category_sync or self._is_running_job:
            logger.warning("Otro ciclo se encuentra en ejecución. Postergando sincronización de categorías para evitar sobrecarga de memoria.")
            return

        self._is_running_category_sync = True
        logger.info("=== Iniciando ciclo programado de sincronización de categorías ===")
        try:
            from src.scrapers.category_scraper import CategoryCrawlerService
            crawler = CategoryCrawlerService()
            async with get_db_session() as session:
                results = await crawler.sync_all_categories(
                    session, max_products=settings.CATEGORY_PRODUCTS_LIMIT
                )
                total_added = sum(r[0] for r in results.values())
                total_updated = sum(r[1] for r in results.values())
                logger.info(
                    f"Sincronización de categorías finalizada: {total_added} nuevos productos, {total_updated} actualizados."
                )
        except Exception as ex:
            logger.error(f"Error en ciclo programado de categorías: {ex}", exc_info=True)
        finally:
            self._is_running_category_sync = False

    async def run_subscription_check_cycle(self) -> None:
        """Verifica vencimientos de membresías VIP, envía recordatorios y desactiva accesos caducados."""
        from datetime import datetime, timezone, timedelta
        from src.models import Subscription
        from src.bot import notifier

        logger.info("=== Verificando vencimientos de suscripciones VIP ===")
        now = datetime.now(timezone.utc)
        two_days_ahead = now + timedelta(days=2)

        try:
            async with get_db_session() as session:
                from sqlalchemy import select
                stmt = select(Subscription).where(Subscription.activo.is_(True))
                res = await session.execute(stmt)
                active_subs = res.scalars().all()

                bot = notifier.bot
                channel_id = settings.TELEGRAM_CHAT_ID

                for sub in active_subs:
                    try:
                        # 1. Caso: Suscripción vencida
                        if sub.fecha_fin <= now:
                            logger.info(f"Suscripción vencida para usuario {sub.telegram_user_id} (@{sub.telegram_username})")
                            sub.activo = False
                            
                            # Remover del canal si está configurado
                            if channel_id:
                                try:
                                    await bot.ban_chat_member(chat_id=channel_id, user_id=sub.telegram_user_id)
                                    await bot.unban_chat_member(chat_id=channel_id, user_id=sub.telegram_user_id)
                                except Exception as e_kick:
                                    logger.warning(f"No se pudo remover a {sub.telegram_user_id} del canal: {e_kick}")

                            # Avisar al usuario
                            try:
                                await bot.send_message(
                                    chat_id=sub.telegram_user_id,
                                    text=(
                                        "⚠️ <b>Tu Membresía VIP ha Vencido</b>\n\n"
                                        "Tu acceso al canal de ofertas ha finalizado. Para reactivar tu membresía mensual "
                                        f"de <b>${settings.SUBSCRIPTION_PRICE_CLP:,.0f} CLP</b>, solicita unirte nuevamente "
                                        "mediante el enlace de invitación y envía tu comprobante a este chat.\n\n"
                                        "¡Esperamos verte pronto de regreso!"
                                    ),
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass

                        # 2. Caso: Por vencer en menos de 48 horas
                        elif sub.fecha_fin <= two_days_ahead and not sub.notificado_vencimiento:
                            logger.info(f"Enviando aviso de vencimiento próximo a usuario {sub.telegram_user_id}")
                            sub.notificado_vencimiento = True
                            try:
                                await bot.send_message(
                                    chat_id=sub.telegram_user_id,
                                    text=(
                                        "⏰ <b>Recordatorio de Renovación VIP</b>\n\n"
                                        f"Tu membresía vence el <b>{sub.fecha_fin.strftime('%d/%m/%Y')}</b> (en menos de 48 horas).\n\n"
                                        "Para mantener tu acceso continuo a las ofertas y errores de precio, "
                                        f"realiza tu transferencia de <b>${settings.SUBSCRIPTION_PRICE_CLP:,.0f} CLP</b> y envía la captura aquí.\n\n"
                                        f"{settings.BANK_TRANSFER_DETAILS}"
                                    ),
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass

                    except Exception as e_user:
                        logger.error(f"Error procesando vencimiento para usuario {sub.telegram_user_id}: {e_user}")

                await session.commit()
            logger.info("=== Verificación de suscripciones finalizada ===")
        except Exception as ex:
            logger.error(f"Error en ciclo de verificación de suscripciones: {ex}", exc_info=True)

    def start(self) -> None:
        """Configura e inicia el programador de tareas periódicas."""
        price_trigger = IntervalTrigger(minutes=settings.CHECK_INTERVAL_MINUTES)
        self.scheduler.add_job(
            self.run_check_cycle,
            trigger=price_trigger,
            id="price_check_job",
            name="Rastreo periódico de precios de productos",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

        category_trigger = IntervalTrigger(hours=settings.CATEGORY_SYNC_INTERVAL_HOURS)
        self.scheduler.add_job(
            self.run_category_sync_cycle,
            trigger=category_trigger,
            id="category_sync_job",
            name="Descubrimiento y sincronización periódica de categorías Top 10",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

        sub_trigger = IntervalTrigger(hours=12)
        self.scheduler.add_job(
            self.run_subscription_check_cycle,
            trigger=sub_trigger,
            id="subscription_check_job",
            name="Control y expiración de suscripciones VIP",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

        self.scheduler.start()
        logger.info(
            f"Scheduler iniciado. Precios cada {settings.CHECK_INTERVAL_MINUTES}m, "
            f"categorías cada {settings.CATEGORY_SYNC_INTERVAL_HOURS}h, "
            f"suscripciones cada 12h."
        )

    def shutdown(self) -> None:
        """Detiene el programador de forma ordenada."""
        if self.scheduler.running:
            logger.info("Deteniendo scheduler de tareas...")
            self.scheduler.shutdown(wait=False)
