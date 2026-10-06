import pytest
from src.scrapers.base import BaseScraper
from src.scrapers.amazon import AmazonScraper
from src.scrapers.mercadolibre import MercadoLibreScraper
from src.scrapers.registry import get_scraper_for_url, GenericScraper
from src.config import settings


def test_clean_price_formats():
    """Verifica la normalización de múltiples formatos de precio internacionales."""
    # Formato hispano / europeo: punto para miles, coma para decimales
    assert BaseScraper.clean_price("$ 1.299,99") == 1299.99
    assert BaseScraper.clean_price("1.250,50 €") == 1250.50
    assert BaseScraper.clean_price("49,90") == 49.90

    # Formato anglosajón: coma para miles, punto para decimales
    assert BaseScraper.clean_price("$ 1,299.99") == 1299.99
    assert BaseScraper.clean_price("1,250.50 USD") == 1250.50
    assert BaseScraper.clean_price("49.90") == 49.90

    # Monedas sin decimales con miles (ARS, CLP, COP, etc.)
    assert BaseScraper.clean_price("$ 12.500") == 12500.0
    assert BaseScraper.clean_price("$ 150.000") == 150000.0

    # Enteros planos y espacios especiales
    assert BaseScraper.clean_price("  $ 1200\xa0 ") == 1200.0


def test_clean_price_invalid():
    """Verifica que entradas sin dígitos numéricos generen excepción adecuada."""
    with pytest.raises(ValueError):
        BaseScraper.clean_price("Gratis")
    with pytest.raises(ValueError):
        BaseScraper.clean_price("")


def test_amazon_affiliate_url_builder(monkeypatch):
    """Verifica la inyección limpia del tag de asociado de Amazon."""
    monkeypatch.setattr(settings, "AMAZON_AFFILIATE_TAG", "miserver-21")
    scraper = AmazonScraper()

    url = "https://www.amazon.es/dp/B08N5WRWNW?ref_=ast_sto_dp&th=1"
    affiliate_url = scraper.build_affiliate_url(url)

    assert "tag=miserver-21" in affiliate_url
    assert "ref_=" not in affiliate_url  # Los parámetros efímeros deben ser removidos


def test_mercadolibre_affiliate_url_builder(monkeypatch):
    """Verifica la inyección del parámetro de tracking de Mercado Libre."""
    monkeypatch.setattr(settings, "MERCADOLIBRE_AFFILIATE_TAG", "ml_partner_123")
    scraper = MercadoLibreScraper()

    url = "https://articulo.mercadolibre.com.ar/MLA-12345-producto.html"
    affiliate_url = scraper.build_affiliate_url(url)

    assert "matt_tool=ml_partner_123" in affiliate_url


def test_scraper_registry():
    """Verifica la correcta resolución de la instancia del scraper por dominio."""
    assert isinstance(get_scraper_for_url("https://www.amazon.com/dp/123"), AmazonScraper)
    assert isinstance(get_scraper_for_url("https://www.amazon.es/dp/123"), AmazonScraper)
    assert isinstance(get_scraper_for_url("https://articulo.mercadolibre.com.mx/MLM-123"), MercadoLibreScraper)
    assert isinstance(get_scraper_for_url("https://tienda-inventada.com/item/1"), GenericScraper)


def test_clean_url_removes_tracking_and_resolvedbidid():
    """Verifica que clean_url elimine tokens dinámicos de Paris (resolvedBidId) y parámetros UTM/tracking."""
    paris_url1 = (
        "https://www.paris.cl/soundbar-hw-q800h-505394999.html"
        "?resolvedBidId=iJE6IwoQBqsZuVvmdga9BFwlVMxbOBIQAaBiNIE-cXuF73kwGSy8JBoQ"
    )
    paris_url2 = (
        "https://www.paris.cl/soundbar-hw-q800h-505394999.html"
        "?resolvedBidId=aCtCKAoQBqsZlT1Ad6-MBC9ghTQvDBIQAaBiNIE-cXuF73kwGSy8JBoQ"
    )
    expected = "https://www.paris.cl/soundbar-hw-q800h-505394999.html"

    assert BaseScraper.clean_url(paris_url1) == expected
    assert BaseScraper.clean_url(paris_url2) == expected
    assert BaseScraper.clean_url(paris_url1) == BaseScraper.clean_url(paris_url2)

    utm_url = "https://falabella.com/producto/123?utm_source=fb&utm_medium=cpc&gclid=123"
    assert BaseScraper.clean_url(utm_url) == "https://falabella.com/producto/123"


def test_clean_product_title():
    """Verifica la limpieza de títulos con prefijos parásitos (Vista Previa) y fragmentos de precio pegados."""
    dirty_title = (
        "Vista PreviaSamsungSoundbar Q-Series 5.1.2 Subwoofer HW-Q800H 2026"
        "0(0)36%36%$309.99034%34%$319.990$489.990Agregar al carroPromocionado"
    )
    cleaned = BaseScraper.clean_product_title(dirty_title)
    assert cleaned == "SamsungSoundbar Q-Series 5.1.2 Subwoofer HW-Q800H 2026"

    simple_dirty = "Vista previa Smart TV LG 55 OLED Agregar al carro"
    assert BaseScraper.clean_product_title(simple_dirty) == "Smart TV LG 55 OLED"

