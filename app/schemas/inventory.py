import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, model_validator
from app.models.inventory_movement import MovementType


# ─────────────────────── Inventory ───────────────────────

class InventoryUpdate(BaseModel):
    """Permite ajustar stock_minimo o ubicacion manualmente."""
    stock_minimo: Optional[int] = Field(None, ge=0)
    ubicacion: Optional[str] = Field(None, max_length=50)


class InventoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sucursal_id: uuid.UUID
    variante_id: uuid.UUID
    stock_actual: int
    stock_reservado: int
    stock_minimo: int
    stock_disponible: int          # Propiedad calculada del modelo
    ubicacion: Optional[str]
    alerta: Optional[str]          # CRITICO | BAJO | EXCEDENTE | None
    activo: bool
    created_at: datetime
    updated_at: datetime


# ─────────────────────── Reception ───────────────────────

class ReceptionItem(BaseModel):
    variante_id: uuid.UUID
    cantidad: int = Field(..., gt=0, description="Cantidad a recibir (> 0)")


class ReceptionCreate(BaseModel):
    sucursal_id: uuid.UUID
    productos: List[ReceptionItem] = Field(..., min_length=1)
    factura: Optional[str] = Field(None, max_length=100)
    nota: Optional[str] = Field(None, max_length=300)


class ReceptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sucursal_id: uuid.UUID
    items_procesados: int
    message: str


# ─────────────────────── Adjustment ──────────────────────

class StockAdjustment(BaseModel):
    inventario_id: uuid.UUID
    cantidad: int = Field(..., description="Cantidad del ajuste, puede ser negativa")
    nota: Optional[str] = Field(None, max_length=300)

    @model_validator(mode="after")
    def validate_cantidad(self):
        if self.cantidad == 0:
            raise ValueError("La cantidad de ajuste no puede ser 0")
        return self


# ─────────────────────── Movements ───────────────────────

class MovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    inventario_id: uuid.UUID
    tipo: MovementType
    cantidad: int
    stock_antes: int
    stock_despues: int
    referencia: Optional[str]
    nota: Optional[str]
    usuario_id: Optional[uuid.UUID]
    created_at: datetime


# ─────────────────────── Availability ────────────────────

class AvailabilityResponse(BaseModel):
    producto_id: uuid.UUID
    producto_nombre: str
    talla: str
    color: str
    sku: str
    sucursal_id: uuid.UUID
    sucursal: str
    ciudad: str
    stock_disponible: int
    precio: float            # precio_base + precio_extra


# ─────────────────────── Alerts ──────────────────────────

class AlertResponse(BaseModel):
    inventario_id: uuid.UUID
    sucursal: str
    variante_sku: str
    producto_nombre: str
    talla: str
    color: str
    stock_actual: int
    stock_minimo: int
    stock_disponible: int
    nivel: str  # CRITICO | BAJO | EXCEDENTE
