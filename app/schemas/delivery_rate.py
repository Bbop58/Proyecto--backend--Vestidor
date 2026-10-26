import uuid
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime

class DeliveryRateBase(BaseModel):
    branch_id: uuid.UUID
    zona_nombre: str = Field(..., max_length=120)
    ciudad: str = Field(..., max_length=100)
    costo_envio: float = Field(..., ge=0.0)
    tiempo_estimado_horas: int = Field(24, ge=1)
    activo: bool = True

class DeliveryRateCreate(DeliveryRateBase):
    pass

class DeliveryRateUpdate(BaseModel):
    zona_nombre: Optional[str] = Field(None, max_length=120)
    ciudad: Optional[str] = Field(None, max_length=100)
    costo_envio: Optional[float] = Field(None, ge=0.0)
    tiempo_estimado_horas: Optional[int] = Field(None, ge=1)
    activo: Optional[bool] = None

class DeliveryRateResponse(DeliveryRateBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    branch_nombre: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
