import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Date, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Season(Base):
    __tablename__ = "temporadas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    nombre = Column(String(50), unique=True, index=True, nullable=False)
    año = Column(Integer, nullable=False, index=True)
    fecha_inicio = Column(Date, nullable=False)
    fecha_fin = Column(Date, nullable=False)
    activa = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
