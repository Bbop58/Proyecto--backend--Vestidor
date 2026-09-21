from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class VoiceReportQueryRequest(BaseModel):
    pregunta: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Pregunta en lenguaje natural sobre el negocio formulada por voz o texto",
        examples=["Dame el top 5 de productos más vendidos este mes"]
    )
    limite: Optional[int] = Field(
        default=100,
        ge=1,
        le=500,
        description="Límite máximo de filas a retornar"
    )


class VoiceReportQueryResponse(BaseModel):
    pregunta: str
    interpretacion: str
    sql_generado: str
    columnas: List[str]
    filas: List[Dict[str, Any]]
    total_filas: int
    tiempo_ms: float
