import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, Numeric, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class ProductExchangeDetail(Base):
    __tablename__ = "detalles_cambio_producto"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    cambio_id = Column(UUID(as_uuid=True), ForeignKey("cambios_producto.id", ondelete="CASCADE"), nullable=False, index=True)
    
    variante_devuelta_id = Column(UUID(as_uuid=True), ForeignKey("variantes_producto.id", ondelete="RESTRICT"), nullable=False, index=True)
    cantidad_devuelta = Column(Integer, nullable=False, default=1)
    precio_unitario_devuelto = Column(Numeric(10, 2), nullable=False)
    
    variante_entregada_id = Column(UUID(as_uuid=True), ForeignKey("variantes_producto.id", ondelete="RESTRICT"), nullable=False, index=True)
    cantidad_entregada = Column(Integer, nullable=False, default=1)
    precio_unitario_entregado = Column(Numeric(10, 2), nullable=False)
    
    subtotal_diferencia = Column(Numeric(10, 2), nullable=False, default=0.0)

    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relaciones
    cambio = relationship("ProductExchange", back_populates="detalles")
    variante_devuelta = relationship("ProductVariant", foreign_keys=[variante_devuelta_id], lazy="joined")
    variante_entregada = relationship("ProductVariant", foreign_keys=[variante_entregada_id], lazy="joined")
