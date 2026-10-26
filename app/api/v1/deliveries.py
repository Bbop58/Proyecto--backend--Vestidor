import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import get_current_user, require_permission
from app.models.user import User
from app.schemas.delivery_rate import (
    DeliveryRateCreate,
    DeliveryRateUpdate,
    DeliveryRateResponse
)
from app.services.delivery_rate_service import delivery_rate_service

router = APIRouter(prefix="/delivery-rates", tags=["Envíos y Tarifas"])


@router.get(
    "",
    response_model=List[DeliveryRateResponse],
    summary="Listar tarifas de envío por sucursal/ciudad"
)
def list_delivery_rates(
    branch_id: Optional[uuid.UUID] = Query(None, description="Filtrar por sucursal"),
    only_active: bool = Query(True, description="Filtrar solo tarifas activas"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return delivery_rate_service.list_delivery_rates(
        db=db,
        tenant_id=current_user.tenant_id,
        branch_id=branch_id,
        only_active=only_active
    )


@router.post(
    "",
    response_model=DeliveryRateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("sucursales.update"))],
    summary="Crear una tarifa de envío para una sucursal"
)
def create_delivery_rate(
    data: DeliveryRateCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return delivery_rate_service.create_delivery_rate(
        db=db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        data=data,
        request=request
    )


@router.put(
    "/{rate_id}",
    response_model=DeliveryRateResponse,
    dependencies=[Depends(require_permission("sucursales.update"))],
    summary="Actualizar tarifa de envío"
)
def update_delivery_rate(
    rate_id: uuid.UUID,
    data: DeliveryRateUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return delivery_rate_service.update_delivery_rate(
        db=db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        rate_id=rate_id,
        data=data,
        request=request
    )


@router.delete(
    "/{rate_id}",
    dependencies=[Depends(require_permission("sucursales.update"))],
    summary="Desactivar tarifa de envío"
)
def delete_delivery_rate(
    rate_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return delivery_rate_service.delete_delivery_rate(
        db=db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        rate_id=rate_id,
        request=request
    )
