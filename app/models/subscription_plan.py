import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Integer, Boolean, Numeric, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    codigo = Column(String(50), unique=True, index=True, nullable=False)  # 'BASICO', 'PROFESIONAL'
    nombre = Column(String(100), nullable=False)
    descripcion = Column(Text, nullable=True)
    max_sucursales = Column(Integer, nullable=False)  # Básico: 2, Profesional: 10
    permite_reportes_ia = Column(Boolean, default=False, nullable=False)  # Básico: False, Profesional: True
    permite_offline = Column(Boolean, default=True, nullable=False)
    precio_mensual_bob = Column(Numeric(10, 2), default=0.00, nullable=False)
    activo = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    subscriptions = relationship("Subscription", back_populates="plan", lazy="selectin")
