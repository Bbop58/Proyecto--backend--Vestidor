import uuid
from typing import List
from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user, require_permission, get_current_tenant, get_history_user
from app.models.user import User
from app.models.tenant import Tenant
from app.schemas.sync import (
    SyncBatchRequest,
    SyncBatchResponse,
    SyncHistoryItemResponse
)
from app.services.sync_service import SyncService
from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import Literal, Any


class RecoveryEntry(BaseModel):
    channel: Literal['SYNC', 'WALLET'] = 'SYNC'
    operation: dict[str, Any]


class RecoveryBatch(BaseModel):
    entries: List[RecoveryEntry] = Field(..., max_length=100)

router = APIRouter(prefix="/sync", tags=["Motor de Sincronización Offline e Idempotencia"])


@router.post(
    "/batch",
    response_model=SyncBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Sincronizar lote de operaciones comerciales registradas offline (PWA / Flutter)"
)
def sync_offline_batch(
    batch_request: SyncBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_history_user)
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
        tenant_id=current_user.tenant_id
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


@router.get('/operations/{operation_id}', summary='Consultar el resultado propio antes de reintentar')
def own_operation(operation_id: uuid.UUID, db: Session = Depends(get_db), current_user=Depends(get_history_user)):
    from app.models.sync_operation import SyncOperation
    row=db.query(SyncOperation).filter_by(client_operation_id=operation_id,
        user_id=current_user.id,tenant_id=current_user.tenant_id).first()
    if not row:raise HTTPException(404,'Operación no disponible')
    return {'client_operation_id':row.client_operation_id,'status':row.sync_status,
        'server_entity_id':row.entity_id,'receipt_number':(row.response_payload or {}).get('receipt_number'),
        'operation_type':row.operation_type,'error_detail':row.error_detail}


@router.post('/recovery/preview', summary='Verificar propietario de filas antiguas sin aplicarlas ni modificar la fuente')
def recovery_preview(payload: RecoveryBatch, db: Session = Depends(get_db), current_user=Depends(get_history_user)):
    from app.models.branch import Branch
    from app.schemas.sync import SyncOperationItemRequest
    from app.schemas.wallet import OfflineOrderPayload
    from app.models.sync_operation import SyncOperation
    from app.models.wallet_transaction import WalletTransaction
    from app.models.user_wallet import UserWallet
    from sqlalchemy import select
    results=[]
    for index,entry in enumerate(payload.entries):
        raw=entry.operation
        try:
            if entry.channel=='SYNC':
                op=SyncOperationItemRequest.model_validate(raw)
                owner=raw.get('cajero_id') if op.operation_type=='VENTA_CONTADO_OFFLINE' else raw.get('cliente_id')
                if str(owner)!=str(current_user.id):raise ValueError('Sin prueba de propietario: permanece en cuarentena')
                SyncService.authorize(db,current_user,op)
                model=SyncOperation
            else:
                op=OfflineOrderPayload.model_validate(raw)
                if str(raw.get('cliente_id'))!=str(current_user.id):raise ValueError('Sin prueba de propietario: permanece en cuarentena')
                model=WalletTransaction
            if not db.query(Branch.id).filter_by(id=op.sucursal_id,tenant_id=current_user.tenant_id,activa=True).first():
                raise ValueError('Sucursal no disponible en esta empresa')
            table=model.__table__
            claimed=db.execute(select(table.c.id).where(table.c.client_operation_id==op.client_operation_id)).first()
            other=SyncOperation if entry.channel=='WALLET' else WalletTransaction
            other_claim=db.execute(select(other.__table__.c.id).where(other.__table__.c.client_operation_id==op.client_operation_id)).first()
            if other_claim and not claimed:
                decision=db.query(SyncOperation).filter_by(client_operation_id=op.client_operation_id,
                    tenant_id=current_user.tenant_id,user_id=current_user.id,operation_type='COMPRA_BILLETERA_OFFLINE').first() if entry.channel=='WALLET' else None
                if not decision:raise ValueError('Identificador reservado en otro canal o cuenta')
            local_status=raw.get('local_status') or raw.get('status')
            if local_status in ('CONFIRMADA','CONFIRMED','OK') and not claimed:
                raise ValueError('Una confirmación antigua sin registro central requiere revisión; no se reaplica')
            if claimed:
                if entry.channel=='SYNC':
                    own=db.query(SyncOperation).filter_by(client_operation_id=op.client_operation_id,
                        tenant_id=current_user.tenant_id,user_id=current_user.id).first()
                else:
                    own=(db.query(WalletTransaction).join(UserWallet).filter(
                        WalletTransaction.client_operation_id==op.client_operation_id,
                        WalletTransaction.tenant_id==current_user.tenant_id,UserWallet.user_id==current_user.id).first())
                    if own and not (own.metadatos or {}).get('request_hash'):
                        raise ValueError('Movimiento monetario antiguo sin trazabilidad: requiere conciliación')
                if not own:raise ValueError('Identificador no disponible para esta cuenta')
            if not claimed:
                from app.services.entitlement_service import EntitlementService
                EntitlementService.reconcile(db,current_user,op.client_operation_id,entry.channel,
                    op.operation_type if entry.channel=='SYNC' else 'COMPRA_BILLETERA_OFFLINE',op.offline_authorization)
            results.append({'index':index,'client_operation_id':str(op.client_operation_id),'eligible':True,
                'reason':'Propietario y empresa verificados; la aplicación validará precios y estado al sincronizar'})
        except Exception:
            results.append({'index':index,'client_operation_id':str(raw.get('client_operation_id','')),
                'eligible':False,'reason':'Propietario, formato, historial o empresa no verificables; conserva/exporta para revisión'})
    return {'results':results}


class OfflineAuthorizationRequest(BaseModel):
    channel: Literal['SYNC','WALLET']
    count: int=Field(10,ge=1,le=20,strict=True)


@router.post('/authorizations')
def authorize_offline(body: OfflineAuthorizationRequest,db: Session=Depends(get_db),current_user=Depends(get_current_user)):
    from app.services.entitlement_service import EntitlementService
    return EntitlementService.authorize_offline(db,current_user,body.channel,body.count)
