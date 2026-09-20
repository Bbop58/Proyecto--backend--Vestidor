import uuid
from typing import Optional
from pydantic import BaseModel, Field


class VirtualTryOnRequest(BaseModel):
    producto_id: Optional[uuid.UUID] = Field(None, description="ID del producto a probar")
    variante_id: Optional[uuid.UUID] = Field(None, description="ID opcional de variante de producto a probar")
    imagen_cliente_base64: str = Field(..., description="Foto del cliente en base64 (JPEG o PNG)")


class VirtualTryOnResponse(BaseModel):
    imagen_resultado_base64: str = Field(..., description="Data-URI o Base64 de la imagen realista generada por Gemini")
