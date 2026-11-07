from typing import List, Dict, Any, Optional
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, Field, FiniteFloat, model_validator
from typing import Literal


class ForecastMetrics(BaseModel):
    mae: float
    rmse: float
    r2: Optional[float] = None
    muestras_prueba: int


class BusinessForecastPoint(BaseModel):
    fecha: str
    ventas_bs: float


ForecastMethod = Literal['ULTIMA_SEMANA','MEDIA_PREVIA','RANDOM_FOREST']


class BusinessMethodProjection(BaseModel):
    metodo: ForecastMethod
    etiqueta_metodo: str
    proyeccion: List[BusinessForecastPoint] = Field(min_length=4,max_length=4)
    total_proyectado_bs: FiniteFloat = Field(ge=0)

    @model_validator(mode='after')
    def coherent_projection(self):
        amounts = []
        days = []
        for point in self.proyeccion:
            # Decimal also rejects NaN/infinity before any comparisons. Preserve
            # exact display cents instead of tolerating conflicting rounded sums.
            value = Decimal(str(point.ventas_bs))
            try:
                valid = value.is_finite() and value >= 0 and value == value.quantize(Decimal('0.01'))
            except InvalidOperation:
                valid = False
            if not valid:
                raise ValueError('Projection amounts must be finite nonnegative cents')
            day = date.fromisoformat(point.fecha)
            if day.isoformat() != point.fecha or day.weekday() != 0:
                raise ValueError('Projection dates must be ISO week starts')
            amounts.append(value)
            days.append(day)
        if any(b-a != timedelta(weeks=1) for a,b in zip(days,days[1:])):
            raise ValueError('Projection weeks must be consecutive')
        if sum(amounts) != Decimal(str(self.total_proyectado_bs)):
            raise ValueError('Projection total must equal its four displayed amounts')
        return self


class BusinessForecastResult(BaseModel):
    metodo: Literal['ULTIMA_SEMANA','MEDIA_PREVIA','RANDOM_FOREST']
    etiqueta_metodo: str
    historico: List[BusinessForecastPoint]
    proyeccion: List[BusinessForecastPoint] = Field(min_length=4,max_length=4)
    total_proyectado_bs: float
    metricas: ForecastMetrics
    comparacion: List[Dict[str,Any]]
    evaluaciones: List[Dict[str,Any]]
    filas_entrenamiento_final: int
    # Absent/null explicitly denotes older stored analyses, never fabricated
    # reference or ML curves. No metadata/format bump for unrelated RFM models.
    proyecciones_metodos: Optional[List[BusinessMethodProjection]] = Field(default=None,min_length=3,max_length=3)

    @model_validator(mode='after')
    def coherent_comparison(self):
        if self.proyecciones_metodos is None:
            return self
        methods = {'ULTIMA_SEMANA','MEDIA_PREVIA','RANDOM_FOREST'}
        rows = {row.metodo: row for row in self.proyecciones_metodos}
        if set(rows) != methods:
            raise ValueError('Exactly one projection per method required')
        winner = rows[self.metodo]
        if self.proyeccion != winner.proyeccion or self.total_proyectado_bs != winner.total_proyectado_bs:
            raise ValueError('Selected forecast must match its published method')
        if any(row.proyeccion[i].fecha != self.proyeccion[i].fecha
               for row in rows.values() for i in range(4)):
            raise ValueError('All methods must project the same four weeks')
        if not self.historico or date.fromisoformat(self.historico[-1].fecha)+timedelta(weeks=1) != date.fromisoformat(self.proyeccion[0].fecha):
            raise ValueError('Projection must start after the last observed week')
        if any(not isinstance(row.get('metodo'),str) for row in self.comparacion):
            raise ValueError('Evaluation method must be an identifier')
        comparisons = {row.get('metodo'): row for row in self.comparacion}
        if len(self.comparacion) != 3 or set(comparisons) != methods:
            raise ValueError('Each projection needs its historical evaluation')
        import math
        for row in comparisons.values():
            metrics = ForecastMetrics.model_validate(row)
            if (any(not math.isfinite(value) or value < 0 for value in (metrics.mae,metrics.rmse))
                or (metrics.r2 is not None and not math.isfinite(metrics.r2))
                or metrics.muestras_prueba != 12):
                raise ValueError('Invalid historical evaluation metrics')
        if ForecastMetrics.model_validate(comparisons[self.metodo]) != self.metricas:
            raise ValueError('Selected metrics must match the evaluated method')
        # Selection uses unrounded errors inside training. Rounded persisted
        # scores must not be re-ranked here (near-ties can round identically).
        return self


class BusinessSegmentResult(BaseModel):
    clusters: List[Dict[str,Any]] = Field(min_length=4,max_length=4)
    puntos_muestra: List[Dict[str,Any]] = Field(max_length=150)
    muestra_grafico: int
    metricas: Dict[str,Any]


class StoredBusinessAnalysis(BaseModel):
    estado: Literal['LISTO','DATOS_INSUFICIENTES']
    calidad: Dict[str,Any]
    advertencias: List[str]
    mensaje: str
    resultado: Optional[Dict[str,Any]] = None


class BusinessAnalysisResponse(BaseModel):
    estado: Literal['LISTO','SIN_ANALISIS','DATOS_INSUFICIENTES','REQUIERE_ACTUALIZAR']
    tenant_id: str
    origen_datos: Literal['BASE_DATOS_EMPRESA']
    entorno: Literal['DEMO']
    fecha_corte: Optional[str] = None
    fecha_entrenamiento: Optional[str] = None
    datos_cambiaron: bool = False
    calidad: Dict[str,Any] = Field(default_factory=dict)
    advertencias: List[str] = Field(default_factory=list)
    mensaje: str
    resultado: Optional[Dict[str,Any]] = None


class BusinessTrainingResponse(BaseModel):
    estado: Literal['ACTUALIZADO']
    tenant_id: str
    fecha_corte: str
    analisis: Dict[str,StoredBusinessAnalysis]
