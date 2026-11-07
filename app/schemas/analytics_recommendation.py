"""Read-only recommendations: historical rules, not another ML prediction."""
from datetime import datetime, date
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field


class ForecastInterpretation(BaseModel):
    texto: str
    estado: Literal['VIGENTE', 'DESACTUALIZADO', 'SIN_ANALISIS']
    fecha_corte: datetime | None = None


class RecommendationAction(BaseModel):
    variante_id: UUID
    producto: str
    sku: str
    talla: str | None = None
    color: str | None = None
    tipo: Literal['REPONER', 'EVALUAR_OFERTA', 'MANTENER', 'REVISAR_DATOS', 'DATOS_INSUFICIENTES']
    texto: str = ''
    vendidas4: int = Field(ge=0)
    vendidas4_anteriores: int = Field(ge=0)
    disponible: int | None
    reservado: int = Field(ge=0)
    minimo_operativo: int = Field(ge=0)
    stock_objetivo: int | None = None
    reponer: int | None = None
    motivos: list[str] = Field(default_factory=list)
    oferta_vigente: bool = False
    sucursales: list[dict] = Field(default_factory=list)


class RecommendationQuality(BaseModel):
    variantes_evaluadas: int
    sin_historial: int
    inventario_inconsistente: int
    ventas_excluidas: int
    detalles: list[RecommendationAction] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    tenant_id: UUID
    fecha_datos: datetime
    ventana_desde: date
    ventana_hasta: date
    fingerprint: str
    interpretacion: ForecastInterpretation
    acciones: list[RecommendationAction] = Field(max_length=3)
    calidad: RecommendationQuality
    texto_lectura: str
