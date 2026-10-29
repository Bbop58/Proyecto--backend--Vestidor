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
    color_primario: Optional[str] = "#4f46e5"
    color_secundario: Optional[str] = "#818cf8"
    eslogan: Optional[str] = None
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


class TenantBrandingUpdateRequest(BaseModel):
    nombre: Optional[str] = None
    logo_url: Optional[str] = None
    color_primario: Optional[str] = Field(None, pattern=r"^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$")
    color_secundario: Optional[str] = Field(None, pattern=r"^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$")
    eslogan: Optional[str] = None
    telefono: Optional[str] = None
    email_contacto: Optional[str] = None


class TenantOverviewItem(BaseModel):
    id: uuid.UUID
    nombre: str
    razon_social: str
    nit_rut: str
    slug: str
    logo_url: Optional[str] = None
    color_primario: Optional[str] = "#4f46e5"
    color_secundario: Optional[str] = "#818cf8"
    eslogan: Optional[str] = None
    email_contacto: Optional[str] = None
    telefono: Optional[str] = None
    activo: bool
    created_at: datetime
    plan_codigo: str
    plan_nombre: str
    precio_mensual_bob: float
    sucursales_usadas: int
    sucursales_maximas: int
    usuarios_activos: int
    fecha_inicio: date
    fecha_vencimiento: date
    dias_restantes: int
    estado_suscripcion: str


class SaaSSummaryResponse(BaseModel):
    total_tenants: int
    total_tenants_activos: int
    total_sucursales_activas: int
    total_usuarios_activos: int
    ingresos_recurrentes_mensuales_bob: float
    tenants: List[TenantOverviewItem]


class TenantCreateRequest(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=150)
    razon_social: str = Field(..., min_length=2, max_length=200)
    nit_rut: str = Field(..., min_length=4, max_length=50)
    slug: str = Field(..., min_length=2, max_length=100)
    plan_codigo: str = Field("PROFESIONAL", description="'BASICO' o 'PROFESIONAL'")
    logo_url: Optional[str] = None
    color_primario: Optional[str] = "#4f46e5"
    color_secundario: Optional[str] = "#818cf8"
    eslogan: Optional[str] = None
    telefono: Optional[str] = None
    email_contacto: Optional[str] = None
    admin_email: Optional[str] = None
    admin_full_name: Optional[str] = None
    admin_password: Optional[str] = "Password123!"
