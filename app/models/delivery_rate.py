import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Numeric, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class DeliveryRate(Base):
    __tablename__ = "tarifas_delivery"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    zona_nombre = Column(String(120), nullable=False)
    ciudad = Column(String(100), nullable=False)
    costo_envio = Column(Numeric(10, 2), nullable=False, default=0.00)
    tiempo_estimado_horas = Column(Integer, nullable=False, default=24)
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    branch = relationship("Branch", backref="tarifas_delivery")
    tenant = relationship("Tenant")
