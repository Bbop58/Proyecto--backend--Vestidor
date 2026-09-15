import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Supplier(Base):
    __tablename__ = "proveedores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    nombre = Column(String(100), unique=True, index=True, nullable=False)
    contacto = Column(String(100), nullable=True)
    telefono = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    direccion = Column(String(200), nullable=True)
    activo = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
