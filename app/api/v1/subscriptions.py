import uuid
from datetime import date
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user, get_current_tenant, require_permission
from app.models.user import User
from app.models.tenant import Tenant
from app.models.subscription_plan import SubscriptionPlan
from app.models.subscription import Subscription
from app.models.branch import Branch
from app.models.role import Role
from app.core.security import get_password_hash
from app.schemas.subscription import (
    SubscriptionPlanResponse,
    TenantInfo,
    CurrentSubscriptionResponse,
    ChangePlanRequest,
    TenantBrandingUpdateRequest,
    TenantOverviewItem,
    SaaSSummaryResponse,
    TenantCreateRequest
)

router = APIRouter(prefix="/subscriptions", tags=["SaaS Suscripciones y Multitenant"])


@router.get(
    "/public/tenants",
    response_model=List[TenantInfo],
    summary="Listado público de empresas activas con su branding para la pantalla de Login"
)
def list_public_tenants(
    db: Session = Depends(get_db)
):
    tenants = db.query(Tenant).filter(Tenant.activo == True).order_by(Tenant.nombre.asc()).all()
    return [TenantInfo.model_validate(t) for t in tenants]


@router.get(
    "/current",
    response_model=CurrentSubscriptionResponse,
    summary="Consultar suscripción, plan y métricas de consumo de la empresa actual"
)
def get_current_subscription_info(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant)
):
    sub = db.query(Subscription).filter(
        Subscription.tenant_id == tenant.id,
        Subscription.estado == "ACTIVA"
    ).order_by(Subscription.fecha_vencimiento.desc()).first()

    if not sub:
        pro_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.codigo == "PROFESIONAL").first()
        if not pro_plan:
            pro_plan = SubscriptionPlan(
                codigo="PROFESIONAL",
                nombre="Plan Profesional",
                descripcion="Hasta 10 sucursales, Reportes con IA e Inteligencia Artificial",
                max_sucursales=10,
                permite_reportes_ia=True,
                permite_offline=True,
                precio_mensual_bob=350.00
            )
            db.add(pro_plan)
            db.flush()

        sub = Subscription(
            tenant_id=tenant.id,
            plan_id=pro_plan.id,
            estado="ACTIVA",
            fecha_inicio=date.today(),
            fecha_vencimiento=date.fromordinal(date.today().toordinal() + 365),
            notas="Suscripción asignada por defecto"
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)

    plan = sub.plan

    sucursales_usadas = db.query(Branch).filter(
        Branch.tenant_id == tenant.id,
        Branch.activa == True
    ).count()

    sucursales_max = plan.max_sucursales
    sucursales_disponibles = max(0, sucursales_max - sucursales_usadas)
    porcentaje = round((sucursales_usadas / sucursales_max) * 100, 1) if sucursales_max > 0 else 100.0
    dias_restantes = max(0, (sub.fecha_vencimiento - date.today()).days)

    mensaje_alerta = None
    if sucursales_usadas >= sucursales_max:
        mensaje_alerta = f"Ha alcanzado el límite máximo de {sucursales_max} sucursales activas para el plan {plan.nombre}."

    return CurrentSubscriptionResponse(
        tenant=TenantInfo.model_validate(tenant),
        plan=SubscriptionPlanResponse.model_validate(plan),
        estado=sub.estado,
        fecha_inicio=sub.fecha_inicio,
        fecha_vencimiento=sub.fecha_vencimiento,
        dias_restantes=dias_restantes,
        sucursales_usadas=sucursales_usadas,
        sucursales_maximas=sucursales_max,
        sucursales_disponibles=sucursales_disponibles,
        porcentaje_sucursales_usadas=porcentaje,
        permite_reportes_ia=plan.permite_reportes_ia,
        permite_offline=plan.permite_offline,
        mensaje_alerta=mensaje_alerta
    )


@router.get(
    "/plans",
    response_model=List[SubscriptionPlanResponse],
    summary="Listar todos los planes de suscripción disponibles"
)
def list_subscription_plans(
    db: Session = Depends(get_db)
):
    plans = db.query(SubscriptionPlan).filter(SubscriptionPlan.activo == True).order_by(SubscriptionPlan.max_sucursales.asc()).all()
    return [SubscriptionPlanResponse.model_validate(p) for p in plans]


@router.post(
    "/change-plan",
    response_model=CurrentSubscriptionResponse,
    summary="Cambiar o simular cambio de plan de suscripción de la empresa (Básico <-> Profesional)"
)
def change_tenant_plan(
    request: ChangePlanRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant)
):
    target_plan = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.codigo == request.plan_codigo.upper().strip(),
        SubscriptionPlan.activo == True
    ).first()

    if not target_plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"El plan con código '{request.plan_codigo}' no existe."
        )

    sub = db.query(Subscription).filter(
        Subscription.tenant_id == tenant.id
    ).order_by(Subscription.fecha_vencimiento.desc()).first()

    if sub:
        sub.plan_id = target_plan.id
        sub.estado = "ACTIVA"
        sub.notas = request.notas
        sub.fecha_vencimiento = date.fromordinal(date.today().toordinal() + 365)
    else:
        sub = Subscription(
            tenant_id=tenant.id,
            plan_id=target_plan.id,
            estado="ACTIVA",
            fecha_inicio=date.today(),
            fecha_vencimiento=date.fromordinal(date.today().toordinal() + 365),
            notas=request.notas
        )
        db.add(sub)

    db.commit()
    db.refresh(sub)

    return get_current_subscription_info(db=db, tenant=tenant)


@router.put(
    "/branding",
    response_model=TenantInfo,
    summary="Actualizar la identidad y branding de la empresa actual (Logo, Colores, Eslogan)"
)
def update_current_tenant_branding(
    request: TenantBrandingUpdateRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant)
):
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
    """Verifica si el usuario es el Super Administrador global de la plataforma SaaS (admin@sistema.com).
    Los administradores de inquilinos (ej. admin@modaexpress.com) solo administran su propia empresa."""
    if not user or not user.email:
        return False
    email = user.email.lower().strip()
    return email == "admin@sistema.com" or email.endswith("@sistema.com")


@router.get(
    "/admin/tenants-overview",
    response_model=SaaSSummaryResponse,
    summary="[SuperAdmin] Resumen gerencial consolidado de todas las empresas, sucursales y suscripciones"
)
def get_saas_tenants_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not is_platform_superadmin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: Solo el Super Administrador de la Plataforma SaaS (admin@sistema.com) puede ver todas las empresas."
        )

    tenants = db.query(Tenant).order_by(Tenant.created_at.asc()).all()
    overview_items: List[TenantOverviewItem] = []
    total_sucursales = 0
    total_usuarios = 0
    mrr_total = 0.0

    today = date.today()

    for t in tenants:
        sub = db.query(Subscription).filter(
            Subscription.tenant_id == t.id,
            Subscription.estado == "ACTIVA"
        ).order_by(Subscription.fecha_vencimiento.desc()).first()

        plan = sub.plan if sub else None
        plan_cod = plan.codigo if plan else "SIN_PLAN"
        plan_nom = plan.nombre if plan else "Sin Plan Asignado"
        plan_precio = float(plan.precio_mensual_bob) if plan else 0.0
        plan_max = plan.max_sucursales if plan else 0

        if t.activo and sub:
            mrr_total += plan_precio

        sucursales_count = db.query(Branch).filter(
            Branch.tenant_id == t.id,
            Branch.activa == True
        ).count()
        total_sucursales += sucursales_count

        users_count = db.query(User).filter(
            User.tenant_id == t.id,
            User.is_active == True
        ).count()
        total_usuarios += users_count

        f_inicio = sub.fecha_inicio if sub else today
        f_venc = sub.fecha_vencimiento if sub else today
        dias_rest = max(0, (f_venc - today).days) if sub else 0
        estado_sub = sub.estado if sub else "INACTIVA"

        overview_items.append(TenantOverviewItem(
            id=t.id,
            nombre=t.nombre,
            razon_social=t.razon_social,
            nit_rut=t.nit_rut,
            slug=t.slug,
            logo_url=t.logo_url,
            color_primario=t.color_primario or "#4f46e5",
            color_secundario=t.color_secundario or "#818cf8",
            eslogan=t.eslogan,
            email_contacto=t.email_contacto,
            telefono=t.telefono,
            activo=t.activo,
            created_at=t.created_at,
            plan_codigo=plan_cod,
            plan_nombre=plan_nom,
            precio_mensual_bob=plan_precio,
            sucursales_usadas=sucursales_count,
            sucursales_maximas=plan_max,
            usuarios_activos=users_count,
            fecha_inicio=f_inicio,
            fecha_vencimiento=f_venc,
            dias_restantes=dias_rest,
            estado_suscripcion=estado_sub
        ))

    return SaaSSummaryResponse(
        total_tenants=len(tenants),
        total_tenants_activos=len([t for t in tenants if t.activo]),
        total_sucursales_activas=total_sucursales,
        total_usuarios_activos=total_usuarios,
        ingresos_recurrentes_mensuales_bob=round(mrr_total, 2),
        tenants=overview_items
    )


@router.post(
    "/admin/tenants",
    response_model=TenantOverviewItem,
    status_code=status.HTTP_201_CREATED,
    summary="[SuperAdmin] Registrar una nueva empresa cliente en la plataforma SaaS"
)
def create_new_tenant(
    request: TenantCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not is_platform_superadmin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el Super Administrador de la Plataforma SaaS (admin@sistema.com) puede dar de alta nuevas empresas."
        )

    clean_slug = request.slug.lower().strip()
    existing = db.query(Tenant).filter(Tenant.slug == clean_slug).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ya existe una empresa registrada con el slug/identificador '{clean_slug}'."
        )

    plan = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.codigo == request.plan_codigo.upper().strip(),
        SubscriptionPlan.activo == True
    ).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"El plan '{request.plan_codigo}' no existe."
        )

    new_tenant = Tenant(
        nombre=request.nombre.strip(),
        razon_social=request.razon_social.strip(),
        nit_rut=request.nit_rut.strip(),
        slug=clean_slug,
        logo_url=request.logo_url,
        color_primario=request.color_primario or "#4f46e5",
        color_secundario=request.color_secundario or "#818cf8",
        eslogan=request.eslogan,
        telefono=request.telefono,
        email_contacto=request.email_contacto,
        activo=True
    )
    db.add(new_tenant)
    db.flush()

    today = date.today()
    sub = Subscription(
        tenant_id=new_tenant.id,
        plan_id=plan.id,
        estado="ACTIVA",
        fecha_inicio=today,
        fecha_vencimiento=date.fromordinal(today.toordinal() + 365),
        notas="Suscripción anual asignada en alta de empresa"
    )
    db.add(sub)

    users_created_count = 0
    if request.admin_email:
        clean_email = request.admin_email.lower().strip()
        existing_u = db.query(User).filter(User.email == clean_email).first()
        if existing_u:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El correo electrónico del administrador ya está en uso."
            )
        admin_role = db.query(Role).filter(Role.name == "admin").first()
        new_admin = User(
            email=clean_email,
            full_name=request.admin_full_name.strip() if request.admin_full_name else f"Admin {new_tenant.nombre}",
            hashed_password=get_password_hash(request.admin_password or "Password123!"),
            role_id=admin_role.id if admin_role else None,
            tenant_id=new_tenant.id,
            is_active=True,
            is_verified=True
        )
        db.add(new_admin)
        users_created_count = 1

    db.commit()
    db.refresh(new_tenant)

    return TenantOverviewItem(
        id=new_tenant.id,
        nombre=new_tenant.nombre,
        razon_social=new_tenant.razon_social,
        nit_rut=new_tenant.nit_rut,
        slug=new_tenant.slug,
        logo_url=new_tenant.logo_url,
        color_primario=new_tenant.color_primario or "#4f46e5",
        color_secundario=new_tenant.color_secundario or "#818cf8",
        eslogan=new_tenant.eslogan,
        email_contacto=new_tenant.email_contacto,
        telefono=new_tenant.telefono,
        activo=new_tenant.activo,
        created_at=new_tenant.created_at,
        plan_codigo=plan.codigo,
        plan_nombre=plan.nombre,
        precio_mensual_bob=float(plan.precio_mensual_bob),
        sucursales_usadas=0,
        sucursales_maximas=plan.max_sucursales,
        usuarios_activos=users_created_count,
        fecha_inicio=today,
        fecha_vencimiento=date.fromordinal(today.toordinal() + 365),
        dias_restantes=365,
        estado_suscripcion="ACTIVA"
    )


@router.put(
    "/admin/tenants/{tenant_id}/toggle-status",
    response_model=TenantInfo,
    summary="[SuperAdmin] Suspender o reactivar una empresa inquilina"
)
def toggle_tenant_status(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not is_platform_superadmin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: Solo el Super Administrador de la Plataforma SaaS puede suspender o activar empresas."
        )

    t = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    t.activo = not t.activo
    db.commit()
    db.refresh(t)
    return TenantInfo.model_validate(t)
