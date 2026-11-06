import uuid
from datetime import date
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user, get_current_tenant, require_permission, require_platform_admin
from app.models.user import User
from app.models.tenant import Tenant
from app.models.subscription_plan import SubscriptionPlan
from app.models.subscription import Subscription
from app.models.branch import Branch
from app.models.role import Role
from app.schemas.platform import PlatformCompanyCreate, PlatformCompanyResponse, PlatformCompanyList
from app.services.platform_company_service import PlatformCompanyService
from app.core.security import get_password_hash
from app.schemas.subscription import (
    SubscriptionPlanResponse,
    SubscriptionOfferResponse,
    TenantInfo,
    CurrentSubscriptionResponse,
    ChangePlanRequest,
    TenantBrandingUpdateRequest,
    TenantOverviewItem,
    SaaSSummaryResponse,
    TenantCreateRequest
)

from app.api.deps import get_subscription_manager, get_history_user
from app.schemas.subscription_contract import ContractChangeRequest
from app.services.subscription_contract_service import SubscriptionContractService

from app.api.secret_safe_route import SecretSafeRoute
router = APIRouter(prefix="/subscriptions", tags=["SaaS Suscripciones y Multitenant"], route_class=SecretSafeRoute)


@router.get(
    "/public/tenants",
    response_model=List[TenantInfo],
    summary="Listado público de empresas activas con su branding para la pantalla de Login y Directorio SaaS"
)
def list_public_tenants(
    db: Session = Depends(get_db)
):
    tenants = db.query(Tenant).filter(Tenant.activo == True, Tenant.es_operadora == False).order_by(Tenant.nombre.asc()).all()
    result: List[TenantInfo] = []
    for t in tenants:
        info = TenantInfo.model_validate(t)
        sub = db.query(Subscription).filter(
            Subscription.tenant_id == t.id,
            Subscription.estado == "ACTIVA"
        ).order_by(Subscription.fecha_vencimiento.desc()).first()
        if sub and sub.plan:
            info.plan_codigo = sub.plan.codigo
            info.plan_nombre = sub.plan.nombre
        else:
            info.plan_codigo = "BASICO"
            info.plan_nombre = "Plan Básico"
        result.append(info)
    return result


@router.get('/current',response_model=CurrentSubscriptionResponse)
def get_current_subscription_info(db: Session=Depends(get_db),current_user: User=Depends(get_history_user)):
    from app.services.entitlement_service import EntitlementService
    from app.models.subscription_plan_version import SubscriptionPlanVersion
    from sqlalchemy import select
    from datetime import timedelta
    from app.core.billing_calendar import business_today
    result=EntitlementService.resolve(db,current_user);sub=result['subscription'];term=result['term']
    if not sub:raise HTTPException(404,'No hay contrato; consulta /subscriptions/contract para solicitarlo')
    connection=db.connection()
    plan_id=sub['plan_id']
    if term and term['version_id']:
        plan_id=connection.execute(select(SubscriptionPlanVersion.plan_id).where(SubscriptionPlanVersion.id==term['version_id'])).scalar_one()
    plan=connection.execute(select(SubscriptionPlan.__table__).where(SubscriptionPlan.id==plan_id)).mappings().one()
    cap=result['max_sucursales'];usage=result['sucursales_usadas'];features=result['features']
    plan_view={'id':plan_id,'codigo':plan['codigo'],'nombre':plan['nombre'],'descripcion':plan['descripcion'],
        'max_sucursales':cap,'permite_reportes_ia':features['reportes_ia'],'permite_offline':features['offline'],
        'precio_mensual_bob':term['precio_mensual_bob'] if term else None,'activo':result['active']}
    end=term['fecha_fin']-timedelta(days=1) if term else sub['fecha_vencimiento']
    return dict(tenant=TenantInfo.model_validate(current_user.tenant),plan=plan_view,estado=sub['estado'],
        fecha_inicio=term['fecha_inicio'] if term else sub['fecha_inicio'],fecha_vencimiento=end,
        dias_restantes=max(0,(term['fecha_fin']-business_today()).days) if term else 0,
        sucursales_usadas=usage,sucursales_maximas=cap,sucursales_disponibles=max(0,cap-usage),
        porcentaje_sucursales_usadas=round(usage/cap*100,1) if cap else 100,
        permite_reportes_ia=features['reportes_ia'],permite_offline=features['offline'],
        mensaje_alerta='Servicio restringido: revisa tu contrato' if result['service_restricted'] else
            'Reduce las sucursales activas para cumplir el límite vigente' if result['exceso_sucursales'] else None,
        estado_efectivo=result['estado_efectivo'],service_restricted=result['service_restricted'],features=features,
        term_id=term['id'] if term else None,origen=term['origen'] if term else None)



@router.get(
    "/plans",
    response_model=List[SubscriptionOfferResponse],
    summary="Listar todos los planes de suscripción disponibles"
)
def list_subscription_plans(
    db: Session = Depends(get_db)
):
    from app.services.plan_catalog_service import PlanCatalogService
    return PlanCatalogService.list_published(db)


@router.post('/change-plan')
def change_tenant_plan(request: ContractChangeRequest | ChangePlanRequest,
    db: Session=Depends(get_db), admin: User=Depends(get_subscription_manager)):
    if isinstance(request,ChangePlanRequest):
        raise HTTPException(409,'Se requiere cotización vigente y aceptación explícita; el cambio queda pendiente de aprobación manual')
    return SubscriptionContractService.request_change(db,admin,request)



@router.put(
    "/branding",
    response_model=TenantInfo,
    summary="Actualizar la identidad y branding de la empresa actual (Logo, Colores, Eslogan)"
)
def update_current_tenant_branding(
    request: TenantBrandingUpdateRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
    admin: User = Depends(require_permission("roles.update"))
):
    if any(value is not None for value in (request.paypal_client_id,request.paypal_client_secret,request.paypal_mode)):
        raise HTTPException(409,'Configura PayPal desde Pagos de mi empresa')
    if request.nombre is not None and request.nombre.strip():
        tenant.nombre = request.nombre.strip()
    if request.logo_url is not None:
        tenant.logo_url = request.logo_url.strip() or None
    if request.color_primario is not None:
        tenant.color_primario = request.color_primario.strip()
    if request.color_secundario is not None:
        tenant.color_secundario = request.color_secundario.strip()
    if request.eslogan is not None:
        tenant.eslogan = request.eslogan.strip() or None
    if request.telefono is not None:
        tenant.telefono = request.telefono.strip() or None
    if request.email_contacto is not None:
        tenant.email_contacto = request.email_contacto.strip() or None

    db.commit()
    db.refresh(tenant)
    return TenantInfo.model_validate(tenant)


def is_platform_superadmin(user: User) -> bool:
    """Server grant plus reserved role and operator-company context."""
    return bool(user and user.is_platform_admin)


@router.get('/admin/tenants-overview', response_model=PlatformCompanyList)
def get_saas_tenants_overview(db: Session = Depends(get_db), current_user: User = Depends(require_platform_admin)):
    return PlatformCompanyService.list_companies(db, current_user)


def _execute_create_tenant(db: Session, request: TenantCreateRequest):
    raise HTTPException(503,'Altas nuevas deshabilitadas hasta configurar verificación privada de correo')



@router.post('/onboarding',status_code=201)
def public_onboarding(request: TenantCreateRequest,db: Session=Depends(get_db)):
    if not request.admin_email or not request.admin_password:
        raise HTTPException(400,'El correo y contraseña propios son requeridos; las altas están deshabilitadas')
    return _execute_create_tenant(db,request)



@router.post('/admin/tenants', response_model=PlatformCompanyResponse, status_code=201)
def create_new_tenant(request: PlatformCompanyCreate, db: Session = Depends(get_db),
                      current_user: User = Depends(require_platform_admin)):
    return PlatformCompanyService.create_company(db, current_user, request)


@router.put('/admin/tenants/{tenant_id}/toggle-status')
def toggle_tenant_status(tenant_id: uuid.UUID, current_user: User = Depends(require_platform_admin)):
    raise HTTPException(409, 'Usa /platform/companies/{id}/status con estado explícito, versión y clave de operación')
