from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.user import UserResponse, UserUpdate
from app.services.user_service import UserService
from app.services.role_service import RoleService
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

