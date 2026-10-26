import uuid
from typing import Optional
from pydantic import BaseModel, ConfigDict


class ProductRecommendationResponse(BaseModel):
    id: uuid.UUID
    nombre: str
    descripcion: Optional[str] = None
    precio_base: float
    categoria_nombre: str
    imagen_url: Optional[str] = None
    motivo_recomendacion: str
    stock_total_disponible: int

    model_config = ConfigDict(from_attributes=True)
