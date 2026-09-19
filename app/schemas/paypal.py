import uuid
from typing import Optional, List
from pydantic import BaseModel, Field
from app.schemas.sale import SaleItemCreate, SaleResponse


class PayPalOrderItem(BaseModel):
    name: str = Field(..., max_length=127)
    quantity: int = Field(..., ge=1)
    unit_amount_bob: float = Field(..., ge=0)


class PayPalCreateOrderRequest(BaseModel):
    monto_bob: float = Field(..., gt=0, description="Monto total en Bolivianos (BOB)")
    sucursal_id: Optional[uuid.UUID] = None
    cliente_id: Optional[uuid.UUID] = None
    reserva_id: Optional[uuid.UUID] = None
    items: Optional[List[PayPalOrderItem]] = []
    descripcion: Optional[str] = "Compra en FICCT STORE"
    return_url: Optional[str] = None
    cancel_url: Optional[str] = None


class PayPalCreateOrderResponse(BaseModel):
    order_id: str
    status: str
    monto_bob: float
    monto_usd: float
    exchange_rate: float
    approve_url: Optional[str] = None


class PayPalCaptureOrderRequest(BaseModel):
    order_id: str = Field(..., description="PayPal Order ID devuelto por el SDK de PayPal")
    sucursal_id: uuid.UUID = Field(..., description="Sucursal donde se concreta la venta")
    detalles: List[SaleItemCreate] = Field(..., min_length=1, description="Items vendidos")
    cliente_id: Optional[uuid.UUID] = None
    reserva_id: Optional[uuid.UUID] = None
    nota: Optional[str] = None


class PayPalCaptureOrderResponse(BaseModel):
    venta: SaleResponse
    paypal_order_id: str
    paypal_capture_id: str
    paypal_status: str
    monto_usd: float
