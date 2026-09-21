import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import require_permission, get_current_user
from app.models.sale import SaleType, SaleStatus
from app.schemas.sale import (
    PresencialSaleCreate,
    DigitalSaleCreate,
    SaleResponse,
    SaleCancel,
    SalesSummaryReport,
    TopProductReport,
)
from app.services.sale_service import SaleService
from app.services.audit_service import AuditService

router = APIRouter(prefix="/ventas", tags=["Ventas"])


# ─────────────────────────── REPORTES ───────────────────────────────────────

@router.get(
    "/reportes/resumen",
    response_model=SalesSummaryReport,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Resumen general de ventas e ingresos"
)
def get_summary_report(
    sucursal_id: Optional[uuid.UUID] = Query(None),
    db: Session = Depends(get_db)
):
    return SaleService.get_summary_report(db, sucursal_id=sucursal_id)


@router.get(
    "/reportes/top-productos",
    response_model=List[TopProductReport],
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Top productos y variantes más vendidas"
)
def get_top_products(
    sucursal_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    return SaleService.get_top_products(db, sucursal_id=sucursal_id, limit=limit)


# ─────────────────────────── REGISTRO DE VENTAS ────────────────────────────

@router.post(
    "/presencial",
    response_model=SaleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("ventas.create"))],
    summary="Registrar venta presencial en mostrador/caja"
)
def create_presencial_sale(
    data: PresencialSaleCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    sale = SaleService.create_presencial_sale(db, data, cajero_id=current_user.id)
    AuditService.log(
        db=db,
        action=f"Venta presencial #{sale.numero_comprobante} - Total: Bs {sale.total}",
        module="Ventas",
        user=current_user
    )
    return sale


@router.post(
    "/digital",
    response_model=SaleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar compra digital (App Móvil / Web)"
)
def create_digital_sale(
    data: DigitalSaleCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    sale = SaleService.create_digital_sale(db, data, cliente_id=current_user.id)
    AuditService.log(
        db=db,
        action=f"Compra digital #{sale.numero_comprobante} - Total: Bs {sale.total}",
        module="Ventas",
        user=current_user
    )
    return sale


@router.get(
    "/mis-compras",
    response_model=List[SaleResponse],
    summary="Historial de compras del cliente autenticado"
)
def get_my_purchases(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Retorna las compras digitales del cliente que realiza la petición."""
    return SaleService.get_sales(
        db,
        cliente_id=current_user.id,
        limit=100,
        offset=0
    )


# ─────────────────────────── CONSULTA Y ANULACIÓN ──────────────────────────

@router.get(
    "",
    response_model=List[SaleResponse],
    dependencies=[Depends(require_permission("ventas.list"))],
    summary="Listar historial de ventas"
)
def get_sales(
    sucursal_id: Optional[uuid.UUID] = Query(None),
    tipo: Optional[SaleType] = Query(None),
    estado: Optional[SaleStatus] = Query(None),
    cajero_id: Optional[uuid.UUID] = Query(None),
    cliente_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    return SaleService.get_sales(
        db,
        sucursal_id=sucursal_id,
        tipo=tipo,
        estado=estado,
        cajero_id=cajero_id,
        cliente_id=cliente_id,
        limit=limit,
        offset=offset
    )


@router.get(
    "/{venta_id}",
    response_model=SaleResponse,
    dependencies=[Depends(require_permission("ventas.list"))],
    summary="Ver detalle de una venta y recibo"
)
def get_sale(
    venta_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    return SaleService.get_sale_by_id(db, venta_id)


@router.patch(
    "/{venta_id}/cancelar",
    response_model=SaleResponse,
    dependencies=[Depends(require_permission("ventas.cancel"))],
    summary="Anular una venta y revertir stock al inventario"
)
def cancel_sale(
    venta_id: uuid.UUID,
    data: SaleCancel,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return SaleService.cancel_sale(
        db,
        venta_id=venta_id,
        motivo=data.motivo,
        usuario_id=current_user.id
    )
