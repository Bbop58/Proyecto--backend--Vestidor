import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Inventory(Base):
    """Stock de una variante de producto en una sucursal específica."""
    __tablename__ = "inventario"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="RESTRICT"), nullable=False, index=True)
    variante_id = Column(UUID(as_uuid=True), ForeignKey("variantes_producto.id", ondelete="RESTRICT"), nullable=False, index=True)

    stock_actual = Column(Integer, default=0, nullable=False)
    stock_reservado = Column(Integer, default=0, nullable=False)
    stock_minimo = Column(Integer, default=5, nullable=False)
    ubicacion = Column(String(50), nullable=True)

    activo = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("sucursal_id", "variante_id", name="uq_inventario_sucursal_variante"),
    )

    # Relationships
    sucursal = relationship("Branch", backref="inventario")
    variante = relationship("ProductVariant", backref="inventario")
    movimientos = relationship("InventoryMovement", back_populates="inventario", lazy="dynamic")

    @property
    def stock_disponible(self) -> int:
        """Stock disponible para nuevas reservas o ventas."""
        return max(0, self.stock_actual - self.stock_reservado)

    @property
    def alerta(self) -> str | None:
        """Nivel de alerta de stock."""
        if self.stock_actual == 0:
            return "CRITICO"
        if self.stock_actual <= self.stock_minimo:
            return "BAJO"
        if self.stock_actual > self.stock_minimo * 10:
            return "EXCEDENTE"
        return None
