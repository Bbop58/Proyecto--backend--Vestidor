import uuid
from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission, get_current_user
from app.models.user import User
from app.schemas.role import (
    RoleResponse,
    RoleWithPermissions,
    RoleCreate,
    RoleUpdate,
    RolePermissionAssign
)
from app.services.role_service import RoleService

router = APIRouter(prefix="/roles", tags=["Roles"])


@router.get(
    "",
    response_model=List[RoleWithPermissions],
    dependencies=[Depends(require_permission("roles.list"))],
    summary="Listar todos los roles con sus permisos"
)
def list_roles(db: Session = Depends(get_db)):
    return RoleService.get_roles(db)


@router.post(
    "",
    response_model=RoleWithPermissions,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("roles.create"))],
    summary="Crear un nuevo rol"
)
def create_role(role_in: RoleCreate, db: Session = Depends(get_db)):
    return RoleService.create_role(db, role_in)


@router.get(
    "/{role_id}",
    response_model=RoleWithPermissions,
    dependencies=[Depends(require_permission("roles.list"))],
    summary="Obtener detalle de un rol"
)
def get_role(role_id: uuid.UUID, db: Session = Depends(get_db)):
    return RoleService.get_role_by_id(db, role_id)


@router.put(
    "/{role_id}",
    response_model=RoleWithPermissions,
    dependencies=[Depends(require_permission("roles.update"))],
    summary="Actualizar un rol"
)
def update_role(
    role_id: uuid.UUID,
    role_in: RoleUpdate,
    db: Session = Depends(get_db)
):
    return RoleService.update_role(db, role_id, role_in)


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("roles.delete"))],
    summary="Eliminar un rol"
)
def delete_role(
    role_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    RoleService.delete_role(db, role_id, current_user.role_id)
    return None


@router.put(
    "/{role_id}/permissions",
    response_model=RoleWithPermissions,
    dependencies=[Depends(require_permission("roles.update"))],
    summary="Asignar permisos a un rol"
)
def assign_permissions(
    role_id: uuid.UUID,
    payload: RolePermissionAssign,
    db: Session = Depends(get_db)
):
    return RoleService.assign_permissions(db, role_id, payload.permission_ids)
