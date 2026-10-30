import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    wallet_id = Column(UUID(as_uuid=True), ForeignKey("user_wallets.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    tipo = Column(String(50), nullable=False, index=True)
    # 'RECARGA_PAYPAL', 'RECARGA_EFECTIVO_MOSTRADOR', 'PAGO_ORDEN_OFFLINE',
    # 'PAGO_ORDEN_ONLINE', 'REEMBOLSO_DEVOLUCION_PRENDA'

    monto = Column(Numeric(10, 2), nullable=False)
    saldo_anterior = Column(Numeric(10, 2), nullable=False)
    saldo_posterior = Column(Numeric(10, 2), nullable=False)

    referencia_externa = Column(String(150), nullable=True, index=True)
    client_operation_id = Column(UUID(as_uuid=True), unique=True, nullable=True, index=True)

    origen = Column(String(30), nullable=False, default="MOBILE")  # 'MOBILE', 'WEB_ADMIN', 'SERVER'
    estado_sync = Column(String(30), nullable=False, default="CONCILIADO")  # 'CONCILIADO', 'EN_DISPUTA'
    notas = Column(Text, nullable=True)
    metadatos = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    # Relaciones
    wallet = relationship("UserWallet", back_populates="transacciones")
    tenant = relationship("Tenant")
