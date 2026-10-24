import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field, ConfigDict


class SyncItemCreate(BaseModel):
    variante_id: uuid.UUID = Field(..., description="ID de la variante de producto (talla/color)")
    cantidad: int = Field(..., ge=1, description="Cantidad vendida o reservada")
    precio_unitario: Decimal = Field(..., ge=0, description="Precio unitario pactado")


class SyncOperationItemRequest(BaseModel):
    client_operation_id: uuid.UUID = Field(..., description="UUID único generado por el dispositivo offline (Web/Flutter)")
    operation_type: str = Field(default="VENTA_CONTADO_OFFLINE", description="'VENTA_CONTADO_OFFLINE' o 'RESERVA_OFFLINE'")
    sucursal_id: uuid.UUID = Field(..., description="ID de la sucursal donde ocurrió la venta")
    cajero_id: Optional[uuid.UUID] = Field(default=None, description="ID del cajero que registró la venta")
    cliente_id: Optional[uuid.UUID] = Field(default=None, description="ID del cliente registrado (o null si es venta rápida)")
    fecha_local: datetime = Field(..., description="Timestamp local exacto en que se registró en el dispositivo")
    items: List[SyncItemCreate] = Field(..., min_length=1, description="Lista de prendas y cantidades")
    monto_total: Decimal = Field(..., ge=0, description="Monto total cobrado")
    monto_efectivo_recibido: Optional[Decimal] = Field(default=None, ge=0)
    cambio_entregado: Optional[Decimal] = Field(default=None, ge=0)
    metodo_pago: Optional[str] = Field(default="EFECTIVO", description="EFECTIVO, QR, TARJETA")
    notas: Optional[str] = Field(default=None, description="Notas de la venta offline")


class SyncBatchRequest(BaseModel):
    operations: List[SyncOperationItemRequest] = Field(..., description="Lote de operaciones offline a sincronizar")


class SyncOperationResult(BaseModel):
    client_operation_id: uuid.UUID
    status: str  # 'CONFIRMADA', 'RECHAZADA_POR_STOCK', 'CONFLICTO'
    is_duplicate: bool = False
    receipt_number: Optional[str] = None
    server_entity_id: Optional[uuid.UUID] = None
    message: str
    error_detail: Optional[str] = None


class SyncBatchResponse(BaseModel):
    processed_at: datetime
    total_received: int
    total_confirmed: int
    total_rejected: int
    total_duplicates: int
    results: List[SyncOperationResult]


class SyncHistoryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_operation_id: uuid.UUID
    tenant_id: uuid.UUID
    branch_id: uuid.UUID
    operation_type: str
    sync_status: str
    client_created_at: datetime
    created_at: datetime
    entity_id: Optional[uuid.UUID] = None
    error_detail: Optional[str] = None
