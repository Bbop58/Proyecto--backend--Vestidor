from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission
from app.schemas.permission import GroupedPermissionsResponse
from app.services.role_service import RoleService

router = APIRouter(prefix="/permissions", tags=["Permisos"])


@router.get(
    "",
    response_model=List[GroupedPermissionsResponse],
    dependencies=[Depends(require_permission("roles.list"))],
    summary="Listar permisos agrupados por módulo"
)
def list_permissions(db: Session = Depends(get_db)):
    return RoleService.get_grouped_permissions(db)
