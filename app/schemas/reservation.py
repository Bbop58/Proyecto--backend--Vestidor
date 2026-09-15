import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, field_validator
from app.models.reservation import ReservationStatus


class ReservationItemCreate(BaseModel):
    variante_id: uuid.UUID
    cantidad: int = Field(..., gt=0, le=3, description="Máximo 3 unidades por variante")


class ReservationCreate(BaseModel):
    sucursal_id: uuid.UUID
    fecha_hora_esperada: datetime
    items: List[ReservationItemCreate] = Field(..., min_length=1, max_length=10, description="Máximo 10 items por reserva")
    nota: Optional[str] = Field(None, max_length=300)


class ReservationDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reserva_id: uuid.UUID
    variante_id: uuid.UUID
    variante_sku: Optional[str] = None
    producto_nombre: Optional[str] = None
    talla: Optional[str] = None
    color: Optional[str] = None
    cantidad: int
    precio_unitario: float
    subtotal: float
    created_at: datetime


class ReservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    codigo: str
    cliente_id: uuid.UUID
    cliente_nombre: Optional[str] = None
    cliente_email: Optional[str] = None
    sucursal_id: uuid.UUID
    sucursal_nombre: Optional[str] = None
    sucursal_ciudad: Optional[str] = None
    estado: ReservationStatus
    fecha_hora_esperada: datetime
    fecha_expiracion: datetime
    fecha_recogida: Optional[datetime] = None
    total_estimado: float
    nota: Optional[str] = None
    activo: bool
    detalles: List[ReservationDetailResponse] = []
    created_at: datetime
    updated_at: datetime


class ExpireReservationsResponse(BaseModel):
    total_expiradas: int
    message: str
