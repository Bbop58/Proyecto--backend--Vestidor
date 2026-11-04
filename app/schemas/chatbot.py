from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal['user','model']
    content: str = Field(...,max_length=4000, description="Texto del mensaje")


class ChatbotRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000, description="Pregunta o mensaje del usuario")
    history: List[ChatMessage] = Field(default_factory=list,max_length=12, description="Historial previo de la conversación")
    tenant_slug: Optional[str] = Field(default=None, description="Slug del inquilino (ej. 'urbanstyle' o 'moda-express-tarija')")


class ChatbotResponse(BaseModel):
    origen: Literal['gemini', 'local'] = 'local'
    modelo: Optional[str] = None
    reply: str = Field(..., description="Respuesta conversacional del asistente")
    suggested_prompts: List[str] = Field(default_factory=list, description="Chips interactivos de preguntas rápidas sugeridas")
    tenant_name: str = Field(..., description="Nombre de la empresa que atiende")
    tenant_slug: str = Field(..., description="Slug de la empresa")

