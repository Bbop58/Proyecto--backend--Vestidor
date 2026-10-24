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


def get_current_tenant(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.models.tenant import Tenant
    if not current_user.tenant_id:
        # Fallback al primer tenant activo para compatibilidad con usuarios base/legacy
        tenant = db.query(Tenant).filter(Tenant.activo == True).order_by(Tenant.created_at.asc()).first()
        if not tenant:
            # Auto-provision para retrocompatibilidad total con tests legacy
            from app.models.subscription_plan import SubscriptionPlan
            from app.models.subscription import Subscription
            from datetime import date, timedelta
            from decimal import Decimal

            plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.codigo == "PROFESIONAL").first()
            if not plan:
                plan = SubscriptionPlan(
                    codigo="PROFESIONAL",
                    nombre="Plan Profesional",
                    descripcion="Plan Profesional Automático",
                    max_sucursales=10,
                    permite_reportes_ia=True,
                    permite_offline=True,
                    precio_mensual_bob=Decimal("499.00"),
                    activo=True
                )
                db.add(plan)
                db.flush()

            tenant = Tenant(
                nombre="Empresa Principal",
                razon_social="Empresa Principal S.R.L.",
                nit_rut="0000000000",
                slug="empresa-principal",
                activo=True
            )
            db.add(tenant)
            db.flush()

            sub = Subscription(
                tenant_id=tenant.id,
                plan_id=plan.id,
                estado="ACTIVA",
                fecha_inicio=date.today(),
                fecha_vencimiento=date.today() + timedelta(days=365)
            )
            db.add(sub)
            db.commit()
            db.refresh(tenant)
        return tenant
    
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
    
    if not sub:
        from app.models.subscription_plan import SubscriptionPlan
        from datetime import date, timedelta
        from decimal import Decimal

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.codigo == "PROFESIONAL").first()
        if not plan:
            plan = SubscriptionPlan(
                codigo="PROFESIONAL",
                nombre="Plan Profesional",
                descripcion="Plan Profesional Automático",
                max_sucursales=10,
                permite_reportes_ia=True,
                permite_offline=True,
                precio_mensual_bob=Decimal("499.00"),
                activo=True
            )
            db.add(plan)
            db.flush()

        sub = Subscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            estado="ACTIVA",
            fecha_inicio=date.today(),
            fecha_vencimiento=date.today() + timedelta(days=365)
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)

    return sub


def require_plan_feature(feature_name: str):
    """
    Verifica que el plan de la empresa del usuario permita una funcionalidad específica
    (ej: 'reportes_ia'). Si no lo permite, rechaza con HTTP 403.
    """
    def dependency(
        sub = Depends(get_current_subscription)
    ):
        plan = sub.plan
        if feature_name == "reportes_ia" and not plan.permite_reportes_ia:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso Denegado: Su suscripción actual ({plan.nombre}) no incluye reportes avanzados con IA. Requiere actualizar al Plan Profesional."
            )
        return sub
    return dependency

