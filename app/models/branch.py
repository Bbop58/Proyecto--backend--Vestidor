import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Branch(Base):
    __tablename__ = "sucursales"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    nombre = Column(String(100), unique=True, index=True, nullable=False)
    ciudad = Column(String(50), nullable=False)
    direccion = Column(String(200), nullable=False)
    telefono = Column(String(20), nullable=True)
    activa = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
