import datetime
from typing import List, Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database import Base


class Product(Base):
    """Modelo representativo de un producto monitoreado."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url_original: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True, index=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    tienda: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Precios
    precio_actual: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precio_minimo: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precio_objetivo: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    umbral_descuento_porcentaje: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    
    # Clasificación por categoría y ranking
    categoria: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    es_top_categoria: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Control de estado y spam
    activo: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    ultima_alerta_en: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    
    # Metadatos de auditoría
    creado_en: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actualizado_en: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relación con el historial de precios (selectin para compatibilidad asíncrona total)
    historial_precios: Mapped[List["PriceHistory"]] = relationship(
        "PriceHistory",
        back_populates="producto",
        cascade="all, delete-orphan",
        order_by="desc(PriceHistory.timestamp)",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<Product(id={self.id}, tienda='{self.tienda}', nombre='{self.nombre[:30] if self.nombre else ''}', "
            f"precio_actual={self.precio_actual}, activo={self.activo})>"
        )


class PriceHistory(Base):
    """Histórico de fluctuación de precios de un producto."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    precio: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )

    producto: Mapped["Product"] = relationship("Product", back_populates="historial_precios")

    def __repr__(self) -> str:
        return f"<PriceHistory(id={self.id}, product_id={self.product_id}, precio={self.precio}, timestamp={self.timestamp})>"
