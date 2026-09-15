"""
inventory.py — API v1 de Inventario y Stock (Fase 3)

Endpoints:
  GET    /inventario/sucursal/{sucursal_id}    — Stock de una sucursal
  PUT    /inventario/{inventario_id}           — Ajustar config (stock_minimo, ubicacion)
  POST   /inventario/ajuste                   — Ajuste manual de stock (+/-)
  POST   /inventario/recepcion                — Recepción de productos
  GET    /inventario/movimientos              — Historial de movimientos (filtros)
  GET    /inventario/alertas                  — Alertas de stock bajo / crítico
  GET    /inventario/disponibilidad           — Consulta pública de disponibilidad
"""
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import require_permission, get_current_user
from app.models.inventory_movement import MovementType
from app.schemas.inventory import (
    InventoryResponse,
    InventoryUpdate,
    ReceptionCreate,
    ReceptionResponse,
    StockAdjustment,
    MovementResponse,
    AvailabilityResponse,
    AlertResponse,
)
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/inventario", tags=["Inventario"])


# ─────────────────────────── STOCK POR SUCURSAL ────────────────────────────

@router.get(
    "/sucursal/{sucursal_id}",
    response_model=List[InventoryResponse],
    dependencies=[Depends(require_permission("inventario.list"))],
    summary="Ver stock de una sucursal"
)
def get_stock_by_branch(
    sucursal_id: uuid.UUID,
    solo_con_stock: bool = Query(False, description="Solo variantes con stock > 0"),
    db: Session = Depends(get_db)
):
    return InventoryService.get_stock_by_branch(db, sucursal_id, solo_con_stock)


@router.put(
    "/{inventario_id}",
    response_model=InventoryResponse,
    dependencies=[Depends(require_permission("inventario.update"))],
    summary="Actualizar configuración de inventario (stock mínimo, ubicación)"
)
def update_inventory_settings(
    inventario_id: uuid.UUID,
    data: InventoryUpdate,
    db: Session = Depends(get_db)
):
    return InventoryService.update_inventory_settings(db, inventario_id, data)


# ─────────────────────────── RECEPCIÓN ─────────────────────────────────────

@router.post(
    "/recepcion",
    response_model=ReceptionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("inventario.create"))],
    summary="Recibir productos en una sucursal"
)
def receive_products(
    data: ReceptionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    result = InventoryService.receive_products(db, data, usuario_id=current_user.id)
    return result


# ─────────────────────────── AJUSTE MANUAL ─────────────────────────────────

@router.post(
    "/ajuste",
    response_model=InventoryResponse,
    dependencies=[Depends(require_permission("inventario.update"))],
    summary="Ajuste manual de stock"
)
def adjust_stock(
    data: StockAdjustment,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return InventoryService.adjust_stock(db, data, usuario_id=current_user.id)


# ─────────────────────────── MOVIMIENTOS ───────────────────────────────────

@router.get(
    "/movimientos",
    response_model=List[MovementResponse],
    dependencies=[Depends(require_permission("inventario.list"))],
    summary="Historial de movimientos de inventario"
)
def get_movements(
    sucursal_id: Optional[uuid.UUID] = Query(None),
    tipo: Optional[MovementType] = Query(None),
    fecha_desde: Optional[datetime] = Query(None),
    fecha_hasta: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    return InventoryService.get_movements(
        db,
        sucursal_id=sucursal_id,
        tipo=tipo,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        limit=limit,
        offset=offset
    )


# ─────────────────────────── ALERTAS ───────────────────────────────────────

@router.get(
    "/alertas",
    response_model=List[AlertResponse],
    dependencies=[Depends(require_permission("inventario.list"))],
    summary="Alertas de stock bajo, crítico y excedente"
)
def get_alerts(
    sucursal_id: Optional[uuid.UUID] = Query(None, description="Filtrar por sucursal"),
    db: Session = Depends(get_db)
):
    return InventoryService.get_alerts(db, sucursal_id=sucursal_id)


# ─────────────────────────── DISPONIBILIDAD (PÚBLICA) ──────────────────────

@router.get(
    "/disponibilidad",
    response_model=List[AvailabilityResponse],
    summary="Consulta pública de disponibilidad de productos"
)
def get_availability(
    producto_id: Optional[uuid.UUID] = Query(None),
    sucursal_id: Optional[uuid.UUID] = Query(None),
    categoria_id: Optional[uuid.UUID] = Query(None),
    talla: Optional[str] = Query(None),
    color: Optional[str] = Query(None),
    ciudad: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Endpoint público — no requiere autenticación. Muestra stock disponible > 0."""
    return InventoryService.get_availability(
        db,
        producto_id=producto_id,
        sucursal_id=sucursal_id,
        categoria_id=categoria_id,
        talla=talla,
        color=color,
        ciudad=ciudad,
    )
