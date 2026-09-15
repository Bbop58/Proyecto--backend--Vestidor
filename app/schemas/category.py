import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class CategoryBase(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=50, description="Nombre de la categoría")
    descripcion: Optional[str] = Field(None, max_length=200, description="Descripción opcional")
    activa: bool = Field(True, description="Estado de la categoría")


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=2, max_length=50)
    descripcion: Optional[str] = Field(None, max_length=200)
    activa: Optional[bool] = None


class CategoryResponse(CategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
