from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    RefreshTokenRequest,
    LogoutRequest
)
from app.schemas.common import MessageResponse
from app.schemas.user import UserResponse
from app.services.auth_service import AuthService
from app.services.user_service import UserService
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
    AuthService.logout(db, payload, logout_in.refresh_token)
    return MessageResponse(message="Sesion cerrada exitosamente")
