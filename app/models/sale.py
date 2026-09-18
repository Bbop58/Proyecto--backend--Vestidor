import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class SaleType(str, enum.Enum):
    PRESENCIAL = "PRESENCIAL"
    DIGITAL = "DIGITAL"


class SaleStatus(str, enum.Enum):
    PENDIENTE_PAGO = "PENDIENTE_PAGO"
    COMPLETADA = "COMPLETADA"
    CANCELADA = "CANCELADA"


class PaymentMethod(str, enum.Enum):
    EFECTIVO = "EFECTIVO"
    TARJETA = "TARJETA"
    QR = "QR"
    PAYPAL = "PAYPAL"


class Sale(Base):
    __tablename__ = "ventas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    numero_recibo = Column(String(30), unique=True, nullable=False, index=True)
    
    tipo = Column(Enum(SaleType, name="sale_type"), nullable=False, default=SaleType.PRESENCIAL)
    estado = Column(Enum(SaleStatus, name="sale_status"), nullable=False, default=SaleStatus.COMPLETADA, index=True)
    
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="RESTRICT"), nullable=False, index=True)
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    cajero_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    
    metodo_pago = Column(Enum(PaymentMethod, name="payment_method"), nullable=False, default=PaymentMethod.EFECTIVO)
    referencia_pago = Column(String(100), nullable=True)
    
    monto_total = Column(Numeric(10, 2), nullable=False, default=0.0)
    monto_recibido = Column(Numeric(10, 2), nullable=True)
    cambio = Column(Numeric(10, 2), nullable=True, default=0.0)
    
    nota = Column(String(300), nullable=True)
    motivo_cancelacion = Column(String(300), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relaciones
    sucursal = relationship("Branch", foreign_keys=[sucursal_id], lazy="joined")
    cliente = relationship("User", foreign_keys=[cliente_id], lazy="joined")
    cajero = relationship("User", foreign_keys=[cajero_id], lazy="joined")
    detalles = relationship("SaleDetail", back_populates="venta", cascade="all, delete-orphan", lazy="selectin")
