import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, EmailStr


class PlatformCompanyCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    operation_id: uuid.UUID
    nombre: str = Field(min_length=2, max_length=150)
    razon_social: str = Field(min_length=2, max_length=200)
    nit_rut: str = Field(min_length=4, max_length=50)
    slug: str = Field(min_length=2, max_length=100, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    email_contacto: Optional[EmailStr] = None
    telefono: Optional[str] = Field(None, max_length=50)
    reason: str = Field(min_length=5, max_length=500)


class PlatformCompanyStatus(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    operation_id: uuid.UUID
    activo: bool
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=5, max_length=500)


class PlatformCompanyResponse(BaseModel):
    id: uuid.UUID
    nombre: str
    razon_social: str
    nit_rut: str
    slug: str
    email_contacto: Optional[str] = None
    telefono: Optional[str] = None
    activo: bool
    platform_revision: int
    created_at: datetime
    sucursales_activas: int = 0


class PlatformCompanyList(BaseModel):
    total: int
    companies: list[PlatformCompanyResponse]
