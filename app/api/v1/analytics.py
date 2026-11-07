from uuid import UUID
from fastapi import APIRouter, Depends, Query, status, HTTPException, Response
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission, get_current_user, require_plan_feature
from app.models.user import User
from app.ml.ml_service import MLAnalyticsService
from app.schemas.analytics_recommendation import RecommendationSummaryV2, BranchRecommendationSnapshot
from app.schemas.analytics import (
    BusinessAnalysisResponse,
    BusinessTrainingResponse,
)

router = APIRouter(prefix="/analytics", tags=["Analítica Inteligente con Machine Learning"], dependencies=[Depends(require_plan_feature("analitica_avanzada"))])


def _resolve_tenant_id(db: Session, current_user: User) -> UUID:
    if not current_user.tenant_id:
        raise HTTPException(403,"La cuenta debe pertenecer a una empresa")
    return current_user.tenant_id


@router.post(
    "/train",
    response_model=BusinessTrainingResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports")), Depends(require_permission("analytics.train"))],
    summary="Entrenar o re-entrenar modelos de Machine Learning (Supervisado y No Supervisado)"
)
def train_models(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Actualiza manualmente los dos análisis propios; pueden ser insuficientes por separado."""
    return MLAnalyticsService.train_all(db,_resolve_tenant_id(db,current_user))


@router.get(
    "/forecast",
    response_model=BusinessAnalysisResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Obtener serie temporal de ventas y proyección de demanda futura (Supervisado)"
)
def get_sales_forecast(
    weeks_ahead: int = Query(default=4, ge=4, le=4, description="Horizonte fijo de cuatro semanas"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Lee el último análisis fechado, sin entrenar ni mover su horizonte guardado."""
    return MLAnalyticsService.get_demand_forecast(db,_resolve_tenant_id(db,current_user))


@router.get(
    "/segments",
    response_model=BusinessAnalysisResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Obtener segmentación de clientes RFM y clusters K-Means (No Supervisado)"
)
def get_customer_segments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Lee grupos descriptivos propios; no fuerza arquetipos ni inventa compradores."""
    return MLAnalyticsService.get_customer_segments(db,_resolve_tenant_id(db,current_user))


@router.get('/recommendations', response_model=RecommendationSummaryV2,
            dependencies=[Depends(require_permission('ventas.reports'))])
def get_recommendations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Read factual stock suggestions; no training, provider or commercial writes."""
    from app.services.analytics_branch_recommendations import read_recommendation_snapshot, summarize_snapshot
    return summarize_snapshot(read_recommendation_snapshot(db, _resolve_tenant_id(db, current_user)))


@router.get('/recommendations/report-data', response_model=BranchRecommendationSnapshot,
            dependencies=[Depends(require_permission('ventas.reports'))])
def get_recommendation_report_data(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Complete own-company snapshot for export, never limited to UI highlights."""
    from app.services.analytics_branch_recommendations import read_recommendation_snapshot
    return read_recommendation_snapshot(db, _resolve_tenant_id(db, current_user))


@router.get('/recommendations/export.xlsx', dependencies=[Depends(require_permission('ventas.reports'))])
def export_recommendations_excel(
    fingerprint: str = Query(pattern='^[a-f0-9]{64}$'),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    from app.services.analytics_branch_recommendations import read_recommendation_snapshot
    from app.services.analytics_recommendation_export import export_recommendation_xlsx
    snapshot = read_recommendation_snapshot(db, _resolve_tenant_id(db, current_user))
    if snapshot.fingerprint != fingerprint:
        raise HTTPException(409,'Los datos cambiaron. Actualiza las recomendaciones antes de descargar.')
    return Response(export_recommendation_xlsx(snapshot),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':'attachment; filename="recomendaciones_completas.xlsx"',
                 'Cache-Control':'no-store','X-Recommendation-Fingerprint':snapshot.fingerprint})


@router.post("/forecast/predict", dependencies=[Depends(require_permission("ventas.reports"))])
@router.get("/markdown-recommendations", dependencies=[Depends(require_permission("ventas.reports"))])
@router.post("/markdown-recommendations/auto-apply-all", dependencies=[Depends(require_permission("productos.update"))])
@router.post("/markdown-recommendations/{product_id}/apply", dependencies=[Depends(require_permission("productos.update"))])
@router.post("/markdown-recommendations/{product_id}/dismiss", dependencies=[Depends(require_permission("productos.update"))])
def retired_ml_route():
    # No parsed payload or product lookup: malformed old requests also have no effects.
    raise HTTPException(410,"Esta función de ML se retiró. Las ofertas manuales siguen en Productos.")
