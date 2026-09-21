"""Módulo de scrapers desacoplados para diversas tiendas."""
from src.scrapers.base import BaseScraper, ScrapedItem
from src.scrapers.amazon import AmazonScraper
from src.scrapers.mercadolibre import MercadoLibreScraper
from src.scrapers.configurable import ConfigurableScraper
from src.scrapers.store_config import StoreRule, load_store_catalog, find_store_rule_for_url
from src.scrapers.registry import get_scraper_for_url, GenericScraper

__all__ = [
    "BaseScraper",
    "ScrapedItem",
    "AmazonScraper",
    "MercadoLibreScraper",
    "ConfigurableScraper",
    "StoreRule",
    "load_store_catalog",
    "find_store_rule_for_url",
    "get_scraper_for_url",
    "GenericScraper",
]
