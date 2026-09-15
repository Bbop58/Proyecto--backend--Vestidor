import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Numeric, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class ProductVariant(Base):
    __tablename__ = "variantes_producto"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id", ondelete="CASCADE"), nullable=False, index=True)
    talla = Column(String(10), nullable=False)
    color = Column(String(30), nullable=False)
    sku = Column(String(50), unique=True, index=True, nullable=False)
    precio_extra = Column(Numeric(10, 2), default=0.00, nullable=False)
    activo = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("producto_id", "talla", "color", name="uq_producto_talla_color"),
    )

    producto = relationship("Product", back_populates="variantes")
