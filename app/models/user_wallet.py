import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class UserWallet(Base):
    __tablename__ = "user_wallets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    saldo_actual = Column(Numeric(10, 2), nullable=False, default=0.00)
    saldo_retenido = Column(Numeric(10, 2), nullable=False, default=0.00)
    nonce_secuencia = Column(Integer, nullable=False, default=0)
    firma_balance = Column(String(255), nullable=True)
    moneda = Column(String(10), default="BOB", nullable=False)

    ultimo_sync_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relaciones
    user = relationship("User", backref="wallet", lazy="selectin")
    tenant = relationship("Tenant", lazy="selectin")
    transacciones = relationship("WalletTransaction", back_populates="wallet", cascade="all, delete-orphan", lazy="selectin")

    @property
    def user_email(self) -> str:
        return self.user.email if self.user else ""

    @property
    def user_name(self) -> str:
        return self.user.full_name if self.user else ""

    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant_wallet"),
    )

