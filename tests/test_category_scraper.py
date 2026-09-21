import json
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.database import Base
from src.models import Product
from src.scrapers.category_config import (
    CategoriesCatalog,
    CategoryItem,
    find_category_by_id,
    load_categories_catalog,
)
from src.scrapers.category_scraper import CategoryCrawlerService, CategoryScraper


def test_load_categories_catalog():
    """Verifica que el catálogo de categorías cargue todas las categorías preconfiguradas."""
    catalog = load_categories_catalog(force_reload=True)
    assert len(catalog.categories) >= 14
    ids = [c.id for c in catalog.categories]
    assert "celulares" in ids
    assert "televisores" in ids
    assert "computadores" in ids
    assert "notebook" in ids
    assert "electrodomesticos" in ids
    assert "pokemon_tcg" in ids
    assert "consolas_videojuegos" in ids


def test_find_category_by_id():
    """Verifica la búsqueda insensible a mayúsculas de una categoría."""
    cat = find_category_by_id("CELULARES")
    assert cat is not None
    assert cat.id == "celulares"
    assert "falabella" in cat.stores
    assert "mercadolibre" in cat.stores


def test_parse_category_page_json_ld():
    """Verifica la extracción de productos desde bloques JSON-LD ItemList."""
    scraper = CategoryScraper()
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "ItemList",
          "itemListElement": [
            {
              "@type": "ListItem",
              "position": 1,
              "url": "/producto-1",
              "name": "Celular Galaxy S24"
            },
            {
              "@type": "ListItem",
              "position": 2,
              "url": "/producto-2",
              "name": "iPhone 15 Pro"
            }
          ]
        }
        </script>
      </head>
      <body></body>
    </html>
    """
    products = scraper.parse_category_page(html, "https://tienda.cl/categoria", max_items=10)
    assert len(products) == 2
    assert products[0].url == "https://tienda.cl/producto-1"
    assert products[0].title == "Celular Galaxy S24"
    assert products[1].url == "https://tienda.cl/producto-2"


def test_parse_category_page_next_data():
    """Verifica la extracción de productos desde __NEXT_DATA__."""
    scraper = CategoryScraper()
    next_data = {
        "props": {
            "pageProps": {
                "products": [
                    {
                        "productId": "12345",
                        "displayName": "Notebook Gamer Asus TUF",
                        "url": "/falabella-cl/product/12345",
                        "prices": [{"price": "899.990"}],
                    },
                    {
                        "productId": "67890",
                        "displayName": "MacBook Air M2",
                        "url": "/falabella-cl/product/67890",
                        "prices": [{"price": "999.990"}],
                    },
                ]
            }
        }
    }
    html = f"""
    <html>
      <body>
        <script id="__NEXT_DATA__" type="application/json">
          {json.dumps(next_data)}
        </script>
      </body>
    </html>
    """
    products = scraper.parse_category_page(
        html, "https://www.falabella.com/falabella-cl/category/cat5860031/Notebooks", store_id="falabella"
    )
    assert len(products) == 2
    assert products[0].title == "Notebook Gamer Asus TUF"
    assert products[0].price == 899990.0
    assert products[0].url == "https://www.falabella.com/falabella-cl/product/12345"


def test_parse_category_page_css_mercadolibre():
    """Verifica la extracción de productos usando selectores CSS configurados para Mercado Libre."""
    scraper = CategoryScraper()
    html = """
    <html>
      <body>
        <li class="ui-search-layout__item">
          <a class="ui-search-link" href="https://articulo.mercadolibre.cl/MLC-1234-pokemon-etb">
            <h2 class="ui-search-item__title">Pokemon TCG Elite Trainer Box</h2>
          </a>
          <div class="ui-search-price__second-line">
            <span class="andes-money-amount__fraction">65.000</span>
          </div>
        </li>
        <li class="ui-search-layout__item">
          <a class="ui-search-link" href="https://articulo.mercadolibre.cl/MLC-5678-booster-box">
            <h2 class="ui-search-item__title">Pokemon Booster Box 36 Sobres</h2>
          </a>
          <div class="ui-search-price__second-line">
            <span class="andes-money-amount__fraction">145.000</span>
          </div>
        </li>
      </body>
    </html>
    """
    products = scraper.parse_category_page(
        html, "https://listado.mercadolibre.cl/pokemon-tcg", store_id="mercadolibre"
    )
    assert len(products) == 2
    assert products[0].title == "Pokemon TCG Elite Trainer Box"
    assert products[0].price == 65000.0
    assert products[0].url == "https://articulo.mercadolibre.cl/MLC-1234-pokemon-etb"


@pytest.mark.asyncio
async def test_crawler_sync_category_db():
    """Verifica que el servicio CategoryCrawlerService inserte los productos en la base de datos."""
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    mock_scraper = CategoryScraper()
    mock_html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "ItemList",
          "itemListElement": [
            {
              "@type": "ListItem",
              "position": 1,
              "url": "https://www.theway.cl/pokemon-etb",
              "name": "ETB Destinos Brillantes"
            }
          ]
        }
        </script>
      </head>
      <body></body>
    </html>
    """
    mock_scraper.fetch_html = AsyncMock(return_value=mock_html)
    crawler = CategoryCrawlerService(scraper=mock_scraper)

    cat_item = CategoryItem(
        id="pokemon_tcg",
        name="Pokémon TCG",
        default_threshold_percent=15.0,
        stores={"theway": "https://www.theway.cl/pokemon-tcg"},
    )

    with patch("src.scrapers.category_scraper.load_categories_catalog") as mock_catalog:
        mock_catalog.return_value = CategoriesCatalog(categories=[cat_item])

        async with async_session() as session:
            added, updated = await crawler.sync_category(session, "pokemon_tcg", max_products=10)
            await session.commit()

            assert added == 1
            assert updated == 0

            # Validar persistencia en BD
            from sqlalchemy import select
            stmt = select(Product).where(Product.url_original == "https://www.theway.cl/pokemon-etb")
            prod = (await session.execute(stmt)).scalar_one_or_none()

            assert prod is not None
            assert prod.nombre == "ETB Destinos Brillantes"
            assert prod.categoria == "Pokémon TCG"
            assert prod.es_top_categoria is True
            assert prod.umbral_descuento_porcentaje == 15.0

    await test_engine.dispose()
