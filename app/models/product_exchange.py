import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class ProductExchange(Base):
    __tablename__ = "cambios_producto"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    numero_cambio = Column(String(30), unique=True, nullable=False, index=True)
    
    venta_id = Column(UUID(as_uuid=True), ForeignKey("ventas.id", ondelete="RESTRICT"), nullable=False, index=True)
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="RESTRICT"), nullable=False, index=True)
    cajero_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    
    fecha_cambio = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    dias_transcurridos = Column(Integer, nullable=False, default=0)
    
    monto_total_devuelto = Column(Numeric(10, 2), nullable=False, default=0.0)
    monto_total_entregado = Column(Numeric(10, 2), nullable=False, default=0.0)
    diferencia = Column(Numeric(10, 2), nullable=False, default=0.0)  # >0 cliente paga, <0 saldo a favor, 0 exacto
    
    metodo_pago_diferencia = Column(String(50), nullable=True, default="NINGUNO")  # EFECTIVO, TARJETA, QR, VALE, NINGUNO
    motivo = Column(String(255), nullable=False)  # Cambio de talla, color, etc.
    estado_prenda = Column(String(100), nullable=False, default="BUEN_ESTADO")  # BUEN_ESTADO, CON_ETIQUETA
    nota = Column(String(300), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relaciones
    venta = relationship("Sale", foreign_keys=[venta_id], lazy="joined")
    sucursal = relationship("Branch", foreign_keys=[sucursal_id], lazy="joined")
    cajero = relationship("User", foreign_keys=[cajero_id], lazy="joined")
    cliente = relationship("User", foreign_keys=[cliente_id], lazy="joined")
    detalles = relationship("ProductExchangeDetail", back_populates="cambio", cascade="all, delete-orphan", lazy="selectin")
