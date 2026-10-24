import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class ExchangeItemCreate(BaseModel):
    variante_devuelta_id: uuid.UUID
    cantidad_devuelta: int = Field(default=1, ge=1)
    variante_entregada_id: uuid.UUID
    cantidad_entregada: int = Field(default=1, ge=1)


class ProductExchangeCreate(BaseModel):
    numero_recibo: str = Field(..., min_length=3, description="Número de recibo original de la venta")
    motivo: str = Field(..., min_length=3, max_length=255, description="Motivo del cambio")
    estado_prenda: str = Field(default="BUEN_ESTADO", max_length=100)
    metodo_pago_diferencia: Optional[str] = Field(default="NINGUNO")
    nota: Optional[str] = None
    items: List[ExchangeItemCreate] = Field(..., min_length=1)


class VerifyReceiptItemResponse(BaseModel):
    variante_id: uuid.UUID
    producto_nombre: str
    talla: str
    color: str
    sku: str
    cantidad_comprada: int
    cantidad_ya_cambiada: int
    cantidad_disponible_cambio: int
    precio_unitario: float


class VerifyReceiptResponse(BaseModel):
    valido: bool
    motivo_invalido: Optional[str] = None
    venta_id: Optional[uuid.UUID] = None
    numero_recibo: str
    fecha_venta: Optional[datetime] = None
    dias_transcurridos: int = 0
    dentro_del_plazo: bool = True
    plazo_maximo_dias: int = 30
    sucursal_id: Optional[uuid.UUID] = None
    sucursal_nombre: Optional[str] = None
    cliente_nombre: Optional[str] = None
    items: List[VerifyReceiptItemResponse] = []


class ExchangeDetailResponse(BaseModel):
    id: uuid.UUID
    variante_devuelta_id: uuid.UUID
    variante_devuelta_nombre: str
    variante_devuelta_talla: str
    variante_devuelta_color: str
    variante_devuelta_sku: str
    cantidad_devuelta: int
    precio_unitario_devuelto: float

    variante_entregada_id: uuid.UUID
    variante_entregada_nombre: str
    variante_entregada_talla: str
    variante_entregada_color: str
    variante_entregada_sku: str
    cantidad_entregada: int
    precio_unitario_entregado: float

    subtotal_diferencia: float


class ProductExchangeResponse(BaseModel):
    id: uuid.UUID
    numero_cambio: str
    venta_id: uuid.UUID
    numero_recibo_original: str
    sucursal_id: uuid.UUID
    sucursal_nombre: str
    cajero_id: Optional[uuid.UUID] = None
    cajero_nombre: Optional[str] = None
    cliente_id: Optional[uuid.UUID] = None
    cliente_nombre: Optional[str] = None
    fecha_cambio: datetime
    dias_transcurridos: int
    monto_total_devuelto: float
    monto_total_entregado: float
    diferencia: float
    metodo_pago_diferencia: str
    motivo: str
    estado_prenda: str
    nota: Optional[str] = None
    detalles: List[ExchangeDetailResponse] = []

    model_config = ConfigDict(from_attributes=True)
