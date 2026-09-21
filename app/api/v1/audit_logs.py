from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission
from app.models.user import User
from app.schemas.audit_log import AuditLogResponse
from app.services.audit_service import AuditService

router = APIRouter(prefix="/bitacora", tags=["Bitácora y Auditoría"])


@router.get("", response_model=List[AuditLogResponse], summary="Listar registros de la bitácora del sistema")
def get_audit_logs(
    query: Optional[str] = Query(None, description="Búsqueda por usuario, correo o acción"),
    module: Optional[str] = Query(None, description="Filtrar por módulo"),
    role: Optional[str] = Query(None, description="Filtrar por rol"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("bitacora.list"))
):
    """
    Endpoint exclusivo para Administradores con permiso 'bitacora.list'.
    Retorna la lista de eventos ocurridos en el sistema ordenados cronológicamente.
    """
    return AuditService.get_logs(
        db=db,
        query=query,
        module=module,
        role=role,
        limit=limit,
        offset=offset
    )
