import json
import logging
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_STORES_PATH = Path(__file__).resolve().parent.parent.parent / "stores.json"


class StoreSelectors(BaseModel):
    title: List[str] = Field(default_factory=lambda: ["h1"])
    price: List[str] = Field(default_factory=list)
    image: List[str] = Field(default_factory=list)


class CategorySelectors(BaseModel):
    card: List[str] = Field(default_factory=list)
    link: List[str] = Field(default_factory=list)
    title: List[str] = Field(default_factory=list)
    price: List[str] = Field(default_factory=list)
    image: List[str] = Field(default_factory=list)


class StoreRule(BaseModel):
    id: str
    name: str
    active: bool = True
    domains: List[str]
    use_json_ld: bool = True
    selectors: StoreSelectors = Field(default_factory=StoreSelectors)
    category_selectors: CategorySelectors = Field(default_factory=CategorySelectors)
    affiliate_param: Optional[str] = None
    affiliate_tag: Optional[str] = None


class StoresCatalog(BaseModel):
    stores: List[StoreRule] = Field(default_factory=list)


_CACHED_CATALOG: Optional[StoresCatalog] = None


def load_store_catalog(file_path: Optional[Path] = None, force_reload: bool = False) -> StoresCatalog:
    """Carga y valida el catálogo de tiendas desde stores.json."""
    global _CACHED_CATALOG
    if _CACHED_CATALOG is not None and not force_reload:
        return _CACHED_CATALOG

    path = file_path or DEFAULT_STORES_PATH
    if not path.exists():
        logger.warning(f"Archivo de configuración de tiendas no encontrado en {path}. Usando catálogo vacío.")
        _CACHED_CATALOG = StoresCatalog(stores=[])
        return _CACHED_CATALOG

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _CACHED_CATALOG = StoresCatalog(**data)
        logger.info(f"Cargadas {len(_CACHED_CATALOG.stores)} configuraciones de tiendas desde {path.name}.")
    except Exception as ex:
        logger.error(f"Error cargando catálogo de tiendas desde {path}: {ex}", exc_info=True)
        _CACHED_CATALOG = StoresCatalog(stores=[])

    return _CACHED_CATALOG


def find_store_rule_for_url(url: str, catalog: Optional[StoresCatalog] = None) -> Optional[StoreRule]:
    """Identifica la regla de tienda correspondiente para una URL dada según su dominio."""
    cat = catalog or load_store_catalog()
    domain = urlparse(url).netloc.lower()

    for store in cat.stores:
        for store_domain in store.domains:
            if store_domain.lower() in domain or domain.endswith(store_domain.lower()):
                return store
    return None


def find_store_rule_by_id(store_id: str, catalog: Optional[StoresCatalog] = None) -> Optional[StoreRule]:
    """Busca una regla de tienda por su ID interno (ej: 'falabella', 'mercadolibre')."""
    cat = catalog or load_store_catalog()
    for store in cat.stores:
        if store.id.lower() == store_id.lower():
            return store
    return None
