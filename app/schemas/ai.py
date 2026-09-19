import uuid
from typing import Optional, List
from pydantic import BaseModel, Field


class VirtualTryOnRequest(BaseModel):
    variante_id: uuid.UUID = Field(..., description="ID de la variante de producto a probar")
    imagen_cliente_base64: str = Field(..., description="Foto del cliente en base64 (JPEG o PNG)")
    altura_cm: Optional[int] = Field(None, ge=120, le=230, description="Estatura en cm para calibrar calce")
    peso_kg: Optional[int] = Field(None, ge=30, le=200, description="Peso aproximado en kg")
    preferencia_calce: Optional[str] = Field("REGULAR", description="SLIM, REGULAR, OVERSIZE")


class MatchingProduct(BaseModel):
    nombre: str
    categoria: str
    motivo: str


class VirtualTryOnResponse(BaseModel):
    imagen_resultado_url: str = Field(..., description="URL o Data-URI con la imagen generada de la prueba virtual")
    talla_sugerida: str = Field(..., description="Talla recomendada por la IA (ej. S, M, L, XL, XXL)")
    calce_detectado: str = Field(..., description="Ajuste detectado: Slim Fit, Regular Fit, Oversize")
    nivel_coincidencia_porcentaje: int = Field(..., ge=0, le=100, description="Nivel de compatibilidad y calce (0-100%)")
    analisis_silueta: str = Field(..., description="Descripción del análisis anatómico realizado por Gemini")
    consejo_estilo: str = Field(..., description="Consejo personalizado de combinación y outfit")
    combinaciones_sugeridas: List[MatchingProduct] = Field(default=[], description="Prendas del catálogo que combinan")
