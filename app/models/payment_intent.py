import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Numeric, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class PaymentIntent(Base):
    """Durable identity and recovery record; no provider credentials stored here."""
    __tablename__ = 'payment_intents'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey('tenants.id'), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False, index=True)
    cliente_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey('sucursales.id'), nullable=True)
    purpose = Column(String(10), nullable=False)
    state = Column(String(24), nullable=False, default='CREATING')
    provider_order_id = Column(String(100), unique=True, nullable=True)
    capture_id = Column(String(100), unique=True, nullable=True)
    refund_id = Column(String(100), nullable=True)
    amount_bob = Column(Numeric(10, 2), nullable=False)
    amount_usd = Column(Numeric(10, 2), nullable=False)
    exchange_rate = Column(Numeric(14, 6), nullable=False)
    merchant_key = Column(String(64), nullable=False)
    provider_mode = Column(String(10), nullable=False)
    payload = Column(JSONB, nullable=False, default=dict)
    sale_id = Column(UUID(as_uuid=True), ForeignKey('ventas.id'), nullable=True, unique=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    __table_args__ = (CheckConstraint("amount_bob > 0 AND amount_bob < 100000000 AND amount_usd > 0 AND amount_usd < 100000000 AND exchange_rate > 0 AND exchange_rate < 100000000", name='ck_payment_positive'),)
