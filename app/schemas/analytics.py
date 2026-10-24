from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


# ── Modelos de Métricas y Rendimiento ──

class SupervisedModelMetrics(BaseModel):
    r2_score: float = Field(..., description="Coeficiente de determinación R² (0.0 a 1.0)")
    rmse: float = Field(..., description="Raíz del Error Cuadrático Medio en Bs.")
    mae: float = Field(..., description="Error Absoluto Medio en Bs.")
    mape: float = Field(..., description="Error Porcentual Absoluto Medio (%)")
    muestras_entrenamiento: int
    muestras_prueba: int
    importancia_caracteristicas: List[Dict[str, Any]] = Field(default_factory=list)


class UnsupervisedModelMetrics(BaseModel):
    silhouette_score: float = Field(..., description="Coeficiente de Silueta (-1.0 a 1.0, >0.5 indica buena separación)")
    inercia_wss: float = Field(..., description="Within-Cluster Sum of Squares")
    n_clusters: int = Field(default=4)
    total_clientes_analizados: int


class TrainingMetricsResponse(BaseModel):
    supervisado: SupervisedModelMetrics
    no_supervisado: UnsupervisedModelMetrics
    fecha_entrenamiento: str
    estado: str = "COMPLETADO"
    tiempo_entrenamiento_ms: float
    mensaje: str = "Modelos entrenados y guardados exitosamente"


# ── Previsión de Demanda de Ventas (Supervisado) ──

class ForecastDataPoint(BaseModel):
    periodo: str
    semana_numero: int
    ventas_reales_bs: Optional[float] = None
    demanda_proyectada_bs: float
    unidades_estimadas: int
    es_proyeccion: bool


class DemandForecastResponse(BaseModel):
    serie_temporal: List[ForecastDataPoint]
    metricas: SupervisedModelMetrics
    resumen_ejecutivo: str
    crecimiento_proyectado_pct: float
    total_proyectado_mes_bs: float


class DemandPredictCustomRequest(BaseModel):
    semana_del_ano: int = Field(..., ge=1, le=52)
    mes: int = Field(..., ge=1, le=12)
    temporada: str = Field(default="Invierno")
    categoria_nombre: Optional[str] = "Poleras y Camisas"
    sucursal_nombre: Optional[str] = "Sucursal Central"
    ventas_semana_anterior_bs: Optional[float] = 12500.0


class DemandPredictCustomResponse(BaseModel):
    demanda_proyectada_bs: float
    unidades_estimadas: int
    rango_confianza_inferior_bs: float
    rango_confianza_superior_bs: float
    factores_principales: List[str]


# ── Segmentación de Clientes (No Supervisado / RFM) ──

class CustomerClusterCentroid(BaseModel):
    cluster_id: int
    nombre_segmento: str
    color_hex: str
    total_clientes: int
    porcentaje_total: float
    recencia_promedio_dias: float
    frecuencia_promedio_compras: float
    gasto_promedio_bs: float
    ticket_promedio_bs: float
    estrategia_recomendada: str


class CustomerDataPoint(BaseModel):
    cliente_id: str
    nombre: str
    recencia_dias: int
    frecuencia_compras: int
    monto_total_bs: float
    cluster_id: int
    cluster_nombre: str


class CustomerSegmentationResponse(BaseModel):
    clusters: List[CustomerClusterCentroid]
    puntos_muestra: List[CustomerDataPoint]
    metricas: UnsupervisedModelMetrics
    resumen_ejecutivo: str
