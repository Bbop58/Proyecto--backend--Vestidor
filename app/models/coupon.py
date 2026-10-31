import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import Column, String, Numeric, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Coupon(Base):
    __tablename__ = "cupones_descuento"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    codigo = Column(String(50), nullable=False, index=True)
    descripcion = Column(String(200), nullable=True)
    porcentaje_descuento = Column(Numeric(5, 2), nullable=False, default=10.00)  # ej: 15.00 (%)
    monto_minimo = Column(Numeric(10, 2), nullable=False, default=0.00)  # Gasto mínimo para aplicar
    fecha_vencimiento = Column(DateTime(timezone=True), nullable=True)
    activo = Column(Boolean, default=True, nullable=False)
    usos_actuales = Column(Integer, default=0, nullable=False)
    max_usos = Column(Integer, nullable=True)  # Null = Ilimitado
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    def is_valid(self, subtotal: Decimal) -> tuple[bool, str]:
        if not self.activo:
            return False, "El cupón no se encuentra activo."
        if self.fecha_vencimiento and self.fecha_vencimiento < datetime.now(timezone.utc):
            return False, "El cupón ha expirado."
        if self.max_usos is not None and self.usos_actuales >= self.max_usos:
            return False, "El cupón ha alcanzado el límite máximo de usos."
        if subtotal < self.monto_minimo:
            return False, f"El monto mínimo de compra para este cupón es de Bs. {self.monto_minimo:.2f}."
        return True, "Cupón válido."

