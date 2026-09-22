import datetime
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base
from src.models import PriceHistory, Product
from src.scrapers.base import ScrapedItem
from src.services.price_service import PriceTrackingService
from src.services.notifier import TelegramNotifier


@pytest_asyncio.fixture
async def test_session():
    """Crea una base de datos SQLite en memoria aislada para cada test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_price_drop_triggers_alert(test_session: AsyncSession):
    """Verifica que una caída superior al umbral dispare la alerta y guarde el histórico."""
    mock_notifier = TelegramNotifier()
    mock_notifier.send_alert = AsyncMock(return_value=True)

    service = PriceTrackingService(notifier=mock_notifier)

    # Producto inicial con precio $1000 y umbral de 10%
    product = Product(
        url_original="https://www.amazon.es/dp/B08TEST",
        nombre="Teclado Mecánico",
        tienda="Amazon",
        precio_actual=1000.0,
        precio_minimo=1000.0,
        precio_objetivo=None,
        umbral_descuento_porcentaje=10.0,
        activo=True,
    )
    test_session.add(product)
    await test_session.flush()

    # Simulamos que el scraper detecta una bajada a $850 (15% de descuento)
    mock_item = ScrapedItem(
        title="Teclado Mecánico",
        price=850.0,
        affiliate_url="https://www.amazon.es/dp/B08TEST?tag=afiliado-21",
    )

    with patch("src.services.price_service.get_scraper_for_url") as mock_get_scraper:
        mock_scraper = AsyncMock()
        mock_scraper.scrape = AsyncMock(return_value=mock_item)
        mock_get_scraper.return_value = mock_scraper

        alerted, payload = await service.process_product(test_session, product)

    assert alerted is True
    assert payload is not None
    assert payload.old_price == 1000.0
    assert payload.new_price == 850.0
    assert payload.discount_percent == 15.0
    assert product.precio_actual == 850.0
    assert product.precio_minimo == 850.0
    assert product.ultima_alerta_en is not None

    # Verificar registro en histórico
    stmt = select(PriceHistory).where(PriceHistory.product_id == product.id)
    history = (await test_session.execute(stmt)).scalars().all()
    assert len(history) == 1
    assert history[0].precio == 850.0
    mock_notifier.send_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_price_drop_below_threshold_does_not_alert(test_session: AsyncSession):
    """Verifica que una caída inferior al umbral NO dispare alerta pero SÍ actualice el precio."""
    mock_notifier = TelegramNotifier()
    mock_notifier.send_alert = AsyncMock(return_value=True)

    service = PriceTrackingService(notifier=mock_notifier)

    product = Product(
        url_original="https://www.amazon.es/dp/B08TEST2",
        nombre="Mouse Gamer",
        tienda="Amazon",
        precio_actual=100.0,
        precio_minimo=100.0,
        precio_objetivo=None,
        umbral_descuento_porcentaje=10.0,  # Requiere al menos 10%
        activo=True,
    )
    test_session.add(product)
    await test_session.flush()

    # Baja a $95 (5% de descuento, no alcanza el 10%)
    mock_item = ScrapedItem(title="Mouse Gamer", price=95.0)

    with patch("src.services.price_service.get_scraper_for_url") as mock_get_scraper:
        mock_scraper = AsyncMock()
        mock_scraper.scrape = AsyncMock(return_value=mock_item)
        mock_get_scraper.return_value = mock_scraper

        alerted, payload = await service.process_product(test_session, product)

    assert alerted is False
    assert payload is None
    assert product.precio_actual == 95.0
    mock_notifier.send_alert.assert_not_called()


@pytest.mark.asyncio
async def test_target_price_triggers_alert(test_session: AsyncSession):
    """Verifica que si se alcanza el precio objetivo se alerte aunque el % sea menor al umbral."""
    mock_notifier = TelegramNotifier()
    mock_notifier.send_alert = AsyncMock(return_value=True)

    service = PriceTrackingService(notifier=mock_notifier)

    product = Product(
        url_original="https://articulo.mercadolibre.com.ar/MLA-TEST",
        nombre="Auriculares Bluetooth",
        tienda="Mercado Libre",
        precio_actual=500.0,
        precio_minimo=500.0,
        precio_objetivo=480.0,  # Objetivo configurado
        umbral_descuento_porcentaje=20.0,  # 20% no se cumple
        activo=True,
    )
    test_session.add(product)
    await test_session.flush()

    # Baja a $475 (baja 5%, no llega a 20%, pero cumple target <= 480)
    mock_item = ScrapedItem(title="Auriculares Bluetooth", price=475.0)

    with patch("src.services.price_service.get_scraper_for_url") as mock_get_scraper:
        mock_scraper = AsyncMock()
        mock_scraper.scrape = AsyncMock(return_value=mock_item)
        mock_get_scraper.return_value = mock_scraper

        alerted, payload = await service.process_product(test_session, product)

    assert alerted is True
    assert payload.new_price == 475.0
    mock_notifier.send_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_spam_cooldown_behavior(test_session: AsyncSession):
    """Verifica que dentro del cooldown no se repita alerta, salvo que sea nuevo mínimo histórico."""
    mock_notifier = TelegramNotifier()
    mock_notifier.send_alert = AsyncMock(return_value=True)

    service = PriceTrackingService(notifier=mock_notifier)

    now = datetime.datetime.now(datetime.timezone.utc)
    # Alerta previa enviada hace solo 1 hora (cooldown es 6h)
    product = Product(
        url_original="https://www.amazon.es/dp/B08COOLDOWN",
        nombre="Tablet Pro",
        tienda="Amazon",
        precio_actual=900.0,
        precio_minimo=800.0,  # Mínimo histórico previo es 800
        umbral_descuento_porcentaje=5.0,
        activo=True,
        ultima_alerta_en=now - datetime.timedelta(hours=1),
    )
    test_session.add(product)
    await test_session.flush()

    # Caso 1: Precio baja a $850 (descuento del 5.5% vs 900, pero NO es < 800 mínimo histórico)
    mock_item_1 = ScrapedItem(title="Tablet Pro", price=850.0)
    with patch("src.services.price_service.get_scraper_for_url") as mock_get_scraper:
        mock_scraper = AsyncMock()
        mock_scraper.scrape = AsyncMock(return_value=mock_item_1)
        mock_get_scraper.return_value = mock_scraper

        alerted, payload = await service.process_product(test_session, product)

    # Debe ser retenido por cooldown
    assert alerted is False
    assert payload is None
    mock_notifier.send_alert.assert_not_called()

    # Caso 2: Precio se desploma a $750 (< 800, rompe mínimo histórico durante cooldown)
    mock_item_2 = ScrapedItem(title="Tablet Pro", price=750.0)
    with patch("src.services.price_service.get_scraper_for_url") as mock_get_scraper:
        mock_scraper = AsyncMock()
        mock_scraper.scrape = AsyncMock(return_value=mock_item_2)
        mock_get_scraper.return_value = mock_scraper

        alerted, payload = await service.process_product(test_session, product)

    # Debe disparar alerta inmediata por mínimo histórico
    assert alerted is True
    assert payload.is_all_time_low is True
    assert payload.new_price == 750.0
    mock_notifier.send_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_price_error_detection(test_session: AsyncSession):
    """Verifica que caídas de precio >= 50% marquen la alerta como is_price_error."""
    mock_notifier = TelegramNotifier()
    mock_notifier.send_alert = AsyncMock(return_value=True)

    service = PriceTrackingService(notifier=mock_notifier)

    product = Product(
        url_original="https://www.falabella.com/p/tv-glitch",
        nombre="Smart TV 75 4K",
        tienda="Falabella",
        categoria="Televisores y Smart TV",
        precio_actual=800000.0,
        precio_minimo=800000.0,
        umbral_descuento_porcentaje=15.0,
        activo=True,
    )
    test_session.add(product)
    await test_session.flush()

    # Cae de 800.000 a 200.000 (75% de descuento, glitch de precio)
    mock_item = ScrapedItem(
        title="Smart TV 75 4K",
        price=200000.0,
        affiliate_url="https://www.falabella.com/p/tv-glitch?aff_source=tag",
    )

    with patch("src.services.price_service.get_scraper_for_url") as mock_get_scraper:
        mock_scraper = AsyncMock()
        mock_scraper.scrape = AsyncMock(return_value=mock_item)
        mock_get_scraper.return_value = mock_scraper

        alerted, payload = await service.process_product(test_session, product)

    assert alerted is True
    assert payload is not None
    assert payload.discount_percent == 75.0
    assert payload.is_price_error is True
    assert payload.category == "Televisores y Smart TV"


@pytest.mark.asyncio
async def test_catalog_discount_on_new_product(test_session: AsyncSession):
    """Verifica que un producto nuevo con 50% de descuento de catálogo (precio lista vs oferta) dispare alerta inmediata."""
    mock_notifier = TelegramNotifier()
    mock_notifier.send_alert = AsyncMock(return_value=True)

    service = PriceTrackingService(notifier=mock_notifier)

    # Producto recién agregado sin alertas previas
    product = Product(
        url_original="https://www.sodimac.cl/sodimac-cl/articulo/154322144/sofa-2-cuerpos-chestrfield/154322149",
        nombre="Sofa 2 Cuerpos Chesterfield",
        tienda="Sodimac",
        precio_actual=250000.0,
        precio_minimo=250000.0,
        umbral_descuento_porcentaje=20.0,
        activo=True,
        ultima_alerta_en=None,
    )
    test_session.add(product)
    await test_session.flush()

    # Scraper detecta que el precio oferta es 250.000 y el precio lista normal es 500.000 (50% OFF)
    mock_item = ScrapedItem(
        title="Sofa 2 Cuerpos Chesterfield",
        price=250000.0,
        normal_price=500000.0,
        discount_percent=50.0,
        affiliate_url="https://www.sodimac.cl/sodimac-cl/articulo/154322144/sofa-2-cuerpos-chestrfield/154322149?aff_source=tag",
    )

    with patch("src.services.price_service.get_scraper_for_url") as mock_get_scraper:
        mock_scraper = AsyncMock()
        mock_scraper.scrape = AsyncMock(return_value=mock_item)
        mock_get_scraper.return_value = mock_scraper

        alerted, payload = await service.process_product(test_session, product)

    assert alerted is True
    assert payload is not None
    assert payload.new_price == 250000.0
    assert payload.old_price == 500000.0
    assert payload.discount_percent == 50.0
    assert payload.is_price_error is True
    mock_notifier.send_alert.assert_called_once()


