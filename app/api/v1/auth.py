from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    RefreshTokenRequest,
    LogoutRequest,
    ChangePasswordRequest
)
from app.schemas.common import MessageResponse
from app.schemas.user import UserResponse
from app.services.auth_service import AuthService
from app.services.user_service import UserService
from app.services.audit_service import AuditService
from app.api.deps import get_current_token_payload, get_current_user
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["Autenticacion"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar nuevo usuario"
)
def register(
    register_in: RegisterRequest,
    db: Session = Depends(get_db)
):
    user, access_token, refresh_token = AuthService.register(db, register_in)
    AuditService.log(db, action="Registro de nuevo usuario", module="Autenticación", user=user)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserService.build_user_response(db, user)
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Iniciar sesion (JSON)"
)
def login(
    login_in: LoginRequest,
    db: Session = Depends(get_db)
):
    user, access_token, refresh_token = AuthService.login(db, login_in)
    AuditService.log(db, action="Inicio de sesión", module="Autenticación", user=user)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserService.build_user_response(db, user)
    )


@router.post(
    "/login/swagger",
    response_model=TokenResponse,
    include_in_schema=False
)
def login_swagger(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    login_in = LoginRequest(email=form_data.username, password=form_data.password)
    user, access_token, refresh_token = AuthService.login(db, login_in)
    AuditService.log(db, action="Inicio de sesión (Swagger)", module="Autenticación", user=user)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserService.build_user_response(db, user)
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refrescar access token"
)
def refresh_token(
    refresh_in: RefreshTokenRequest,
    db: Session = Depends(get_db)
):
    user, access_token, refresh_token = AuthService.refresh_tokens(db, refresh_in.refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserService.build_user_response(db, user)
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Cerrar sesion e invalidar tokens"
)
def logout(
    logout_in: LogoutRequest = LogoutRequest(),
    payload: dict = Depends(get_current_token_payload),
    db: Session = Depends(get_db)
):
    user_id = payload.get("sub")
    if user_id:
        user = UserService.get_by_id(db, user_id)
        if user:
            AuditService.log(db, action="Cierre de sesión", module="Autenticación", user=user)

    AuthService.logout(db, payload, logout_in.refresh_token)
    return MessageResponse(message="Sesion cerrada exitosamente")


@router.post(
    "/change-password",
    response_model=MessageResponse,
    summary="Cambiar contraseña del usuario autenticado"
)
def change_password(
    change_pwd_in: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    AuthService.change_password(db, current_user, change_pwd_in)
    AuditService.log(db, action="Cambio de contraseña", module="Autenticación", user=current_user)
    return MessageResponse(message="Contraseña actualizada exitosamente")

