import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime, Integer, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    nombre = Column(String(150), nullable=False)
    razon_social = Column(String(200), nullable=False)
    nit_rut = Column(String(50), nullable=False)
    slug = Column(String(100), unique=True, index=True, nullable=False)
    logo_url = Column(String(255), nullable=True)
    color_primario = Column(String(20), default="#4f46e5", nullable=True)
    color_secundario = Column(String(20), default="#818cf8", nullable=True)
    eslogan = Column(String(200), nullable=True)
    telefono = Column(String(50), nullable=True)
    email_contacto = Column(String(150), nullable=True)
    paypal_client_id = Column(String(255), nullable=True)
    paypal_client_secret = Column(String(255), nullable=True)
    paypal_mode = Column(String(20), default="sandbox", nullable=True)
    activo = Column(Boolean, default=True, nullable=False)
    es_operadora = Column(Boolean, default=False, server_default='false', nullable=False)
    platform_revision = Column(Integer, default=0, server_default='0', nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    subscriptions = relationship("Subscription", back_populates="tenant", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (Index('uq_tenants_single_operator', 'es_operadora', unique=True,
                           postgresql_where=text('es_operadora = true')),)
