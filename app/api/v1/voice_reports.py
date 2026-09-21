from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user, require_permission
from app.models.user import User
from app.schemas.voice_report import VoiceReportQueryRequest, VoiceReportQueryResponse
from app.services.voice_report_service import VoiceReportService

router = APIRouter(prefix="/reportes-voz", tags=["Reportes por Voz con IA"])


@router.post(
    "/query",
    response_model=VoiceReportQueryResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Ejecuta una consulta de reporte gerencial formulada en lenguaje natural o por voz"
)
def execute_voice_report_query(
    request: VoiceReportQueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Recibe una pregunta en español sobre el negocio (ej. 'Top 5 productos más vendidos'),
    la traduce a SQL PostgreSQL seguro usando IA (Gemini Free Tier / Text-to-SQL),
    valida que sea estrictamente de solo lectura (SELECT) y la ejecuta contra la base de datos,
    devolviendo las filas y columnas para visualización en tabla y exportación a PDF.
    """
    return VoiceReportService.execute_voice_report(
        db=db,
        question=request.pregunta,
        limit=request.limite or 100
    )
