from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from typing import Literal


class ForecastMetrics(BaseModel):
    mae: float
    rmse: float
    r2: Optional[float] = None
    muestras_prueba: int


class BusinessForecastPoint(BaseModel):
    fecha: str
    ventas_bs: float


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
