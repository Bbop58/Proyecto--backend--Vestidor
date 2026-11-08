import uuid
from typing import Generator, Tuple, Optional
from fastapi import Depends, HTTPException, status, Request
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
        
        from app.services.mobile_session_service import MobileSessionService
        MobileSessionService.validate_access(db, payload)
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


def get_authenticated_identity(
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
    from app.models.tenant import Tenant
    if not user.tenant_id or not user.tenant:
        raise HTTPException(403,"La cuenta debe pertenecer a una empresa")
    from app.services.entitlement_service import EntitlementService
    if not user.tenant.activo and not EntitlementService.can_recover(user):
        raise HTTPException(403,'Empresa suspendida; solo recuperación contractual autorizada')
    if payload.get("tenant_id") != str(user.tenant_id):
        raise CredentialsException(detail="La sesión no corresponde a la empresa actual")
    from app.core.tenant_scope import bind_tenant
    from app.core.access_policy import resolve_access_scope, AccessScope
    scope = resolve_access_scope(user)
    if scope == AccessScope.DENIED:
        raise HTTPException(403, 'Cuenta de plataforma no habilitada o contexto inválido')
    bind_tenant(db,user.tenant_id,user.id)
    if scope == AccessScope.PLATFORM:
        db.info['platform_actor_id'] = user.id
    return user


def get_current_user(
    db: Session = Depends(get_db),
    payload: dict = Depends(get_current_token_payload),
    request: Request = None
) -> User:
    user = get_authenticated_identity(db, payload)
    if user.is_platform_admin:
        raise HTTPException(403, 'La cuenta de plataforma no tiene acceso al negocio')
    if not user.tenant.activo:raise HTTPException(403,'Empresa suspendida; acceso comercial deshabilitado')
    if request and request.method not in ('GET','HEAD','OPTIONS'):
        from app.services.entitlement_service import EntitlementService
        EntitlementService.check(db,user)
    return user


def get_history_user(db: Session=Depends(get_db),payload: dict=Depends(get_current_token_payload)):
    from app.core.access_policy import is_platform_candidate
    from app.core.tenant_scope import bind_tenant
    try:user=UserService.get_by_id(db,uuid.UUID(payload.get('sub','')))
    except (ValueError,TypeError):raise CredentialsException()
    if not user or not user.is_active or not user.tenant_id or not user.tenant:
        raise CredentialsException()
    if payload.get('tenant_id')!=str(user.tenant_id):raise CredentialsException()
    if is_platform_candidate(user):raise HTTPException(403,'La plataforma no accede a operaciones comerciales')
    bind_tenant(db,user.tenant_id,user.id)
    return user


def require_platform_admin(user: User = Depends(get_authenticated_identity)) -> User:
    if not user.is_platform_admin:
        raise HTTPException(403, 'Se requiere administración de plataforma')
    return user


def get_current_user_optional(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),request: Request=None
) -> Optional[User]:
    if not token:
        return None
    payload = get_current_token_payload(token,db)
    return get_current_user(db,payload,request)


def get_subscription_manager(db: Session=Depends(get_db),payload: dict=Depends(get_current_token_payload)):
    from app.core.access_policy import is_platform_candidate
    from app.core.tenant_scope import bind_tenant
    try:user=UserService.get_by_id(db,uuid.UUID(payload.get('sub','')))
    except (ValueError,TypeError):raise CredentialsException()
    if not user or not user.is_active or not user.tenant or user.tenant_id is None:
        raise CredentialsException()
    if payload.get('tenant_id')!=str(user.tenant_id):raise CredentialsException()
    if is_platform_candidate(user) or not user.is_verified:
        raise HTTPException(403,'Se requiere un administrador empresarial verificado')
    if 'subscriptions.manage' not in RoleService.get_permission_codes(db,user.role_id):
        raise HTTPException(403,'No tienes permiso para gestionar el contrato')
    bind_tenant(db,user.tenant_id,user.id)
    return user


def get_current_tenant(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.models.tenant import Tenant
    if not current_user.tenant_id:
        raise HTTPException(403,"La cuenta debe pertenecer a una empresa")
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id, Tenant.activo == True).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La empresa asociada a este usuario está inactiva o no existe."
        )
    return tenant


def get_current_subscription(
    tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    from app.models.subscription import Subscription
    sub = db.query(Subscription).filter(
        Subscription.tenant_id == tenant.id,
        Subscription.estado == "ACTIVA"
    ).order_by(Subscription.fecha_vencimiento.desc()).first()
    
    from app.services.entitlement_service import EntitlementService
    # The company was authenticated by get_current_tenant; resolve its actor.
    user=UserService.get_by_id(db,db.info.get('actor_id'))
    EntitlementService.check(db,user)

    return sub


def require_plan_feature(feature_name: str):
    """
    Verifica que el plan de la empresa del usuario permita una funcionalidad específica
    (ej: 'reportes_ia'). Si no lo permite, rechaza con HTTP 403.
    """
    def dependency(
        current_user: User=Depends(get_current_user), db: Session=Depends(get_db)
    ):
        from app.services.entitlement_service import EntitlementService
        return EntitlementService.check(db,current_user,feature_name)
    return dependency

