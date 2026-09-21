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
