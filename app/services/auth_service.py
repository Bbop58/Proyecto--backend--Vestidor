from datetime import datetime, timezone
from typing import Tuple, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.token_blacklist import TokenBlacklist
from app.schemas.auth import RegisterRequest, LoginRequest, ChangePasswordRequest
from app.schemas.user import UserCreate
from app.services.user_service import UserService
from app.core.security import (
    verify_password,
    get_password_hash,
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
    def _validate_access_scope(user):
        from app.core.access_policy import resolve_access_scope, AccessScope
        if resolve_access_scope(user) == AccessScope.DENIED:
            raise HTTPException(403, 'Cuenta de plataforma no habilitada o contexto inválido')

    @staticmethod
    def register(db: Session, register_in: RegisterRequest) -> Tuple[User, str, str]:
        from app.models.tenant import Tenant
        tenant = db.query(Tenant).filter_by(id=register_in.tenant_id,activo=True).first()
        if not tenant:
            raise HTTPException(404,"Empresa no disponible")
        if tenant.es_operadora:
            raise HTTPException(403, 'El registro público no crea operadores de plataforma')
        existing_user = UserService.get_by_email(db, register_in.email)
        if existing_user:
            raise UserAlreadyExistsException()
        from types import SimpleNamespace
        from app.services.entitlement_service import EntitlementService
        from uuid import uuid4
        EntitlementService.check(db,SimpleNamespace(id=uuid4(),tenant_id=tenant.id,tenant=tenant,
            is_active=True,role=None,is_verified=False))
        
        user_create = UserCreate(
            tenant_id=register_in.tenant_id,
            email=register_in.email,
            full_name=register_in.full_name,
            password=register_in.password
        )
        user = UserService.create(db, user_create)
        
        from app.models.tenant import Tenant
        if not user.tenant_id or not user.tenant:
            raise HTTPException(403,'La cuenta debe pertenecer a una empresa')
        from app.services.entitlement_service import EntitlementService
        if not user.tenant.activo and not EntitlementService.can_recover(user):
            raise HTTPException(403,'La empresa está suspendida; no hay acceso comercial')
        AuthService._validate_access_scope(user)
        access_token_data = create_access_token(subject=str(user.id), tenant_id=user.tenant_id)
        refresh_token_data = create_refresh_token(subject=str(user.id), tenant_id=user.tenant_id)
        
        return user, access_token_data["token"], refresh_token_data["token"]

    @staticmethod
    def login(db: Session, login_in: LoginRequest) -> Tuple[User, str, str]:
        user = UserService.get_by_email(db, login_in.email)
        if not user:
            raise CredentialsException(detail="Correo electronico o contrasena incorrectos")
        
        if login_in.tenant_id and login_in.tenant_id != user.tenant_id:
            raise CredentialsException(detail="Correo electrónico o contraseña incorrectos")

        if not verify_password(login_in.password, user.hashed_password):
            raise CredentialsException(detail="Correo electronico o contrasena incorrectos")
        
        if not user.is_active:
            raise InactiveUserException()
        
        from app.models.tenant import Tenant
        if not user.tenant_id or not user.tenant:
            raise HTTPException(403,'La cuenta debe pertenecer a una empresa')
        from app.services.entitlement_service import EntitlementService
        if not user.tenant.activo and not EntitlementService.can_recover(user):
            raise HTTPException(403,'La empresa está suspendida; no hay acceso comercial')
        AuthService._validate_access_scope(user)
        access_token_data = create_access_token(subject=str(user.id), tenant_id=user.tenant_id)
        refresh_token_data = create_refresh_token(subject=str(user.id), tenant_id=user.tenant_id)
        
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
            
            from app.models.tenant import Tenant
            if not user.tenant_id or not user.tenant:
                raise HTTPException(403,'La cuenta debe pertenecer a una empresa')
            from app.services.entitlement_service import EntitlementService
            if not user.tenant.activo and not EntitlementService.can_recover(user):
                raise HTTPException(403,'La empresa está suspendida; no hay acceso comercial')
            if payload.get("tenant_id") != str(user.tenant_id):
                raise CredentialsException(detail="La sesión no corresponde a la empresa actual")
            AuthService._validate_access_scope(user)
            # Blacklist the old refresh token
            exp_timestamp = payload.get("exp")
            expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
            revoked_token = TokenBlacklist(token_jti=jti, expires_at=expires_at)
            db.add(revoked_token)
            db.commit()
            
            # Issue new pair
            new_access = create_access_token(subject=str(user.id), tenant_id=user.tenant_id)
            new_refresh = create_refresh_token(subject=str(user.id), tenant_id=user.tenant_id)
            
            return user, new_access["token"], new_refresh["token"]
        except Exception as e:
            if isinstance(e, (HTTPException, CredentialsException, TokenRevokedException, InactiveUserException)):
                raise e
            raise CredentialsException(detail="Token de actualizacion invalido o expirado")

    @staticmethod
    def logout(db: Session, token_payload: dict, refresh_token_str: Optional[str] = None) -> None:
        if refresh_token_str:
            try:
                reference = decode_token(refresh_token_str)
            except Exception:
                reference = None  # Revoking the access token still works with an expired refresh.
            if reference and (reference.get('sub') != token_payload.get('sub') or reference.get('type') != 'refresh'):
                raise HTTPException(400,'El token de renovación no pertenece a esta sesión')
        try:
            if token_payload.get('mobile_session_id'):
                from sqlalchemy import update
                from app.models.mobile_session import MobileSession
                from uuid import UUID
                table = MobileSession.__table__
                db.connection().execute(update(table).where(
                    table.c.id == UUID(token_payload['mobile_session_id']),
                    table.c.user_id == UUID(token_payload['sub']),
                    table.c.tenant_id == UUID(token_payload['tenant_id'])
                ).values(revoked_at=datetime.now(timezone.utc)))
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

    @staticmethod
    def change_password(db: Session, user: User, data: ChangePasswordRequest) -> None:
        from app.services.mobile_session_service import MobileSessionService
        MobileSessionService.lock_user(db, user.id)
        db.refresh(user)
        if not verify_password(data.current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La contraseña actual es incorrecta."
            )
        
        if data.current_password == data.new_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La nueva contraseña debe ser diferente de la actual."
            )
        
        user.hashed_password = get_password_hash(data.new_password)
        MobileSessionService.revoke_user(db, user.id)
        db.commit()
        db.refresh(user)

