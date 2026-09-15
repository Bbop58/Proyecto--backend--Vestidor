import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Numeric, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Product(Base):
    __tablename__ = "productos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    nombre = Column(String(100), index=True, nullable=False)
    descripcion = Column(Text, nullable=True)
    precio_base = Column(Numeric(10, 2), nullable=False)
    categoria_id = Column(UUID(as_uuid=True), ForeignKey("categorias.id", ondelete="RESTRICT"), nullable=False)
    temporada = Column(String(50), nullable=True, index=True)
    proveedor = Column(String(100), nullable=True, index=True)
    imagen_url = Column(String(500), nullable=True)
    activo = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    categoria = relationship("Category", back_populates="productos", lazy="joined")
    variantes = relationship("ProductVariant", back_populates="producto", cascade="all, delete-orphan", lazy="joined")
