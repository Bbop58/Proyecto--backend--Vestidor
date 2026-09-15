import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
import enum


def utc_now():
    return datetime.now(timezone.utc)


class MovementType(str, enum.Enum):
    ENTRADA = "ENTRADA"                       # Recepción de productos (+stock_actual)
    SALIDA_VENTA = "SALIDA_VENTA"             # Venta consumada (-stock_actual, -stock_reservado)
    SALIDA_RESERVA = "SALIDA_RESERVA"         # Reserva creada (+stock_reservado)
    LIBERACION_RESERVA = "LIBERACION_RESERVA" # Reserva cancelada/expirada (-stock_reservado)
    AJUSTE = "AJUSTE"                         # Corrección manual (+/- stock_actual)
    DEVOLUCION = "DEVOLUCION"                 # Devolución de cliente (+stock_actual)


class InventoryMovement(Base):
    """Registro de cada movimiento de stock en inventario."""
    __tablename__ = "movimientos_inventario"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    inventario_id = Column(UUID(as_uuid=True), ForeignKey("inventario.id", ondelete="RESTRICT"), nullable=False, index=True)

    tipo = Column(SAEnum(MovementType, name="movement_type"), nullable=False, index=True)
    cantidad = Column(Integer, nullable=False)  # Puede ser negativo en AJUSTE
    stock_antes = Column(Integer, nullable=False)
    stock_despues = Column(Integer, nullable=False)

    # Contexto opcional
    referencia = Column(String(100), nullable=True)  # Número de factura, orden, etc.
    nota = Column(String(300), nullable=True)
    usuario_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relationships
    inventario = relationship("Inventory", back_populates="movimientos")
    usuario = relationship("User", backref="movimientos_inventario")
