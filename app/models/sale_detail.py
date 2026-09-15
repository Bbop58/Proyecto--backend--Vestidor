import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, Numeric, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class SaleDetail(Base):
    __tablename__ = "detalles_venta"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    venta_id = Column(UUID(as_uuid=True), ForeignKey("ventas.id", ondelete="CASCADE"), nullable=False, index=True)
    variante_id = Column(UUID(as_uuid=True), ForeignKey("variantes_producto.id", ondelete="RESTRICT"), nullable=False, index=True)
    reserva_id = Column(UUID(as_uuid=True), ForeignKey("reservas.id", ondelete="SET NULL"), nullable=True, index=True)
    
    cantidad = Column(Integer, nullable=False, default=1)
    precio_unitario = Column(Numeric(10, 2), nullable=False)
    subtotal = Column(Numeric(10, 2), nullable=False)

    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relaciones
    venta = relationship("Sale", back_populates="detalles")
    variante = relationship("ProductVariant", foreign_keys=[variante_id], lazy="joined")
    reserva = relationship("Reservation", foreign_keys=[reserva_id], lazy="joined")
