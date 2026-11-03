from uuid import UUID
from typing import List
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission, get_current_user
from app.models.user import User
from app.models.tenant import Tenant
from app.ml.ml_service import MLAnalyticsService
from app.schemas.analytics import (
    TrainingMetricsResponse,
    DemandForecastResponse,
    CustomerSegmentationResponse,
    DemandPredictCustomRequest,
    DemandPredictCustomResponse,
    MarkdownRecommendation,
    ApplyMarkdownRequest,
    ApplyMarkdownResponse
)

router = APIRouter(prefix="/analytics", tags=["Analítica Inteligente con Machine Learning"])


def _resolve_tenant_id(db: Session, current_user: User) -> UUID:
    if not current_user.tenant_id:
        raise HTTPException(403,"La cuenta debe pertenecer a una empresa")
    return current_user.tenant_id


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


# ── Optimizador Predictivo de Precios y Liquidaciones (Smart Markdown) ──

@router.get(
    "/markdown-recommendations",
    response_model=List[MarkdownRecommendation],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Listar recomendaciones de liquidación por baja rotación de inventario (ML)"
)
def get_markdown_recommendations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Evalúa el stock de inventario frente a la demanda y ventas recientes para identificar
    prendas estancadas y proponer descuentos óptimos con justificación analítica.
    """
    tenant_id = _resolve_tenant_id(db, current_user)
    return MLAnalyticsService.get_markdown_recommendations(db, tenant_id)


@router.post(
    "/markdown-recommendations/{product_id}/apply",
    response_model=ApplyMarkdownResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Aprobar o programar oferta sugerida por Machine Learning sobre un producto"
)
def apply_markdown_recommendation(
    product_id: UUID,
    payload: ApplyMarkdownRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Aplica el descuento sugerido (o personalizado) sobre el producto, programando fechas y precio oferta.
    """
    tenant_id = _resolve_tenant_id(db, current_user)
    try:
        return MLAnalyticsService.apply_markdown_recommendation(
            db=db,
            tenant_id=tenant_id,
            product_id=product_id,
            payload=payload
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post(
    "/markdown-recommendations/{product_id}/dismiss",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Descartar recomendación de oferta y conservar precio regular"
)
def dismiss_markdown_recommendation(
    product_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retira la oferta del producto restableciendo su condición de precio base.
    """
    tenant_id = _resolve_tenant_id(db, current_user)
    ok = MLAnalyticsService.dismiss_markdown_recommendation(
        db=db,
        tenant_id=tenant_id,
        product_id=product_id
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")
    return {"success": True, "mensaje": "Oferta retirada. El producto conserva su precio regular."}


@router.post(
    "/markdown-recommendations/auto-apply-all",
    response_model=List[ApplyMarkdownResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Piloto automático: aplicar masivamente todas las ofertas recomendadas por ML"
)
def auto_apply_all_markdown(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Aplica automáticamente el descuento recomendado por el modelo para todas las prendas
    con riesgo de estancamiento detectadas en el tenant.
    """
    tenant_id = _resolve_tenant_id(db, current_user)
    recs = MLAnalyticsService.get_markdown_recommendations(db, tenant_id)
    applied = []
    for r in recs:
        if r.estado != "ACTIVA":
            try:
                res = MLAnalyticsService.apply_markdown_recommendation(
                    db=db,
                    tenant_id=tenant_id,
                    product_id=UUID(r.producto_id),
                    payload=ApplyMarkdownRequest(
                        descuento_pct=r.descuento_sugerido_pct,
                        fecha_inicio=r.fecha_inicio_sugerida,
                        fecha_fin=r.fecha_fin_sugerida,
                        motivo=f"Piloto automático ML ({r.descuento_sugerido_pct:.0f}% OFF)"
                    )
                )
                applied.append(res)
            except Exception:
                pass
    return applied
