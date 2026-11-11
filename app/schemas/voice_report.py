import json
from typing import List, Dict, Any, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


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
    criterios_aplicados: List[str] = Field(default_factory=list)
    resumen_hablado: Optional[str] = Field(
        default=None,
        description="Resumen ejecutivo en lenguaje natural optimizado para síntesis de voz (Audio Briefing)"
    )


class VoiceReportExcelExportRequest(BaseModel):
    pregunta: str
    interpretacion: Optional[str] = "Reporte de Inteligencia de Negocios"
    columnas: List[str]
    filas: List[Dict[str, Any]]


class VoiceReportEmailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipient: EmailStr
    operation_id: UUID
    pregunta: str = Field(min_length=3, max_length=500)
    interpretacion: str = Field(default='Reporte gerencial', max_length=3000)
    columnas: List[str] = Field(min_length=1, max_length=30)
    filas: List[Dict[str, Any]] = Field(max_length=500)

    @model_validator(mode='after')
    def validate_snapshot(self):
        if any(not column.strip() or len(column) > 120 for column in self.columnas):
            raise ValueError('Los encabezados del reporte no son válidos')
        if len(set(self.columnas)) != len(self.columnas):
            raise ValueError('El reporte contiene encabezados duplicados')
        try:
            encoded = json.dumps(self.filas, ensure_ascii=False, default=str).encode('utf-8')
        except (TypeError, ValueError):
            raise ValueError('Los datos del reporte no son válidos') from None
        if len(encoded) > 1_000_000:
            raise ValueError('El reporte supera el tamaño máximo para enviar por correo')
        if any(len(str(value)) > 4000 for row in self.filas for value in row.values() if value is not None):
            raise ValueError('Una celda del reporte supera el tamaño permitido')
        return self
