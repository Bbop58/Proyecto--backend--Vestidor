import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, EmailStr, ConfigDict


class SupplierBase(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=100, description="Nombre del proveedor")
    contacto: Optional[str] = Field(None, max_length=100, description="Persona de contacto")
    telefono: Optional[str] = Field(None, max_length=20, description="Teléfono")
    email: Optional[EmailStr] = Field(None, description="Correo electrónico válido")
    direccion: Optional[str] = Field(None, max_length=200, description="Dirección del proveedor")
    activo: bool = Field(True, description="Estado del proveedor")


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    contacto: Optional[str] = Field(None, max_length=100)
    telefono: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    direccion: Optional[str] = Field(None, max_length=200)
    activo: Optional[bool] = None


class SupplierResponse(SupplierBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
