import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base
from src.models import Product
from src.scrapers.configurable import ConfigurableScraper
from src.scrapers.registry import get_scraper_for_url
from src.scrapers.store_config import find_store_rule_for_url, load_store_catalog
from src.services.price_service import PriceTrackingService


@pytest_asyncio.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


def test_stores_catalog_loading():
    """Verifica que stores.json contenga todas las tiendas solicitadas."""
    catalog = load_store_catalog(force_reload=True)
    store_ids = {s.id for s in catalog.stores}

    expected_stores = {
        "falabella",
        "hites",
        "abc",
        "paris",
        "ripley",
        "entel",
        "easy",
        "sodimac",
        "amazon",
        "mercadolibre",
    }
    assert expected_stores.issubset(store_ids)


def test_domain_matching_for_all_requested_stores():
    """Verifica que las URLs de las tiendas especificadas se mapeen a su regla correspondiente."""
    test_urls = [
        ("https://www.falabella.com/falabella-cl/product/16843454", "Falabella"),
        ("https://www.hites.com/consola-sony-playstation-5-919507001.html", "Hites"),
        ("https://www.abc.cl/telefonia/celulares/iphone-15.html", "ABC / Abcdin"),
        ("https://www.paris.cl/consola-playstation-5-536418999.html", "Paris"),
        ("https://simple.ripley.cl/consola-playstation-5-2000398417932p", "Ripley"),
        ("https://www.entel.cl/equipos/apple/iphone-15/", "Entel"),
        ("https://www.easy.cl/taladro-percutor-1102928/p", "Easy"),
        ("https://www.sodimac.cl/sodimac-cl/product/110292837", "Sodimac"),
    ]

    for url, expected_name in test_urls:
        rule = find_store_rule_for_url(url)
        assert rule is not None, f"No se encontró regla para {url}"
        assert rule.name == expected_name
        scraper = get_scraper_for_url(url)
        assert scraper.store_name == expected_name


@pytest.mark.asyncio
async def test_configurable_scraper_json_ld():
    """Verifica extracción vía Schema.org JSON-LD en ConfigurableScraper."""
    rule = find_store_rule_for_url("https://www.falabella.com/falabella-cl/product/123")
    scraper = ConfigurableScraper(rule)

    html_content = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org/",
          "@type": "Product",
          "name": "Smart TV Samsung 55 UHD",
          "image": "https://falabella.scene7.com/is/image/tv.jpg",
          "offers": {
            "@type": "Offer",
            "price": "299990",
            "priceCurrency": "CLP",
            "availability": "https://schema.org/InStock"
          }
        }
        </script>
      </head>
      <body></body>
    </html>
    """

    with patch.object(scraper, "fetch_html", return_value=html_content):
        item = await scraper.scrape("https://www.falabella.com/falabella-cl/product/123")

    assert item.title == "Smart TV Samsung 55 UHD"
    assert item.price == 299990.0
    assert item.in_stock is True
    assert item.image_url == "https://falabella.scene7.com/is/image/tv.jpg"


@pytest.mark.asyncio
async def test_configurable_scraper_next_data():
    """Verifica extracción vía __NEXT_DATA__ (Next.js) común en tiendas modernas."""
    rule = find_store_rule_for_url("https://simple.ripley.cl/item-test")
    scraper = ConfigurableScraper(rule)

    html_content = """
    <html>
      <head>
        <script id="__NEXT_DATA__" type="application/json">
        {
          "props": {
            "pageProps": {
              "product": {
                "name": "Consola PS5 Slim",
                "price": 499990
              }
            }
          }
        }
        </script>
      </head>
      <body></body>
    </html>
    """

    with patch.object(scraper, "fetch_html", return_value=html_content):
        item = await scraper.scrape("https://simple.ripley.cl/item-test")

    assert item.title == "Consola PS5 Slim"
    assert item.price == 499990.0


@pytest.mark.asyncio
async def test_configurable_scraper_css_selectors():
    """Verifica extracción mediante selectores CSS de stores.json."""
    rule = find_store_rule_for_url("https://www.paris.cl/item-test")
    scraper = ConfigurableScraper(rule)

    html_content = """
    <html>
      <body>
        <h1 class="pdp-title">Cafetera Espresso Italiana</h1>
        <div class="default-price">
          <span class="price">$ 49.990</span>
        </div>
      </body>
    </html>
    """

    with patch.object(scraper, "fetch_html", return_value=html_content):
        item = await scraper.scrape("https://www.paris.cl/item-test")

    assert item.title == "Cafetera Espresso Italiana"
    assert item.price == 49990.0


@pytest.mark.asyncio
async def test_sync_monitored_urls(test_session: AsyncSession, tmp_path):
    """Verifica que el archivo monitored_urls.json se sincronice correctamente en la base de datos."""
    json_file = tmp_path / "custom_urls.json"
    json_file.write_text(
        """
        [
          {
            "url": "https://www.falabella.com/falabella-cl/product/101",
            "name": "Consola Falabella",
            "target_price": 450000,
            "threshold_percent": 12.0
          },
          {
            "url": "https://www.paris.cl/product/202",
            "name": "Notebook Paris",
            "target_price": 800000
          }
        ]
        """,
        encoding="utf-8",
    )

    service = PriceTrackingService()
    added, updated = await service.sync_monitored_urls_file(test_session, str(json_file))

    assert added == 2
    assert updated == 0

    stmt = select(Product).order_by(Product.id.asc())
    products = (await test_session.execute(stmt)).scalars().all()
    assert len(products) == 2
    assert products[0].tienda == "Falabella"
    assert products[0].nombre == "Consola Falabella"
    assert products[0].precio_objetivo == 450000.0
    assert products[0].umbral_descuento_porcentaje == 12.0
    assert products[1].tienda == "Paris"
    assert products[1].nombre == "Notebook Paris"

    # Segunda sincronización con modificación de metadatos
    json_file.write_text(
        """
        [
          {
            "url": "https://www.falabella.com/falabella-cl/product/101",
            "name": "Consola Falabella Renombrada",
            "target_price": 420000,
            "threshold_percent": 15.0
          }
        ]
        """,
        encoding="utf-8",
    )

    added2, updated2 = await service.sync_monitored_urls_file(test_session, str(json_file))
    assert added2 == 0
    assert updated2 == 1

    stmt = select(Product).where(Product.url_original == "https://www.falabella.com/falabella-cl/product/101")
    p1 = (await test_session.execute(stmt)).scalar_one()
    assert p1.nombre == "Consola Falabella Renombrada"
    assert p1.precio_objetivo == 420000.0
    assert p1.umbral_descuento_porcentaje == 15.0
