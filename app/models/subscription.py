import uuid
from datetime import datetime, timezone, date
from sqlalchemy import Column, String, Text, Date, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("subscription_plans.id"), nullable=False, index=True)
    estado = Column(String(50), default="ACTIVA", nullable=False)  # 'ACTIVA', 'VENCIDA', 'SUSPENDIDA'
    fecha_inicio = Column(Date, default=date.today, nullable=False)
    fecha_vencimiento = Column(Date, nullable=False)
    notas = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    tenant = relationship("Tenant", back_populates="subscriptions", lazy="joined")
    plan = relationship("SubscriptionPlan", back_populates="subscriptions", lazy="joined")
