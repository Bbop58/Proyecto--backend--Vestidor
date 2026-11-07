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


class BranchRecommendationCase(RecommendationAction):
    producto_id: UUID
    sucursal_id: UUID | None
    sucursal_nombre: str | None
    stock_actual: int | None = Field(default=None, ge=0)
    reservado: int | None = Field(default=None, ge=0)
    minimo_operativo: int | None = Field(default=None, ge=0)
    observado_desde: date | None = None
    observados_dias: int = Field(ge=0)
    exceso: int | None = Field(default=None, ge=0)
    alerta_minimo: bool = False
    revisar_otras_sucursales: bool = False
    otras_sucursales: list[dict] = Field(default_factory=list)


class BranchRecommendationTotals(BaseModel):
    total: int = 0
    reponer: int = 0
    evaluar_oferta: int = 0
    mantener: int = 0
    sin_historial: int = 0
    revisar_datos: int = 0
    variantes_unicas: int = 0
    productos_unicos: int = 0


class BranchRecommendationSummary(BaseModel):
    id: UUID
    nombre: str
    totales: BranchRecommendationTotals


class VariantInventoryCoverage(BaseModel):
    variante_id: UUID
    sku: str
    disponible: int | None = None
    objetivo: int | None = None
    compra_sugerida: int | None = None
    datos_completos: bool


class BranchRecommendationSnapshot(BaseModel):
    version: Literal[2] = 2
    origen_datos: Literal['BASE_DATOS_EMPRESA'] = 'BASE_DATOS_EMPRESA'
    entorno: Literal['DEMO'] = 'DEMO'
    tenant_id: UUID
    empresa_nombre: str
    fecha_datos: datetime
    ventana_desde: date
    ventana_hasta: date
    fingerprint: str
    interpretacion: ForecastInterpretation
    resumen_operativo: str
    ventas_excluidas: int = Field(ge=0)
    totales: BranchRecommendationTotals
    sucursales_resumen: list[BranchRecommendationSummary]
    casos: list[BranchRecommendationCase]
    cobertura_empresa: list[VariantInventoryCoverage]
    legacy: RecommendationResponse = Field(exclude=True)


class RecommendationSummaryV2(RecommendationResponse):
    version: Literal[2] = 2
    origen_datos: Literal['BASE_DATOS_EMPRESA'] = 'BASE_DATOS_EMPRESA'
    entorno: Literal['DEMO'] = 'DEMO'
    empresa_nombre: str
    totales: BranchRecommendationTotals
    sucursales_resumen: list[BranchRecommendationSummary]
    destacados: list[BranchRecommendationCase]
    resumen_operativo: str
