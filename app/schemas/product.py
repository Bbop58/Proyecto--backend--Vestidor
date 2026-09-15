import uuid
from datetime import datetime
from typing import Optional, List
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.category import CategoryResponse
from app.schemas.product_variant import ProductVariantCreate, ProductVariantResponse


class ProductBase(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=100, description="Nombre del producto")
    descripcion: Optional[str] = Field(None, description="Descripción detallada")
    precio_base: Decimal = Field(..., gt=0, description="Precio base en moneda local")
    categoria_id: uuid.UUID = Field(..., description="ID de la categoría a la que pertenece")
    temporada: Optional[str] = Field(None, max_length=50, description="Temporada")
    proveedor: Optional[str] = Field(None, max_length=100, description="Proveedor")
    imagen_url: Optional[str] = Field(None, max_length=500, description="URL de la imagen del producto")
    activo: bool = Field(True, description="Estado del producto")


class ProductCreate(ProductBase):
    variantes: Optional[List[ProductVariantCreate]] = Field(default=[], description="Variantes iniciales opcionales")


class ProductUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    descripcion: Optional[str] = None
    precio_base: Optional[Decimal] = Field(None, gt=0)
    categoria_id: Optional[uuid.UUID] = None
    temporada: Optional[str] = Field(None, max_length=50)
    proveedor: Optional[str] = Field(None, max_length=100)
    imagen_url: Optional[str] = Field(None, max_length=500)
    activo: Optional[bool] = None


class ProductResponse(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    categoria: Optional[CategoryResponse] = None
    variantes: List[ProductVariantResponse] = []
    created_at: datetime
    updated_at: datetime
