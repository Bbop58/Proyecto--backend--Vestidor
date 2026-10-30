from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import require_permission, get_current_user
from app.models.user import User
from app.services.custom_report_service import CustomReportService

router = APIRouter(prefix="/reportes-personalizados", tags=["Constructor de Reportes Personalizados (Punto 5 SI2)"])


class CustomReportRequest(BaseModel):
    entity: str = Field(default="ventas", description="Entidad a reportar: ventas, inventario, clientes, reservas")
    columns: List[str] = Field(default=[], description="Lista de IDs de columnas deseadas")
    filters: Dict[str, Any] = Field(default={}, description="Filtros aplicados")
    sort_by: str = Field(default="fecha", description="Columna para ordenar")
    sort_order: str = Field(default="desc", description="asc o desc")


@router.get("/meta", summary="Obtener entidades y columnas disponibles para el constructor de reportes")
def get_report_builder_metadata(
    current_user: User = Depends(get_current_user)
):
    """
    Retorna la lista de módulos y campos disponibles para armar reportes dinámicos.
    """
    return CustomReportService.get_available_entities()


@router.post("/generate", summary="Generar reporte dinámico multi-columna filtrado")
def generate_custom_report(
    payload: CustomReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Construye y ejecuta la consulta personalizada retornando las filas y columnas exactas solicitadas.
    """
    return CustomReportService.execute_custom_report(
        db=db,
        entity=payload.entity,
        columns=payload.columns,
        filters=payload.filters,
        sort_by=payload.sort_by,
        sort_order=payload.sort_order,
        tenant_id=current_user.tenant_id
    )

