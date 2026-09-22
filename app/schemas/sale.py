import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict
from app.models.sale import SaleType, SaleStatus, PaymentMethod


class SaleItemCreate(BaseModel):
    variante_id: uuid.UUID
    cantidad: int = Field(..., gt=0, description="Cantidad vendida (> 0)")
    precio_unitario: Optional[float] = Field(None, gt=0, description="Precio opcional, default al precio de lista")
    reserva_id: Optional[uuid.UUID] = Field(None, description="ID de reserva si la venta liquida un apartado")


class PaymentInfo(BaseModel):
    metodo: PaymentMethod
    monto_recibido: Optional[float] = Field(None, ge=0)
    referencia: Optional[str] = Field(None, max_length=100)


class PresencialSaleCreate(BaseModel):
    sucursal_id: uuid.UUID
    cliente_id: Optional[uuid.UUID] = Field(None, description="Opcional para cliente anónimo")
    items: List[SaleItemCreate] = Field(..., min_length=1)
    pago: PaymentInfo
    nota: Optional[str] = Field(None, max_length=300)


class DigitalSaleCreate(BaseModel):
    sucursal_id: uuid.UUID
    items: List[SaleItemCreate] = Field(..., min_length=1)
    token_pago: Optional[str] = Field(None, description="Token de pasarela de pago")
    nota: Optional[str] = Field(None, max_length=300)


class SaleCancel(BaseModel):
    motivo: str = Field(..., min_length=3, max_length=300)


class SaleDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    venta_id: uuid.UUID
    variante_id: uuid.UUID
    variante_sku: Optional[str] = None
    producto_nombre: Optional[str] = None
    talla: Optional[str] = None
    color: Optional[str] = None
    reserva_id: Optional[uuid.UUID] = None
    cantidad: int
    precio_unitario: float
    subtotal: float
    created_at: datetime


class SaleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    numero_recibo: str
    tipo: SaleType
    estado: SaleStatus
    sucursal_id: uuid.UUID
    sucursal_nombre: Optional[str] = None
    sucursal_ciudad: Optional[str] = None
    cliente_id: Optional[uuid.UUID] = None
    cliente_nombre: Optional[str] = None
    cajero_id: Optional[uuid.UUID] = None
    cajero_nombre: Optional[str] = None
    metodo_pago: PaymentMethod
    referencia_pago: Optional[str] = None
    monto_total: float
    impuesto_iva: float = 0.0
    monto_neto: float = 0.0
    monto_recibido: Optional[float] = None
    cambio: Optional[float] = None
    nota: Optional[str] = None
    motivo_cancelacion: Optional[str] = None
    detalles: List[SaleDetailResponse] = []
    created_at: datetime
    updated_at: datetime


class SalesSummaryReport(BaseModel):
    total_ventas: int
    ingresos_totales: float
    total_iva: float = 0.0
    ganancia_neta: float = 0.0
    ticket_promedio: float
    ventas_efectivo: float
    ventas_tarjeta: float
    ventas_qr: float


class TopProductReport(BaseModel):
    producto_nombre: str
    variante_sku: str
    talla: str
    color: str
    unidades_vendidas: int
    ingresos_generados: float
