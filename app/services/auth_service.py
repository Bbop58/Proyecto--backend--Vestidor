from datetime import datetime, timezone
from typing import Tuple, Optional
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.token_blacklist import TokenBlacklist
from app.schemas.auth import RegisterRequest, LoginRequest
from app.schemas.user import UserCreate
from app.services.user_service import UserService
from app.core.security import (
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token
)
from app.core.exceptions import (
    UserAlreadyExistsException,
    CredentialsException,
    TokenRevokedException,
    InactiveUserException
)


class AuthService:
    @staticmethod
    def register(db: Session, register_in: RegisterRequest) -> Tuple[User, str, str]:
        existing_user = UserService.get_by_email(db, register_in.email)
        if existing_user:
            raise UserAlreadyExistsException()
        
        user_create = UserCreate(
            email=register_in.email,
            full_name=register_in.full_name,
            password=register_in.password
        )
        user = UserService.create(db, user_create)
        
        access_token_data = create_access_token(subject=str(user.id))
        refresh_token_data = create_refresh_token(subject=str(user.id))
        
        return user, access_token_data["token"], refresh_token_data["token"]

    @staticmethod
    def login(db: Session, login_in: LoginRequest) -> Tuple[User, str, str]:
        user = UserService.get_by_email(db, login_in.email)
        if not user:
            raise CredentialsException(detail="Correo electronico o contrasena incorrectos")
        
        if not verify_password(login_in.password, user.hashed_password):
            raise CredentialsException(detail="Correo electronico o contrasena incorrectos")
        
        if not user.is_active:
            raise InactiveUserException()
        
        access_token_data = create_access_token(subject=str(user.id))
        refresh_token_data = create_refresh_token(subject=str(user.id))
        
        return user, access_token_data["token"], refresh_token_data["token"]

    @staticmethod
    def refresh_tokens(db: Session, refresh_token_str: str) -> Tuple[User, str, str]:
        try:
            payload = decode_token(refresh_token_str)
            if payload.get("type") != "refresh":
                raise CredentialsException(detail="Tipo de token invalido")
            
            jti = payload.get("jti")
            user_id = payload.get("sub")
            if not jti or not user_id:
                raise CredentialsException()
            
            # Check blacklist
            is_revoked = db.query(TokenBlacklist).filter(TokenBlacklist.token_jti == jti).first()
            if is_revoked:
                raise TokenRevokedException()
            
            user = UserService.get_by_id(db, user_id)
            if not user or not user.is_active:
                raise CredentialsException(detail="Usuario no encontrado o inactivo")
            
            # Blacklist the old refresh token
            exp_timestamp = payload.get("exp")
            expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
            revoked_token = TokenBlacklist(token_jti=jti, expires_at=expires_at)
            db.add(revoked_token)
            db.commit()
            
            # Issue new pair
            new_access = create_access_token(subject=str(user.id))
            new_refresh = create_refresh_token(subject=str(user.id))
            
            return user, new_access["token"], new_refresh["token"]
        except Exception as e:
            if isinstance(e, (CredentialsException, TokenRevokedException, InactiveUserException)):
                raise e
            raise CredentialsException(detail="Token de actualizacion invalido o expirado")

    @staticmethod
    def logout(db: Session, token_payload: dict, refresh_token_str: Optional[str] = None) -> None:
        try:
            jti = token_payload.get("jti")
            exp_timestamp = token_payload.get("exp")
            if jti and exp_timestamp:
                expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                if not db.query(TokenBlacklist).filter(TokenBlacklist.token_jti == jti).first():
                    db.add(TokenBlacklist(token_jti=jti, expires_at=expires_at))
            
            if refresh_token_str:
                try:
                    ref_payload = decode_token(refresh_token_str)
                    ref_jti = ref_payload.get("jti")
                    ref_exp = ref_payload.get("exp")
                    if ref_jti and ref_exp:
                        ref_expires_at = datetime.fromtimestamp(ref_exp, tz=timezone.utc)
                        if not db.query(TokenBlacklist).filter(TokenBlacklist.token_jti == ref_jti).first():
                            db.add(TokenBlacklist(token_jti=ref_jti, expires_at=ref_expires_at))
                except Exception:
                    pass
            
            db.commit()
        except Exception:
            db.rollback()
