import uuid
from typing import List, Optional
from datetime import date
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import require_permission, get_current_user
from app.models.user import User
from app.schemas.product_exchange import (
    ProductExchangeCreate,
    ProductExchangeResponse,
    VerifyReceiptResponse,
)
from app.services.exchange_service import ExchangeService
from app.services.audit_service import AuditService

router = APIRouter(prefix="/cambios", tags=["Cambios de Prendas"])


@router.get(
    "/verificar-recibo/{numero_recibo}",
    response_model=VerifyReceiptResponse,
    dependencies=[Depends(require_permission("ventas.create"))],
    summary="Verificar validez, plazo (30 días) y prendas elegibles de un recibo"
)
def verify_receipt(
    numero_recibo: str,
    db: Session = Depends(get_db)
):
    return ExchangeService.verify_receipt(db, numero_recibo=numero_recibo)


@router.post(
    "",
    response_model=ProductExchangeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("ventas.create"))],
    summary="Registrar un cambio de prendas de vestir"
)
def create_exchange(
    data: ProductExchangeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = ExchangeService.create_exchange(db, data, cajero_id=current_user.id)

    # Registro en Bitácora de Auditoría
    try:
        AuditService.log_event(
            db=db,
            user_id=current_user.id,
            action="REGISTRAR_CAMBIO_PRENDA",
            module="Ventas / Cambios",
            details={
                "numero_cambio": result["numero_cambio"],
                "numero_recibo_original": result["numero_recibo_original"],
                "monto_devuelto": result["monto_total_devuelto"],
                "monto_entregado": result["monto_total_entregado"],
                "diferencia": result["diferencia"],
                "motivo": result["motivo"],
                "cajero": current_user.email
            }
        )
    except Exception:
        pass

    return result


@router.get(
    "",
    response_model=List[ProductExchangeResponse],
    dependencies=[Depends(require_permission("ventas.list"))],
    summary="Listar historial de cambios de prendas"
)
def list_exchanges(
    sucursal_id: Optional[uuid.UUID] = Query(None),
    fecha_inicio: Optional[date] = Query(None),
    fecha_fin: Optional[date] = Query(None),
    db: Session = Depends(get_db)
):
    return ExchangeService.list_exchanges(
        db,
        sucursal_id=sucursal_id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin
    )


@router.get(
    "/{id}",
    response_model=ProductExchangeResponse,
    dependencies=[Depends(require_permission("ventas.list"))],
    summary="Obtener detalle de un cambio de prendas por ID"
)
def get_exchange_by_id(
    id: uuid.UUID,
    db: Session = Depends(get_db)
):
    return ExchangeService.get_exchange_by_id(db, cambio_id=id)


@router.get(
    "/{id}/nota-devolucion",
    summary="Obtener la Nota Oficial de Cambio y Devolución con formato retail para impresión"
)
def get_exchange_receipt_note(
    id: uuid.UUID,
    db: Session = Depends(get_db)
):
    from app.services.receipt_service import ReceiptService
    return ReceiptService.get_exchange_receipt(db, id)
