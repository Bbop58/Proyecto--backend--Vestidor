import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.user import UserResponse, UserUpdate, UserAdminCreate, UserAdminUpdate
from app.schemas.auth import ChangePasswordRequest
from app.schemas.common import MessageResponse
from app.services.user_service import UserService
from app.services.auth_service import AuthService
from app.services.role_service import RoleService
from app.services.audit_service import AuditService
from app.api.deps import get_current_user, require_permission
from app.models.user import User

router = APIRouter(prefix="/users", tags=["Usuarios"])


def build_user_response(user: User, db: Session) -> UserResponse:
    res = UserResponse.model_validate(user)
    if user.role_id:
        res.permissions = RoleService.get_permission_codes(db, user.role_id)
    return res


@router.get(
    "",
    response_model=List[UserResponse],
    dependencies=[Depends(require_permission("users.list"))],
    summary="Listar usuarios"
)
def list_users(db: Session = Depends(get_db)):
    users = UserService.get_all(db)
    return [build_user_response(u, db) for u in users]


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Obtener perfil del usuario autenticado"
)
def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return build_user_response(current_user, db)


@router.put(
    "/me",
    response_model=UserResponse,
    summary="Actualizar perfil del usuario autenticado"
)
def update_me(
    user_update: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    updated_user = UserService.update(db, current_user, user_update)
    return build_user_response(updated_user, db)


@router.post(
    "/me/change-password",
    response_model=MessageResponse,
    summary="Cambiar contraseña del usuario autenticado"
)
def change_my_password(
    change_pwd_in: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    AuthService.change_password(db, current_user, change_pwd_in)
    return MessageResponse(message="Contraseña actualizada exitosamente")


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("users.create"))],
    summary="Crear nuevo usuario con rol asignado (Admin)"
)
def create_user(
    user_in: UserAdminCreate,
    admin_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user = UserService.create_by_admin(db, user_in)
    AuditService.log(
        db=db,
        action=f"Creación de usuario: {user.full_name} ({user.email})",
        module="Usuarios",
        user=admin_user
    )
    return build_user_response(user, db)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_permission("users.list"))],
    summary="Obtener usuario por ID (Admin)"
)
def get_user_by_id(
    user_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    user = UserService.get_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )
    return build_user_response(user, db)


@router.put(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_permission("users.update"))],
    summary="Actualizar usuario y su rol (Admin)"
)
def update_user(
    user_id: uuid.UUID,
    user_in: UserAdminUpdate,
    admin_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user = UserService.get_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )
    updated = UserService.update_by_admin(db, user, user_in, admin_user)
    AuditService.log(
        db=db,
        action=f"Modificación de usuario: {updated.full_name} ({updated.email})",
        module="Usuarios",
        user=admin_user
    )
    return build_user_response(updated, db)


@router.patch(
    "/{user_id}/toggle-active",
    response_model=UserResponse,
    dependencies=[Depends(require_permission("users.update"))],
    summary="Activar o desactivar cuenta de usuario (Admin)"
)
def toggle_user_active(
    user_id: uuid.UUID,
    admin_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user = UserService.get_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )
    updated = UserService.toggle_active(db, user, admin_user)
    estado = "activada" if updated.is_active else "desactivada"
    AuditService.log(
        db=db,
        action=f"Cuenta de usuario {updated.email} {estado}",
        module="Usuarios",
        user=admin_user
    )
    return build_user_response(updated, db)



