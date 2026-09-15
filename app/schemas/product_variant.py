import uuid
from datetime import datetime
from typing import Optional, Literal
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict

TallaEnum = Literal["S", "M", "L", "XL", "XXL", "ESTANDAR"]


class ProductVariantBase(BaseModel):
    talla: str = Field(..., min_length=1, max_length=10, description="Talla: S, M, L, XL, XXL")
    color: str = Field(..., min_length=2, max_length=30, description="Color de la prenda")
    sku: Optional[str] = Field(None, max_length=50, description="Código SKU único")
    precio_extra: Decimal = Field(Decimal("0.00"), ge=0, description="Precio adicional sobre precio base")
    activo: bool = Field(True, description="Estado de la variante")


class ProductVariantCreate(ProductVariantBase):
    pass


class ProductVariantUpdate(BaseModel):
    talla: Optional[str] = Field(None, min_length=1, max_length=10)
    color: Optional[str] = Field(None, min_length=2, max_length=30)
    sku: Optional[str] = Field(None, max_length=50)
    precio_extra: Optional[Decimal] = Field(None, ge=0)
    activo: Optional[bool] = None


class ProductVariantResponse(ProductVariantBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    producto_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
