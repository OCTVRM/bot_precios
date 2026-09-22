import argparse
import asyncio
import sys
from typing import Optional
from sqlalchemy import delete, select
from src.config import settings
from src.database import get_db_session, init_db
from src.models import Product
from src.scrapers.registry import get_scraper_for_url
from src.services.notifier import format_clp
from src.services.price_service import PriceTrackingService


async def add_product(
    url: str,
    name: Optional[str] = None,
    target_price: Optional[float] = None,
    threshold: Optional[float] = None,
) -> None:
    """Registra una nueva URL en el sistema para monitoreo continuo."""
    await init_db()
    scraper = get_scraper_for_url(url)
    store = scraper.store_name

    threshold_val = threshold if threshold is not None else settings.DEFAULT_DISCOUNT_THRESHOLD_PERCENT

    print(f"[*] Analizando URL y extrayendo datos preliminares de {store}...")
    scraped_title = name
    initial_price = None

    try:
        item = await scraper.scrape(url)
        scraped_title = name or item.title
        initial_price = item.price
        print(f"[+] Producto detectado: '{scraped_title}' | Precio actual: {format_clp(initial_price)}")
    except Exception as ex:
        print(f"[!] Aviso: No se pudo raspar de inmediato ({ex}). Se guardará para revisión automática.")

    async with get_db_session() as session:
        # Verificar si ya existe
        stmt = select(Product).where(Product.url_original == url)
        existing = (await session.execute(stmt)).scalar_one_or_none()

        if existing:
            print(f"[!] El producto ya se encuentra registrado con ID {existing.id}.")
            return

        new_product = Product(
            url_original=url,
            nombre=scraped_title or "Pendiente de primer escaneo",
            tienda=store,
            precio_actual=initial_price,
            precio_minimo=initial_price,
            precio_objetivo=target_price,
            umbral_descuento_porcentaje=threshold_val,
            activo=True,
        )
        session.add(new_product)
        await session.flush()
        print(
            f"[OK] Producto agregado con éxito!\n"
            f"     ID: {new_product.id}\n"
            f"     Tienda: {new_product.tienda}\n"
            f"     Nombre: {new_product.nombre}\n"
            f"     Precio Inicial: {format_clp(initial_price) if initial_price else '$0'}\n"
            f"     Umbral Descuento: {threshold_val}%\n"
            f"     Precio Objetivo: {format_clp(target_price) if target_price else '$0'}"
        )


async def list_products(category_filter: Optional[str] = None) -> None:
    """Muestra un resumen tabulado de todos los productos en seguimiento."""
    await init_db()
    async with get_db_session() as session:
        stmt = select(Product).order_by(Product.id.asc())
        if category_filter:
            stmt = stmt.where(Product.categoria.ilike(f"%{category_filter}%"))
        products = (await session.execute(stmt)).scalars().all()

        if not products:
            print("[i] No hay productos registrados aún con esos criterios.")
            return

        print("\n" + "=" * 145)
        print(
            f"{'ID':<4} | {'TIENDA':<12} | {'CATEGORÍA':<18} | {'ACT':<4} | {'ACTUAL':<12} | {'MÍNIMO':<12} | {'OBJETIVO':<10} | {'UMBRAL':<7} | {'NOMBRE':<40}"
        )
        print("-" * 145)
        for p in products:
            act_str = "SÍ" if p.activo else "NO"
            curr_str = format_clp(p.precio_actual) if p.precio_actual else "N/D"
            min_str = format_clp(p.precio_minimo) if p.precio_minimo else "N/D"
            tgt_str = format_clp(p.precio_objetivo) if p.precio_objetivo else "-"
            cat_str = (p.categoria[:16] + "..") if p.categoria and len(p.categoria) > 18 else (p.categoria or "Sin categoría")
            name_str = (p.nombre[:37] + "...") if p.nombre and len(p.nombre) > 40 else (p.nombre or "N/D")
            print(
                f"{p.id:<4} | {p.tienda:<12} | {cat_str:<18} | {act_str:<4} | {curr_str:<12} | {min_str:<12} | {tgt_str:<10} | {p.umbral_descuento_porcentaje:>5.1f}% | {name_str:<40}"
            )
        print("=" * 145 + "\n")


async def list_categories() -> None:
    """Lista las categorías configuradas en categories.json y cantidad de productos en BD."""
    await init_db()
    from src.scrapers.category_config import load_categories_catalog
    from sqlalchemy import func

    catalog = load_categories_catalog()
    async with get_db_session() as session:
        stmt = select(Product.categoria, func.count(Product.id)).group_by(Product.categoria)
        counts = dict((await session.execute(stmt)).all())

    print("\n" + "=" * 115)
    print(
        f"{'ID':<22} | {'NOMBRE':<32} | {'UMBRAL':<8} | {'ERROR':<8} | {'TIENDAS':<8} | {'PRODS BD':<8} | {'ACTIVA'}"
    )
    print("-" * 115)
    for cat in catalog.categories:
        prod_count = counts.get(cat.name, 0)
        stores_count = len(cat.stores)
        act_str = "SÍ" if cat.active else "NO"
        print(
            f"{cat.id:<22} | {cat.name[:30]:<32} | {cat.default_threshold_percent:>5.1f}% | {cat.error_threshold_percent:>5.1f}% | {stores_count:>8} | {prod_count:>8} | {act_str}"
        )
    print("=" * 115 + "\n")


async def add_category(
    category_id: str,
    name: str,
    threshold: Optional[float] = None,
    error_threshold: Optional[float] = None,
) -> None:
    """Registra o actualiza una categoría en categories.json."""
    from src.scrapers.category_config import add_or_update_category
    t_val = threshold if threshold is not None else settings.DEFAULT_DISCOUNT_THRESHOLD_PERCENT
    e_val = error_threshold if error_threshold is not None else settings.ERROR_DISCOUNT_THRESHOLD_PERCENT

    cat = add_or_update_category(
        category_id=category_id,
        name=name,
        threshold=t_val,
        error_threshold=e_val,
    )
    print(
        f"[OK] Categoría guardada exitosamente:\n"
        f"     ID: {cat.id}\n"
        f"     Nombre: {cat.name}\n"
        f"     Umbral Descuento: {cat.default_threshold_percent}%\n"
        f"     Umbral Error de Precio: {cat.error_threshold_percent}%"
    )


async def add_category_store(category_id: str, store_id: str, url: str) -> None:
    """Asocia una URL de tienda a una categoría existente."""
    from src.scrapers.category_config import add_store_to_category
    success = add_store_to_category(category_id=category_id, store_id=store_id, url=url)
    if success:
        print(f"[OK] URL de '{store_id}' asociada correctamente a la categoría '{category_id}'.")
    else:
        print(f"[!] No se encontró la categoría con ID '{category_id}'. Usa 'categories' para ver disponibles.")


async def sync_categories_cmd(category_id: Optional[str] = None, max_items: int = 50) -> None:
    """Descubre y sincroniza en la BD los Top productos por categoría."""
    await init_db()
    from src.scrapers.category_scraper import CategoryCrawlerService
    crawler = CategoryCrawlerService()

    async with get_db_session() as session:
        if category_id:
            print(f"[*] Sincronizando Top {max_items} para categoría '{category_id}'...")
            added, updated = await crawler.sync_category(session, category_id, max_products=max_items)
            print(f"[OK] Categoría '{category_id}': {added} agregados, {updated} actualizados.")
        else:
            print(f"[*] Sincronizando Top {max_items} para todas las categorías activas...")
            results = await crawler.sync_all_categories(session, max_products=max_items)
            print("\n[OK] Resumen de sincronización de categorías:")
            for c_id, (added, updated) in results.items():
                print(f"     - {c_id}: +{added} nuevos, {updated} actualizados")


async def toggle_product(product_id: int) -> None:
    """Activa o desactiva el rastreo de un producto por su ID."""
    await init_db()
    async with get_db_session() as session:
        stmt = select(Product).where(Product.id == product_id)
        product = (await session.execute(stmt)).scalar_one_or_none()
        if not product:
            print(f"[!] No se encontró el producto con ID {product_id}.")
            return
        product.activo = not product.activo
        state_str = "ACTIVADO" if product.activo else "DESACTIVADO"
        print(f"[OK] Producto ID {product.id} ha sido {state_str}.")


async def delete_product(product_id: int) -> None:
    """Elimina un producto y su historial de la base de datos."""
    await init_db()
    async with get_db_session() as session:
        stmt = select(Product).where(Product.id == product_id)
        product = (await session.execute(stmt)).scalar_one_or_none()
        if not product:
            print(f"[!] No se encontró el producto con ID {product_id}.")
            return
        await session.delete(product)
        print(f"[OK] Producto ID {product_id} ('{product.nombre}') eliminado exitosamente.")


async def check_now(product_id: Optional[int] = None) -> None:
    """Fuerza una comprobación inmediata de precios y dispara alertas si corresponde."""
    await init_db()
    service = PriceTrackingService()
    async with get_db_session() as session:
        if product_id:
            stmt = select(Product).where(Product.id == product_id)
            products = (await session.execute(stmt)).scalars().all()
        else:
            products = await service.get_active_products(session)

        if not products:
            print("[i] No se encontraron productos para verificar.")
            return

        print(f"[*] Iniciando comprobación manual para {len(products)} producto(s)...")
        for p in products:
            alerted, payload = await service.process_product(session, p)
            if alerted and payload:
                print(f"[ALERTA DISPARADA] {p.nombre} bajó a {format_clp(payload.new_price)} (-{payload.discount_percent:.1f}%)")
            else:
                price_str = format_clp(p.precio_actual) if p.precio_actual is not None else "Sin precio / Error en enlace"
                print(f"[SIN ALERTA] {p.nombre}: {price_str}")


async def sync_urls(file_path: Optional[str] = None) -> None:
    """Sincroniza los productos definidos en el archivo JSON (monitored_urls.json)."""
    await init_db()
    service = PriceTrackingService()
    async with get_db_session() as session:
        added, updated = await service.sync_monitored_urls_file(session, file_path)
        print(f"[OK] Sincronización finalizada: {added} producto(s) agregado(s), {updated} actualizado(s).")


async def test_alert() -> None:
    """Envía una alerta de prueba a Telegram para comprobar conexión, permisos y formato."""
    from src.services.notifier import AlertPayload, TelegramNotifier
    notifier = TelegramNotifier()
    print("[*] Enviando alerta de prueba a Telegram...")
    print(f"    Canal/Chat ID: {settings.TELEGRAM_CHAT_ID}")

    test_payload = AlertPayload(
        title="[PRUEBA] Pokémon TCG 30th Celebration - Elite Trainer Box Inglés",
        store="The Way",
        old_price=159990.0,
        new_price=110000.0,
        discount_percent=31.2,
        affiliate_url="https://www.theway.cl/preventa-16092026-pokemon-tcg-30th-celebration-elite-trainer-box-ingles",
        is_all_time_low=True,
        target_price=120000.0,
        image_url="https://cdnx.jumpseller.com/pokemaniacos/image/78411732/resize/1200/1200?1782930476",
    )

    try:
        success = await notifier.send_alert(test_payload)
        if success:
            print("[OK] ¡Alerta de prueba enviada con éxito a Telegram!")
            print("     Revisa tu canal/grupo para ver la foto, formato y botón de la oferta.")
        else:
            print("[!] La función retornó False. Revisa los logs.")
    except Exception as ex:
        print(f"[ERROR] Falló el envío a Telegram: {ex}")
    finally:
        await notifier.close()


async def simulate_price_drop(product_id: int, new_price: float) -> None:
    """Simula una caída de precio artificial en un producto para probar la alerta en tiempo real."""
    await init_db()
    service = PriceTrackingService()
    async with get_db_session() as session:
        stmt = select(Product).where(Product.id == product_id)
        product = (await session.execute(stmt)).scalar_one_or_none()
        if not product:
            print(f"[!] No se encontró el producto con ID {product_id}.")
            return

        old_price = product.precio_actual or (new_price * 1.35)
        reduction = old_price - new_price
        discount_percent = (reduction / old_price) * 100

        print(f"[*] Simulando caída de precio para ID {product.id} ('{product.nombre}')...")
        print(f"    Precio Anterior: {format_clp(old_price)}")
        print(f"    Nuevo Precio Simulado: {format_clp(new_price)} (-{discount_percent:.1f}%)")

        from src.services.notifier import AlertPayload
        payload = AlertPayload(
            title=product.nombre or "Producto en Oferta",
            store=product.tienda,
            old_price=old_price,
            new_price=new_price,
            discount_percent=discount_percent,
            affiliate_url=product.url_original,
            is_all_time_low=True,
            target_price=product.precio_objetivo,
        )

        sent = await service.notifier.send_alert(payload)
        if sent:
            print("[OK] ¡Alerta de oferta enviada a Telegram exitosamente!")
        else:
            print("[ERROR] No se pudo enviar la alerta a Telegram.")
        await service.notifier.close()


def main() -> None:
    """Punto de entrada de la línea de comandos (CLI)."""
    parser = argparse.ArgumentParser(
        description="Gestor de Productos y Alertas de Precio",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Comando a ejecutar")

    # Subcomando: add
    add_parser = subparsers.add_parser("add", help="Agregar un nuevo producto para monitoreo")
    add_parser.add_argument("--url", required=True, help="URL original del producto (Amazon, Mercado Libre, etc.)")
    add_parser.add_argument("--name", required=False, default=None, help="Nombre o alias personalizado")
    add_parser.add_argument("--target-price", type=float, required=False, default=None, help="Precio objetivo deseado")
    add_parser.add_argument(
        "--threshold",
        type=float,
        required=False,
        default=None,
        help=f"Porcentaje de descuento para alertar (por defecto: {settings.DEFAULT_DISCOUNT_THRESHOLD_PERCENT}%%)",
    )

    # Subcomando: sync
    sync_parser = subparsers.add_parser("sync", help="Sincronizar productos desde monitored_urls.json")
    sync_parser.add_argument("--file", required=False, default=None, help="Ruta opcional al archivo JSON")

    # Subcomando: list
    list_parser = subparsers.add_parser("list", help="Listar todos los productos registrados")
    list_parser.add_argument("--category", required=False, default=None, help="Filtrar por categoría")

    # Subcomando: check
    check_parser = subparsers.add_parser("check", help="Ejecutar comprobación manual inmediata")
    check_parser.add_argument("--id", type=int, required=False, default=None, help="ID específico a comprobar")

    # Subcomando: toggle
    toggle_parser = subparsers.add_parser("toggle", help="Activar o pausar el monitoreo de un producto")
    toggle_parser.add_argument("--id", type=int, required=True, help="ID del producto")

    # Subcomando: delete
    delete_parser = subparsers.add_parser("delete", help="Eliminar un producto del sistema")
    delete_parser.add_argument("--id", type=int, required=True, help="ID del producto")

    # Subcomando: categories (listar)
    subparsers.add_parser("categories", help="Listar todas las categorías configuradas y sus tiendas")

    # Subcomando: add-category
    add_cat_parser = subparsers.add_parser("add-category", help="Crear o actualizar una categoría en categories.json")
    add_cat_parser.add_argument("--id", required=True, help="Identificador único (ej: 'consolas')")
    add_cat_parser.add_argument("--name", required=True, help="Nombre descriptivo (ej: 'Consolas y Videojuegos')")
    add_cat_parser.add_argument(
        "--threshold", type=float, required=False, default=None, help="Umbral de descuento sugerido (ej: 15.0)"
    )
    add_cat_parser.add_argument(
        "--error-threshold", type=float, required=False, default=None, help="Umbral de error de precio (ej: 50.0)"
    )

    # Subcomando: add-category-store
    add_store_parser = subparsers.add_parser(
        "add-category-store", help="Asociar la URL de una tienda a una categoría existente"
    )
    add_store_parser.add_argument("--category", required=True, help="ID de la categoría existente")
    add_store_parser.add_argument("--store", required=True, help="ID de la tienda (ej: falabella, paris, ripley)")
    add_store_parser.add_argument("--url", required=True, help="URL del listado o más vendidos de la tienda")

    # Subcomando: sync-categories
    sync_cat_parser = subparsers.add_parser(
        "sync-categories", help="Escanear y registrar productos por categoría en la BD"
    )
    sync_cat_parser.add_argument("--category", required=False, default=None, help="ID de categoría específica a sincronizar")
    sync_cat_parser.add_argument(
        "--max", type=int, required=False, default=50, help="Cantidad máxima de productos por tienda (por defecto 50)"
    )

    # Subcomando: test-alert
    subparsers.add_parser("test-alert", help="Enviar una alerta de prueba a Telegram")

    # Subcomando: simulate
    sim_parser = subparsers.add_parser("simulate", help="Simular una caída de precio y disparar alerta")
    sim_parser.add_argument("--id", type=int, required=True, help="ID del producto")
    sim_parser.add_argument("--price", type=float, required=True, help="Nuevo precio menor en CLP")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "add":
        asyncio.run(add_product(args.url, args.name, args.target_price, args.threshold))
    elif args.command == "sync":
        asyncio.run(sync_urls(args.file))
    elif args.command == "list":
        asyncio.run(list_products(args.category))
    elif args.command == "check":
        asyncio.run(check_now(args.id))
    elif args.command == "toggle":
        asyncio.run(toggle_product(args.id))
    elif args.command == "delete":
        asyncio.run(delete_product(args.id))
    elif args.command == "categories":
        asyncio.run(list_categories())
    elif args.command == "add-category":
        asyncio.run(add_category(args.id, args.name, args.threshold, args.error_threshold))
    elif args.command == "add-category-store":
        asyncio.run(add_category_store(args.category, args.store, args.url))
    elif args.command == "sync-categories":
        asyncio.run(sync_categories_cmd(args.category, args.max))
    elif args.command == "test-alert":
        asyncio.run(test_alert())
    elif args.command == "simulate":
        asyncio.run(simulate_price_drop(args.id, args.price))


if __name__ == "__main__":
    main()
