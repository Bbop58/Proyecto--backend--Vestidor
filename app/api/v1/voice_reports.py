from datetime import datetime
from fastapi import APIRouter, Depends, status, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user, require_permission, require_plan_feature
from app.models.user import User
from app.schemas.voice_report import (
    VoiceReportQueryRequest,
    VoiceReportQueryResponse,
    VoiceReportExcelExportRequest,
    VoiceReportEmailRequest,
)
from app.schemas.receipt import ReceiptEmailResult
from app.services.voice_report_service import VoiceReportService

router = APIRouter(prefix="/reportes-voz", tags=["Reportes por Voz con IA"])


@router.post(
    "/query",
    response_model=VoiceReportQueryResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports")), Depends(require_plan_feature("reportes_ia"))],
    summary="Ejecuta una consulta de reporte gerencial formulada en lenguaje natural o por voz"
)
def execute_voice_report_query(
    request: VoiceReportQueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Recibe una pregunta en español, usa Gemini para interpretarla en un objeto tipado,
    construye SQL PostgreSQL desde el catálogo aprobado, aplica el aislamiento de empresa,
    y ejecuta únicamente una consulta de solo lectura con límites y parámetros,
    devolviendo las filas y columnas para visualización en tabla y exportación a PDF.
    """
    return VoiceReportService.execute_voice_report(
        db=db,
        question=request.pregunta,
        limit=request.limite or 100
    )


@router.post(
    "/export-excel",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports")), Depends(require_plan_feature("reportes_ia"))],
    summary="Exportar resultados de la consulta a un archivo Excel (.xlsx) con formato corporativo"
)
def export_voice_report_to_excel(
    request: VoiceReportExcelExportRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Genera y descarga un archivo Excel profesional (.xlsx) con los datos del reporte,
    encabezados estilizados, formato de moneda en Bolivianos (Bs.) y totales automáticos.
    """
    excel_bytes = VoiceReportService.generate_excel_report(
        question=request.pregunta,
        interpretation=request.interpretacion or "Reporte Inteligente",
        columns=request.columnas,
        rows=request.filas
    )

    filename = f"Reporte_FICCT_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.post(
    "/email",
    response_model=ReceiptEmailResult,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.reports")), Depends(require_plan_feature("reportes_ia"))],
    summary="Enviar por correo el reporte actual en PDF y Excel"
)
def email_voice_report(
    request: VoiceReportEmailRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.voice_report_email_service import send_voice_report_email
    return send_voice_report_email(db,current_user,request)
