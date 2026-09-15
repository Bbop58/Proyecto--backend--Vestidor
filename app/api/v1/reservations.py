import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import require_permission, get_current_user
from app.models.reservation import ReservationStatus
from app.schemas.reservation import (
    ReservationCreate,
    ReservationResponse,
    ExpireReservationsResponse,
)
from app.services.reservation_service import ReservationService

router = APIRouter(prefix="/reservas", tags=["Reservas"])


@router.post(
    "",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una nueva reserva"
)
def create_reservation(
    data: ReservationCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return ReservationService.create_reservation(db, data, cliente_id=current_user.id)


@router.get(
    "/mis-reservas",
    response_model=List[ReservationResponse],
    summary="Listar mis reservas (cliente)"
)
def get_my_reservations(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return ReservationService.get_client_reservations(db, cliente_id=current_user.id)


@router.get(
    "",
    response_model=List[ReservationResponse],
    dependencies=[Depends(require_permission("reservas.list"))],
    summary="Listar reservas con filtros (Staff / Encargado / Admin)"
)
def get_reservations(
    sucursal_id: Optional[uuid.UUID] = Query(None),
    estado: Optional[ReservationStatus] = Query(None),
    cliente_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    return ReservationService.get_reservations(
        db,
        sucursal_id=sucursal_id,
        estado=estado,
        cliente_id=cliente_id,
        limit=limit,
        offset=offset
    )


@router.get(
    "/{reserva_id}",
    response_model=ReservationResponse,
    summary="Ver detalle de una reserva"
)
def get_reservation(
    reserva_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return ReservationService.get_reservation_by_id(db, reserva_id)


@router.patch(
    "/{reserva_id}/cancelar",
    response_model=ReservationResponse,
    summary="Cancelar una reserva pendiente"
)
def cancel_reservation(
    reserva_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    is_staff = any(r.name in ["admin", "encargado"] for r in getattr(current_user, "roles", []))
    return ReservationService.cancel_reservation(
        db,
        reserva_id=reserva_id,
        user_id=current_user.id,
        is_staff=is_staff
    )


@router.patch(
    "/{reserva_id}/preparar",
    response_model=ReservationResponse,
    dependencies=[Depends(require_permission("reservas.update"))],
    summary="Marcar reserva como preparada para entrega"
)
def prepare_reservation(
    reserva_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return ReservationService.prepare_reservation(db, reserva_id, user_id=current_user.id)


@router.patch(
    "/{reserva_id}/recoger",
    response_model=ReservationResponse,
    dependencies=[Depends(require_permission("reservas.update"))],
    summary="Marcar reserva como recogida por el cliente"
)
def pickup_reservation(
    reserva_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return ReservationService.pickup_reservation(db, reserva_id, user_id=current_user.id)


@router.post(
    "/expirar-vencidas",
    response_model=ExpireReservationsResponse,
    dependencies=[Depends(require_permission("reservas.update"))],
    summary="Procesar y liberar stock de reservas vencidas"
)
def expire_reservations(
    db: Session = Depends(get_db)
):
    return ReservationService.expire_pending_reservations(db)
