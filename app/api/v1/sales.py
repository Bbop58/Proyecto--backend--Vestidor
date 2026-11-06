import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import require_permission, get_current_user, get_history_user
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


def get_sales_user(db: Session = Depends(get_db), user=Depends(get_current_user)):
    SaleService._actor_tenant(db, user.id)
    return user


# ─────────────────────────── REPORTES ───────────────────────────────────────

@router.get(
    "/reportes/resumen",
    response_model=SalesSummaryReport,
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Resumen general de ventas e ingresos"
)
def get_summary_report(
    sucursal_id: Optional[uuid.UUID] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_sales_user)
):
    return SaleService.get_summary_report(db, sucursal_id=sucursal_id, tenant_id=current_user.tenant_id)


@router.get(
    "/reportes/top-productos",
    response_model=List[TopProductReport],
    dependencies=[Depends(require_permission("ventas.reports"))],
    summary="Top productos y variantes más vendidas"
)
def get_top_products(
    sucursal_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user=Depends(get_sales_user)
):
    return SaleService.get_top_products(db, sucursal_id=sucursal_id, limit=limit, tenant_id=current_user.tenant_id)


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
    current_user=Depends(get_sales_user)
):
    sale = SaleService.create_presencial_sale(db, data, cajero_id=current_user.id)
    recibo = sale.get("numero_recibo") if isinstance(sale, dict) else getattr(sale, "numero_recibo", "")
    total = sale.get("monto_total") if isinstance(sale, dict) else getattr(sale, "monto_total", 0.0)
    AuditService.log(
        db=db,
        action=f"Venta presencial #{recibo} - Total: Bs {total}",
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
    current_user=Depends(get_sales_user)
):
    sale = SaleService.create_digital_sale(db, data, cliente_id=current_user.id)
    recibo = sale.get("numero_recibo") if isinstance(sale, dict) else getattr(sale, "numero_recibo", "")
    total = sale.get("monto_total") if isinstance(sale, dict) else getattr(sale, "monto_total", 0.0)
    AuditService.log(
        db=db,
        action=f"Compra digital #{recibo} - Total: Bs {total}",
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
    current_user=Depends(get_history_user)
):
    """Retorna las compras digitales del cliente que realiza la petición."""
    return SaleService.get_sales(
        db,
        cliente_id=current_user.id,
        tenant_id=current_user.tenant_id,
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
    db: Session = Depends(get_db),
    current_user=Depends(get_sales_user)
):
    return SaleService.get_sales(
        db,
        sucursal_id=sucursal_id,
        tipo=tipo,
        estado=estado,
        cajero_id=cajero_id,
        cliente_id=cliente_id,
        limit=limit,
        offset=offset,
        tenant_id=current_user.tenant_id
    )


@router.get(
    "/{venta_id}",
    response_model=SaleResponse,
    dependencies=[Depends(require_permission("ventas.list"))],
    summary="Ver detalle de una venta y recibo"
)
def get_sale(
    venta_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_sales_user)
):
    return SaleService.get_sale_by_id(db, venta_id, tenant_id=current_user.tenant_id)


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
    current_user=Depends(get_sales_user)
):
    from app.services.payment_flow_service import PaymentFlowService
    res = PaymentFlowService.refund_sale(db, current_user, venta_id, data.motivo)
    recibo = res.get("numero_recibo") if isinstance(res, dict) else getattr(res, "numero_recibo", "")
    AuditService.log(
        db=db,
        action=f"Anulación de venta #{recibo} - Motivo: {data.motivo}",
        module="Ventas",
        user=current_user
    )
    return res


@router.get(
    "/{venta_id}/nota-venta",
    summary="Obtener la Nota de Venta Oficial formateada para impresión térmica y reporte A4"
)
def get_sale_receipt_note(
    venta_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_sales_user)
):
    from app.services.receipt_service import ReceiptService
    from app.services.role_service import RoleService
    sale = SaleService.get_sale_by_id(db, venta_id, tenant_id=current_user.tenant_id)
    permissions = RoleService.get_permission_codes(db, current_user.role_id) if current_user.role_id else []
    if sale["cliente_id"] != current_user.id and "ventas.list" not in permissions:
        raise HTTPException(status_code=404, detail="Venta no encontrada")
    return ReceiptService.get_sale_receipt(db, venta_id)
