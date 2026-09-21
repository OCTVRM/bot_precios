import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES_PATH = Path(__file__).resolve().parent.parent.parent / "categories.json"


class CategoryItem(BaseModel):
    id: str
    name: str
    default_threshold_percent: float = Field(default=15.0)
    error_threshold_percent: float = Field(default=50.0)
    active: bool = Field(default=True)
    stores: Dict[str, str] = Field(default_factory=dict)


class CategoriesCatalog(BaseModel):
    categories: List[CategoryItem] = Field(default_factory=list)


_CACHED_CATEGORIES: Optional[CategoriesCatalog] = None


def load_categories_catalog(
    file_path: Optional[Path] = None, force_reload: bool = False
) -> CategoriesCatalog:
    """Carga y valida el catálogo de categorías desde categories.json."""
    global _CACHED_CATEGORIES
    if _CACHED_CATEGORIES is not None and not force_reload:
        return _CACHED_CATEGORIES

    path = file_path or DEFAULT_CATEGORIES_PATH
    if not path.exists():
        logger.warning(f"Archivo de categorías no encontrado en {path}. Usando catálogo vacío.")
        _CACHED_CATEGORIES = CategoriesCatalog(categories=[])
        return _CACHED_CATEGORIES

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _CACHED_CATEGORIES = CategoriesCatalog(**data)
        logger.info(
            f"Cargadas {len(_CACHED_CATEGORIES.categories)} categorías desde {path.name}."
        )
    except Exception as ex:
        logger.error(f"Error cargando catálogo de categorías desde {path}: {ex}", exc_info=True)
        _CACHED_CATEGORIES = CategoriesCatalog(categories=[])

    return _CACHED_CATEGORIES


def save_categories_catalog(
    catalog: CategoriesCatalog, file_path: Optional[Path] = None
) -> None:
    """Persiste el catálogo de categorías a disco."""
    global _CACHED_CATEGORIES
    path = file_path or DEFAULT_CATEGORIES_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog.model_dump(), f, indent=2, ensure_ascii=False)
    _CACHED_CATEGORIES = catalog
    logger.info(f"Catálogo de categorías guardado exitosamente en {path}.")


def find_category_by_id(
    category_id: str, catalog: Optional[CategoriesCatalog] = None
) -> Optional[CategoryItem]:
    """Busca una categoría por su identificador (ej: 'celulares', 'notebook')."""
    cat = catalog or load_categories_catalog()
    for item in cat.categories:
        if item.id.lower() == category_id.lower():
            return item
    return None


def add_or_update_category(
    category_id: str,
    name: str,
    threshold: float = 15.0,
    error_threshold: float = 50.0,
    active: bool = True,
    stores: Optional[Dict[str, str]] = None,
    file_path: Optional[Path] = None,
) -> CategoryItem:
    """Crea una nueva categoría o actualiza una existente y persiste los cambios."""
    catalog = load_categories_catalog(file_path=file_path, force_reload=True)
    existing = find_category_by_id(category_id, catalog)

    if existing:
        existing.name = name
        existing.default_threshold_percent = threshold
        existing.error_threshold_percent = error_threshold
        existing.active = active
        if stores:
            existing.stores.update(stores)
        item = existing
    else:
        item = CategoryItem(
            id=category_id,
            name=name,
            default_threshold_percent=threshold,
            error_threshold_percent=error_threshold,
            active=active,
            stores=stores or {},
        )
        catalog.categories.append(item)

    save_categories_catalog(catalog, file_path=file_path)
    return item


def add_store_to_category(
    category_id: str,
    store_id: str,
    url: str,
    file_path: Optional[Path] = None,
) -> bool:
    """Asocia o actualiza la URL de una tienda a una categoría."""
    catalog = load_categories_catalog(file_path=file_path, force_reload=True)
    category = find_category_by_id(category_id, catalog)
    if not category:
        return False

    category.stores[store_id.lower()] = url.strip()
    save_categories_catalog(catalog, file_path=file_path)
    return True
