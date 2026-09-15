import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class BranchBase(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=100, description="Nombre de la sucursal")
    ciudad: str = Field(..., min_length=2, max_length=50, description="Ciudad de ubicación")
    direccion: str = Field(..., min_length=3, max_length=200, description="Dirección física")
    telefono: Optional[str] = Field(None, max_length=20, description="Teléfono de contacto")
    activa: bool = Field(True, description="Estado operativo de la sucursal")


class BranchCreate(BranchBase):
    pass


class BranchUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    ciudad: Optional[str] = Field(None, min_length=2, max_length=50)
    direccion: Optional[str] = Field(None, min_length=3, max_length=200)
    telefono: Optional[str] = Field(None, max_length=20)
    activa: Optional[bool] = None


class BranchResponse(BranchBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
