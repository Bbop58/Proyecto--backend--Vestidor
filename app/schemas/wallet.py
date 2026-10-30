import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class WalletResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    saldo_actual: float
    saldo_retenido: float
    nonce_secuencia: int
    firma_balance: Optional[str] = None
    ultimo_sync_at: datetime
    updated_at: datetime


class WalletTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    wallet_id: uuid.UUID
    tipo: str
    monto: float
    saldo_anterior: float
    saldo_posterior: float
    referencia_externa: Optional[str] = None
    client_operation_id: Optional[uuid.UUID] = None
    origen: str
    estado_sync: str
    notas: Optional[str] = None
    metadatos: Optional[Dict[str, Any]] = None
    created_at: datetime


class PayPalTopupRequest(BaseModel):
    order_id: str = Field(..., min_length=3, description="Order ID de captura de PayPal")
    monto_usd: float = Field(..., gt=0, description="Monto recargado en dólares")
    monto_bs: Optional[float] = Field(None, gt=0, description="Equivalente en Bolivianos")


class AdminCashTopupRequest(BaseModel):
    cliente_id: uuid.UUID = Field(..., description="ID del cliente al que se abona el efectivo")
    monto_bs: float = Field(..., gt=0, description="Monto en Bolivianos recibido en caja")
    notas: Optional[str] = Field(None, max_length=250, description="Observaciones del cajero")


class OfflineOrderItem(BaseModel):
    variante_id: uuid.UUID
    cantidad: int = Field(..., gt=0)
    precio_unitario: float = Field(..., gt=0)


class OfflineOrderPayload(BaseModel):
    client_operation_id: uuid.UUID = Field(..., description="UUIDv4 único generado offline por la app móvil")
    sucursal_id: uuid.UUID
    items: List[OfflineOrderItem] = Field(..., min_length=1)
    monto_total: float = Field(..., gt=0)
    fecha_orden: Optional[datetime] = None
    codigo_autorizacion_offline: str
    nota: Optional[str] = None


class BatchOfflineSyncRequest(BaseModel):
    orders: List[OfflineOrderPayload] = Field(..., min_length=1)


class BatchOfflineSyncResponse(BaseModel):
    procesadas: int
    exitosas: int
    fallidas: int
    nuevo_saldo: float
    nuevo_nonce: int
    nueva_firma: str
    detalles: List[Dict[str, Any]]
