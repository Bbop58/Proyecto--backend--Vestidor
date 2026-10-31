import uuid
from decimal import Decimal
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.coupon import Coupon
from app.models.user import User
from app.models.tenant import Tenant
from app.schemas.coupon import (
    CouponCreate,
    CouponResponse,
    CouponValidateRequest,
    CouponValidateResponse,
)
from app.api.deps import get_current_user

router = APIRouter(prefix="/coupons", tags=["Cupones y Ofertas"])


def _resolve_tenant_id(db: Session, current_user: User) -> uuid.UUID:
    if current_user.tenant_id:
        return current_user.tenant_id
    first_tenant = db.query(Tenant).first()
    return first_tenant.id if first_tenant else uuid.uuid4()


@router.get("", response_model=List[CouponResponse], summary="Listar cupones de descuento del tenant")
def list_coupons(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tenant_id = _resolve_tenant_id(db, current_user)
    return db.query(Coupon).filter(Coupon.tenant_id == tenant_id).order_by(Coupon.created_at.desc()).all()


@router.post("", response_model=CouponResponse, status_code=status.HTTP_201_CREATED, summary="Crear un nuevo cupón")
def create_coupon(
    payload: CouponCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tenant_id = _resolve_tenant_id(db, current_user)
    code_upper = payload.codigo.strip().upper()

    existing = db.query(Coupon).filter(
        Coupon.tenant_id == tenant_id,
        Coupon.codigo == code_upper
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ya existe un cupón con el código '{code_upper}' en este comercio."
        )

    coupon = Coupon(
        tenant_id=tenant_id,
        codigo=code_upper,
        descripcion=payload.descripcion,
        porcentaje_descuento=payload.porcentaje_descuento,
        monto_minimo=payload.monto_minimo,
        fecha_vencimiento=payload.fecha_vencimiento,
        activo=payload.activo,
        max_usos=payload.max_usos,
    )
    db.add(coupon)
    db.commit()
    db.refresh(coupon)
    return coupon


@router.post("/validate", response_model=CouponValidateResponse, summary="Validar cupón de descuento y calcular monto")
def validate_coupon(
    payload: CouponValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tenant_id = _resolve_tenant_id(db, current_user)
    code_upper = payload.codigo.strip().upper()

    coupon = db.query(Coupon).filter(
        Coupon.tenant_id == tenant_id,
        Coupon.codigo == code_upper
    ).first()

    # Si no lo encuentra por tenant, buscar cupón global de plataforma
    if not coupon:
        coupon = db.query(Coupon).filter(Coupon.codigo == code_upper).first()

    if not coupon:
        return CouponValidateResponse(
            valido=False,
            mensaje=f"El cupón '{code_upper}' no existe o no es aplicable en esta tienda.",
            codigo=code_upper,
            monto_descuento=Decimal("0.00"),
            monto_total=payload.monto_subtotal
        )

    valid, reason = coupon.is_valid(payload.monto_subtotal)
    if not valid:
        return CouponValidateResponse(
            valido=False,
            mensaje=reason,
            codigo=code_upper,
            monto_descuento=Decimal("0.00"),
            monto_total=payload.monto_subtotal
        )

    descuento_calculado = round((payload.monto_subtotal * coupon.porcentaje_descuento) / Decimal("100.00"), 2)
    total_con_descuento = max(Decimal("0.00"), payload.monto_subtotal - descuento_calculado)

    return CouponValidateResponse(
        valido=True,
        mensaje=f"Cupón aplicado exitosamente. Ahorras Bs. {descuento_calculado:.2f} ({coupon.porcentaje_descuento:.0f}% OFF)",
        codigo=coupon.codigo,
        porcentaje_descuento=coupon.porcentaje_descuento,
        monto_descuento=descuento_calculado,
        monto_total=total_con_descuento
    )
