import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class CouponBase(BaseModel):
    codigo: str = Field(..., min_length=3, max_length=50, description="Código del cupón (ej: FICC2026)")
    descripcion: Optional[str] = Field(None, max_length=200, description="Descripción del beneficio")
    porcentaje_descuento: Decimal = Field(..., gt=0, le=100, description="Porcentaje de descuento (1 - 100%)")
    monto_minimo: Decimal = Field(default=Decimal("0.00"), ge=0, description="Monto mínimo para aplicar cupón")
    fecha_vencimiento: Optional[datetime] = Field(None, description="Fecha de expiración opcional")
    activo: bool = Field(True, description="Estado del cupón")
    max_usos: Optional[int] = Field(None, ge=1, description="Límite máximo de usos permitidos")


class CouponCreate(CouponBase):
    pass


class CouponResponse(CouponBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    usos_actuales: int
    created_at: datetime
    updated_at: datetime


class CouponValidateRequest(BaseModel):
    codigo: str = Field(..., description="Código de cupón a validar")
    monto_subtotal: Decimal = Field(..., gt=0, description="Subtotal de la compra en Bolivianos")


class CouponValidateResponse(BaseModel):
    valido: bool
    mensaje: str
    codigo: str
    porcentaje_descuento: Decimal = Decimal("0.00")
    monto_descuento: Decimal = Decimal("0.00")
    monto_total: Decimal = Decimal("0.00")

