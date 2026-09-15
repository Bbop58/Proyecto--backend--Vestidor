import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class ReservationStatus(str, enum.Enum):
    PENDIENTE = "PENDIENTE"
    PREPARADA = "PREPARADA"
    RECOGIDA = "RECOGIDA"
    CANCELADA = "CANCELADA"
    EXPIRADA = "EXPIRADA"
    COMPLETADA = "COMPLETADA"


class Reservation(Base):
    __tablename__ = "reservas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    codigo = Column(String(30), unique=True, nullable=False, index=True)
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False, index=True)
    
    estado = Column(
        Enum(ReservationStatus, name="reservation_status"),
        nullable=False,
        default=ReservationStatus.PENDIENTE,
        index=True
    )
    fecha_hora_esperada = Column(DateTime(timezone=True), nullable=False)
    fecha_expiracion = Column(DateTime(timezone=True), nullable=False)
    fecha_recogida = Column(DateTime(timezone=True), nullable=True)
    
    total_estimado = Column(Numeric(10, 2), nullable=False, default=0.0)
    nota = Column(String(300), nullable=True)
    activo = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relaciones
    cliente = relationship("User", foreign_keys=[cliente_id], lazy="joined")
    sucursal = relationship("Branch", foreign_keys=[sucursal_id], lazy="joined")
    detalles = relationship("ReservationDetail", back_populates="reserva", cascade="all, delete-orphan", lazy="selectin")
