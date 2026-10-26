import uuid
from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class FavoriteCreate(BaseModel):
    product_id: uuid.UUID

class FavoriteResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    product_id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    # Product summary info
    product_nombre: Optional[str] = None
    precio_base: Optional[float] = None
    categoria_nombre: Optional[str] = None
    imagen_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class FavoriteCheckResponse(BaseModel):
    is_favorite: bool
    product_id: uuid.UUID
