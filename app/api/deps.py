import uuid
from typing import Generator, Tuple, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import jwt
from app.database import get_db
from app.models.user import User
from app.models.token_blacklist import TokenBlacklist
from app.core.security import decode_token
from app.core.exceptions import CredentialsException, TokenRevokedException, InactiveUserException
from app.services.user_service import UserService
from app.services.role_service import RoleService


def require_permission(permission_code: str):
    def dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
    ) -> User:
        if not current_user.role_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes un rol asignado"
            )
        user_permissions = RoleService.get_permission_codes(db, current_user.role_id)
        if permission_code not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para acceder a este recurso"
            )
        return current_user
    return dependency


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login/swagger",
    auto_error=False
)


def get_current_token_payload(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> dict:
    if not token:
        raise CredentialsException(detail="No autenticado")
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise CredentialsException(detail="Tipo de token invalido")
        
        jti = payload.get("jti")
        if jti:
            revoked = db.query(TokenBlacklist).filter(TokenBlacklist.token_jti == jti).first()
            if revoked:
                raise TokenRevokedException()
        
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token de acceso ha expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (jwt.InvalidTokenError, Exception) as e:
        if isinstance(e, (HTTPException, TokenRevokedException)):
            raise e
        raise CredentialsException()


def get_current_user(
    db: Session = Depends(get_db),
    payload: dict = Depends(get_current_token_payload)
) -> User:
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise CredentialsException()
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise CredentialsException()
    
    user = UserService.get_by_id(db, user_id)
    if not user:
        raise CredentialsException(detail="Usuario no encontrado")
    if not user.is_active:
        raise InactiveUserException()
    return user


def get_current_user_optional(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Optional[User]:
    if not token:
        return None
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_id_str = payload.get("sub")
        if not user_id_str:
            return None
        user_id = uuid.UUID(user_id_str)
        user = UserService.get_by_id(db, user_id)
        if user and user.is_active:
            return user
    except Exception:
        return None
    return None

