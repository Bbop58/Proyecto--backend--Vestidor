from fastapi import APIRouter, Depends, Query, status
from app.api.deps import require_permission, get_current_user
from app.models.user import User
from app.ml.ml_service import MLAnalyticsService
from app.schemas.analytics import (
    TrainingMetricsResponse,
    DemandForecastResponse,
    CustomerSegmentationResponse,
    DemandPredictCustomRequest,
    DemandPredictCustomResponse
)

router = APIRouter(prefix="/analytics", tags=["Analítica Inteligente con Machine Learning"])


@router.post(
    "/train",
    response_model=TrainingMetricsResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Entrenar o re-entrenar modelos de Machine Learning (Supervisado y No Supervisado)"
)
def train_models(
    current_user: User = Depends(get_current_user)
):
    """
    Entrena el modelo supervisado de Demanda (Random Forest) y el modelo no supervisado
    de Segmentación de Clientes (K-Means RFM), calculando métricas matemáticas de rendimiento (R², RMSE, Silhouette Score).
    """
    return MLAnalyticsService.train_all()


@router.get(
    "/forecast",
    response_model=DemandForecastResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Obtener serie temporal de ventas y proyección de demanda futura (Supervisado)"
)
def get_sales_forecast(
    weeks_ahead: int = Query(default=4, ge=1, le=12, description="Número de semanas a proyectar"),
    current_user: User = Depends(get_current_user)
):
    """
    Devuelve la serie histórica de ventas combinada con el pronóstico para las próximas semanas
    junto con métricas estadísticas y el resumen ejecutivo del modelo.
    """
    return MLAnalyticsService.get_demand_forecast(weeks_ahead=weeks_ahead)


@router.get(
    "/segments",
    response_model=CustomerSegmentationResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Obtener segmentación de clientes RFM y clusters K-Means (No Supervisado)"
)
def get_customer_segments(
    current_user: User = Depends(get_current_user)
):
    """
    Devuelve los 4 clusters de clientes (VIP, Frecuentes, Ocasionales, En Riesgo) con sus
    estadísticas RFM promedio, centroides, puntos para gráfico de dispersión y métrica Silhouette Score.
    """
    return MLAnalyticsService.get_customer_segments()


@router.post(
    "/forecast/predict",
    response_model=DemandPredictCustomResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Simulador de demanda predictiva personalizada"
)
def predict_custom_demand(
    request: DemandPredictCustomRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Realiza una inferencia puntual para simular la demanda esperada en Bolivianos y unidades
    según la semana, temporada, sucursal, categoría y ventas previas.
    """
    return MLAnalyticsService.predict_custom_demand(request)
