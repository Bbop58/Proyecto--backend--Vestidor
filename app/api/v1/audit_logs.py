import os
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Header, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission
from app.models.user import User
from app.schemas.audit_log import AuditLogResponse
from app.services.audit_service import AuditService

DEVELOPER_SECRET_KEY = os.getenv("DEVELOPER_SECRET_KEY", "DEV-SI2-SECURE-KEY-2026!")

router = APIRouter(prefix="/bitacora", tags=["Bitácora y Auditoría"])


@router.post("/verify-key", summary="Validar Llave del Desarrollador para acceso a Bitácora")
def verify_developer_key(
    payload: dict,
    current_user: User = Depends(require_permission("bitacora.list"))
):
    """
    Verifica si la llave maestra del desarrollador proporcionada es válida.
    """
    key = payload.get("developer_key", "").strip()
    if key == DEVELOPER_SECRET_KEY:
        return {"valid": True, "message": "Llave del Desarrollador verificada con éxito"}
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Llave de Desarrollador incorrecta. Acceso denegado por políticas de confidencialidad."
    )


@router.get("", response_model=List[AuditLogResponse], summary="Listar registros de la bitácora del sistema")
def get_audit_logs(
    query: Optional[str] = Query(None, description="Búsqueda por usuario, correo o acción"),
    module: Optional[str] = Query(None, description="Filtrar por módulo"),
    role: Optional[str] = Query(None, description="Filtrar por rol"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    x_developer_key: Optional[str] = Header(None, alias="X-Developer-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("bitacora.list"))
):
    """
    Endpoint exclusivo para Administradores con permiso 'bitacora.list' y Llave del Desarrollador.
    Retorna la lista de eventos ocurridos en el sistema con su IP correspondiente.
    """
    if not x_developer_key or x_developer_key.strip() != DEVELOPER_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: Se requiere la Llave Maestra del Desarrollador (Developer Key) para descifrar la bitácora confidencial."
        )

    return AuditService.get_logs(
        db=db,
        query=query,
        module=module,
        role=role,
        limit=limit,
        offset=offset
    )
