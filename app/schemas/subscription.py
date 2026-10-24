import uuid
from datetime import date, datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


class SubscriptionPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    codigo: str
    nombre: str
    descripcion: Optional[str] = None
    max_sucursales: int
    permite_reportes_ia: bool
    permite_offline: bool
    precio_mensual_bob: float
    activo: bool


class TenantInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre: str
    razon_social: str
    nit_rut: str
    slug: str
    logo_url: Optional[str] = None
    telefono: Optional[str] = None
    email_contacto: Optional[str] = None
    activo: bool


class CurrentSubscriptionResponse(BaseModel):
    tenant: TenantInfo
    plan: SubscriptionPlanResponse
    estado: str
    fecha_inicio: date
    fecha_vencimiento: date
    dias_restantes: int
    sucursales_usadas: int
    sucursales_maximas: int
    sucursales_disponibles: int
    porcentaje_sucursales_usadas: float
    permite_reportes_ia: bool
    permite_offline: bool
    mensaje_alerta: Optional[str] = None


class ChangePlanRequest(BaseModel):
    plan_codigo: str = Field(..., description="Código del plan: 'BASICO' o 'PROFESIONAL'")
    notas: Optional[str] = "Cambio de plan solicitado desde administración"
