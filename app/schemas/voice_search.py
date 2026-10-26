import uuid
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class VoiceCatalogSearchRequest(BaseModel):
    transcript: str = Field(..., min_length=2, max_length=250, description="Texto transcripto de la voz del usuario")
    sucursal_id: Optional[uuid.UUID] = Field(None, description="Filtrar por sucursal específica")


class ExtractedFilters(BaseModel):
    categoria: Optional[str] = None
    color: Optional[str] = None
    talla: Optional[str] = None
    rango_precio_max: Optional[float] = None
    estilo_o_evento: Optional[str] = None


class VoiceProductItem(BaseModel):
    id: uuid.UUID
    nombre: str
    descripcion: Optional[str] = None
    precio_base: float
    categoria_nombre: Optional[str] = None
    imagen_url: Optional[str] = None
    colores_disponibles: List[str] = []
    tallas_disponibles: List[str] = []

    model_config = ConfigDict(from_attributes=True)


class VoiceCatalogSearchResponse(BaseModel):
    query_original: str
    filtros_interpretados: ExtractedFilters
    total_encontrados: int
    productos: List[VoiceProductItem]
