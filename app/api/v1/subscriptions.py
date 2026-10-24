import uuid
from datetime import date
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user, get_current_tenant
from app.models.user import User
from app.models.tenant import Tenant
from app.models.subscription_plan import SubscriptionPlan
from app.models.subscription import Subscription
from app.models.branch import Branch
from app.schemas.subscription import (
    SubscriptionPlanResponse,
    TenantInfo,
    CurrentSubscriptionResponse,
    ChangePlanRequest
)

router = APIRouter(prefix="/subscriptions", tags=["SaaS Suscripciones y Multitenant"])


@router.get(
    "/current",
    response_model=CurrentSubscriptionResponse,
    summary="Consultar suscripción, plan y métricas de consumo de la empresa actual"
)
def get_current_subscription_info(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant)
):
    # Obtener suscripción activa
    sub = db.query(Subscription).filter(
        Subscription.tenant_id == tenant.id,
        Subscription.estado == "ACTIVA"
    ).order_by(Subscription.fecha_vencimiento.desc()).first()

    if not sub:
        # Si no tiene suscripción activa, asignar automáticamente el Plan Profesional
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

    # Conteo de sucursales activas del tenant
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

    # Actualizar o crear la suscripción
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
