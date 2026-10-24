import uuid
from typing import List
from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user, require_permission, get_current_tenant
from app.models.user import User
from app.models.tenant import Tenant
from app.schemas.sync import (
    SyncBatchRequest,
    SyncBatchResponse,
    SyncHistoryItemResponse
)
from app.services.sync_service import SyncService

router = APIRouter(prefix="/sync", tags=["Motor de Sincronización Offline e Idempotencia"])


@router.post(
    "/batch",
    response_model=SyncBatchResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ventas.create"))],
    summary="Sincronizar lote de operaciones comerciales registradas offline (PWA / Flutter)"
)
def sync_offline_batch(
    batch_request: SyncBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant)
):
    """
    Recibe un lote de ventas o reservas offline desde la Web PWA o la aplicación móvil Flutter.
    Garantiza:
    - Idempotencia estricta por 'client_operation_id' (si el cliente reintenta, no se duplica).
    - Descuento atómico de stock e inserción en Kardex.
    - Detección y reporte individual de conflictos de inventario sin abortar el lote.
    """
    return SyncService.process_sync_batch(
        db=db,
        batch_request=batch_request,
        current_user=current_user,
        tenant_id=tenant.id
    )


@router.get(
    "/history",
    response_model=List[SyncHistoryItemResponse],
    dependencies=[Depends(require_permission("ventas.list"))],
    summary="Consultar historial de operaciones sincronizadas y estado de resolución de conflictos"
)
def get_sync_history(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant)
):
    """
    Permite visualizar la bitácora de sincronización offline con estados:
    CONFIRMADA, RECHAZADA_POR_STOCK o CONFLICTO.
    """
    return SyncService.get_sync_history(
        db=db,
        tenant_id=tenant.id,
        limit=limit
    )
