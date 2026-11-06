"""Exactly-once commercial effects for authenticated offline queues."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select, func, desc
from fastapi import HTTPException
from pydantic import ValidationError
from app.core.money import money, quote
from app.models.sync_operation import SyncOperation
from app.models.branch import Branch
from app.models.sale import Sale
from app.models.reservation import Reservation
from app.schemas.sync import SyncBatchResponse, SyncOperationResult
from app.schemas.sale import PresencialSaleCreate, PaymentInfo, SaleItemCreate
from app.schemas.reservation import ReservationCreate
from app.services.sale_service import SaleService
from app.services.reservation_service import ReservationService
from app.services.role_service import RoleService
from app.services.audit_service import AuditService


def utc_now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class SyncService:
    @staticmethod
    def fingerprint(op):
        grouped = Counter()
        for row in op.items:
            grouped[(str(row.variante_id), str(money(row.precio_unitario)))] += row.cantidad
        body = dict(kind=op.operation_type, branch=str(op.sucursal_id), customer=str(op.cliente_id),
            date=utc(op.fecha_local).isoformat(), items=sorted((key[0], key[1], count) for key,count in grouped.items()),
            total=str(money(op.monto_total)), method=op.metodo_pago,
            received=str(money(op.monto_efectivo_recibido)) if op.monto_efectivo_recibido is not None else None,
            change=str(money(op.cambio_entregado)) if op.cambio_entregado is not None else None,
            coupon=op.codigo_cupon.strip().upper() if op.codigo_cupon else None,
            pickup=utc(op.fecha_hora_esperada).isoformat() if op.fecha_hora_esperada else None,
            supersedes=str(op.supersedes_operation_id) if op.supersedes_operation_id else None)
        return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    @staticmethod
    def lock_keys(db, op):
        for key in sorted({op.client_operation_id,op.supersedes_operation_id}-{None},key=str):
            number = int.from_bytes(hashlib.sha256(b'fashionstore-sync:'+key.bytes).digest()[:8], 'big', signed=True)
            db.execute(select(func.pg_advisory_xact_lock(number)))

    @staticmethod
    def result(op, status, *, duplicate=False, entity=None, receipt=None, error=None):
        return SyncOperationResult(client_operation_id=op.client_operation_id,operation_type=op.operation_type,
            status=status,is_duplicate=duplicate,server_entity_id=entity,receipt_number=receipt,
            entity_type=('RESERVA' if op.operation_type=='RESERVA_OFFLINE' else 'VENTA') if entity else None,
            retryable=status=='REINTENTAR', error_detail=error,
            message=error or ('Operación confirmada' if status=='CONFIRMADA' else 'Operación pendiente de reintento'))

    @staticmethod
    def legacy_matches(db, row, op):
        if row.sync_status != 'CONFIRMADA' or not row.entity_id or row.branch_id != op.sucursal_id:
            return False
        if row.operation_type != op.operation_type or utc(row.client_created_at)!=utc(op.fecha_local):
            return False
        if op.operation_type=='VENTA_CONTADO_OFFLINE':
            entity=db.query(Sale).filter_by(id=row.entity_id,tenant_id=row.tenant_id,sucursal_id=row.branch_id).first()
            if (not entity or entity.cajero_id!=row.user_id or entity.cliente_id!=op.cliente_id
                or entity.metodo_pago.value!=op.metodo_pago or money(entity.monto_total)!=money(op.monto_total)
                or entity.codigo_cupon!=(op.codigo_cupon.strip().upper() if op.codigo_cupon else None)
                or (op.monto_efectivo_recibido is not None and (entity.monto_recibido is None or money(entity.monto_recibido)!=money(op.monto_efectivo_recibido)))
                or (op.cambio_entregado is not None and (entity.cambio is None or money(entity.cambio)!=money(op.cambio_entregado)))):
                return False
            details=entity.detalles
        else:
            entity=db.query(Reservation).filter_by(id=row.entity_id,tenant_id=row.tenant_id,cliente_id=row.user_id).first()
            if not entity or money(entity.total_estimado)!=money(op.monto_total):return False
            details=entity.detalles
        expected=Counter();actual=Counter()
        for item in op.items:expected[(item.variante_id,money(item.precio_unitario))]+=item.cantidad
        for item in details:actual[(item.variante_id,money(item.precio_unitario))]+=item.cantidad
        return actual==expected

    @staticmethod
    def authorize(db, user, op):
        permissions=RoleService.get_permission_codes(db,user.role_id)
        if op.operation_type=='VENTA_CONTADO_OFFLINE':
            if 'ventas.create' not in permissions:raise HTTPException(403,'La operación requiere permiso de caja')
            if op.cajero_id and op.cajero_id!=user.id:raise HTTPException(403,'El cajero es el usuario autenticado')
        elif op.operation_type=='RESERVA_OFFLINE':
            if 'reservas.create' not in permissions:raise HTTPException(403,'La cuenta no puede crear reservas')
            if op.cliente_id and op.cliente_id!=user.id:raise HTTPException(403,'Solo puedes sincronizar reservas propias')
        else:raise HTTPException(400,'Tipo de operación offline no soportado')

    @classmethod
    def process_sync_batch(cls,db,batch_request,current_user,tenant_id):
        results=[]
        for op in batch_request.operations:
            try:
                if current_user.tenant_id!=tenant_id:raise HTTPException(403,'Empresa no disponible')
                cls.lock_keys(db,op)
                digest=cls.fingerprint(op)
                row=db.query(SyncOperation).filter_by(client_operation_id=op.client_operation_id,
                    tenant_id=tenant_id,user_id=current_user.id).populate_existing().first()
                if row:
                    if row.sync_status=='CONFIRMADA':
                        model=Reservation if row.operation_type=='RESERVA_OFFLINE' else Sale
                        if not db.query(model.id).filter_by(id=row.entity_id,tenant_id=tenant_id).first():
                            results.append(cls.result(op,'CONFLICTO',entity=row.entity_id,error='Falta la entidad original; requiere conciliación'))
                            db.rollback();continue
                    if row.request_fingerprint is None and cls.legacy_matches(db,row,op):
                        row.request_fingerprint=digest
                        AuditService.record(db,f'SYNC_LEGACY_VERIFICADA {op.client_operation_id}','Sincronización Offline',current_user)
                    if row.request_fingerprint!=digest:
                        results.append(cls.result(op,'CONFLICTO',entity=row.entity_id,
                            error='El UUID pertenece a otro contenido o a un registro antiguo que requiere revisión'))
                    else:
                        results.append(cls.result(op,row.sync_status,duplicate=True,entity=row.entity_id,
                            receipt=(row.response_payload or {}).get('receipt_number'),error=row.error_detail))
                    db.commit()
                    continue
                table=SyncOperation.__table__
                if db.execute(select(table.c.id).where(table.c.client_operation_id==op.client_operation_id)).first():
                    results.append(cls.result(op,'CONFLICTO',error='Identificador no disponible para esta cuenta'))
                    db.rollback();continue
                from app.models.wallet_transaction import WalletTransaction
                if db.execute(select(WalletTransaction.__table__.c.id).where(WalletTransaction.__table__.c.client_operation_id==op.client_operation_id)).first():
                    results.append(cls.result(op,'CONFLICTO',error='Identificador no disponible para esta operación'))
                    db.rollback();continue
                from app.services.entitlement_service import EntitlementService
                try:
                    EntitlementService.reconcile(db,current_user,op.client_operation_id,'SYNC',op.operation_type,op.offline_authorization)
                except HTTPException as error:
                    results.append(cls.result(op,'REINTENTAR',error=str(error.detail)).model_copy(update={'requires_review':True,'retryable':False}))
                    db.rollback();continue
                branch=db.query(Branch).filter_by(id=op.sucursal_id,tenant_id=tenant_id,activa=True).first()
                try:
                    cls.authorize(db,current_user,op)
                    if not branch:raise HTTPException(404,'Sucursal no encontrada, inactiva o no pertenece a su empresa')
                    if op.supersedes_operation_id:
                        previous=db.query(SyncOperation).filter_by(client_operation_id=op.supersedes_operation_id,
                            tenant_id=tenant_id,user_id=current_user.id).first()
                        if (not previous or previous.entity_id or previous.sync_status not in ('CONFLICTO','RECHAZADA_POR_STOCK')
                            or op.supersedes_operation_id==op.client_operation_id):
                            raise HTTPException(409,'La operación anterior no es un rechazo corregible de esta cuenta')
                        if db.query(SyncOperation.id).filter_by(supersedes_operation_id=previous.client_operation_id).first():
                            raise HTTPException(409,'El rechazo ya tiene una operación sucesora; revisa su estado')
                    local=utc(op.fecha_local)
                    if local>utc_now()+timedelta(minutes=5):raise HTTPException(400,'Revisa la fecha y hora del dispositivo')
                    if op.operation_type=='RESERVA_OFFLINE':
                        expiry=min(local+timedelta(hours=24),utc_now()+timedelta(hours=24))
                        pickup=utc(op.fecha_hora_esperada) if op.fecha_hora_esperada else expiry
                        if expiry<=utc_now() or pickup>expiry:raise HTTPException(409,'La reserva offline venció; crea una nueva solicitud')
                        priced=quote(db,op.items,tenant_id)
                        if op.codigo_cupon or money(op.monto_total)!=priced['total']:
                            raise HTTPException(409,'El total de reserva no coincide con el servidor')
                        payload=ReservationCreate(sucursal_id=op.sucursal_id,fecha_hora_esperada=pickup,
                            items=[dict(variante_id=i.variante_id,cantidad=i.cantidad) for i in op.items],nota=op.notas)
                        entity=ReservationService.create_reservation(db,payload,current_user.id,_commit=False,_expires_at=expiry)
                        receipt=entity['codigo']
                    else:
                        if op.metodo_pago not in ('EFECTIVO','QR','TARJETA'):
                            raise HTTPException(409,'Método no autorizado para una venta manual offline')
                        priced=quote(db,op.items,tenant_id,op.codigo_cupon)
                        if money(op.monto_total)!=priced['total']:raise HTTPException(409,'El total no coincide con el catálogo del servidor')
                        received=money(op.monto_efectivo_recibido) if op.monto_efectivo_recibido is not None else priced['total']
                        change=received-priced['total'] if op.metodo_pago=='EFECTIVO' else Decimal('0')
                        if (op.metodo_pago=='EFECTIVO' and change<0) or (op.cambio_entregado is not None and money(op.cambio_entregado)!=change):
                            raise HTTPException(409,'El efectivo o cambio no coincide con el servidor')
                        payload=PresencialSaleCreate(sucursal_id=op.sucursal_id,cliente_id=op.cliente_id,
                            items=[SaleItemCreate(variante_id=i.variante_id,cantidad=i.cantidad,precio_unitario=i.precio_unitario) for i in op.items],
                            pago=PaymentInfo(metodo=op.metodo_pago,monto_recibido=received),nota=op.notas,codigo_cupon=op.codigo_cupon)
                        previous=db.info.get('contract_reconciliation')
                        db.info['contract_reconciliation']=(current_user.id,tenant_id,op.client_operation_id)
                        try:entity=SaleService.create_presencial_sale(db,payload,current_user.id,_commit=False,_created_at=local)
                        finally:
                            if previous is None:db.info.pop('contract_reconciliation',None)
                            else:db.info['contract_reconciliation']=previous
                        receipt=entity['numero_recibo']
                    record=SyncOperation(client_operation_id=op.client_operation_id,tenant_id=tenant_id,
                        branch_id=branch.id,user_id=current_user.id,operation_type=op.operation_type,
                        client_created_at=local,request_fingerprint=digest,supersedes_operation_id=op.supersedes_operation_id,
                        sync_status='CONFIRMADA',entity_id=entity['id'],response_payload={'receipt_number':receipt})
                    db.add(record)
                    AuditService.record(db,f'SYNC_{op.operation_type} {op.client_operation_id}','Sincronización Offline',current_user)
                    db.commit()
                    results.append(cls.result(op,'CONFIRMADA',entity=entity['id'],receipt=receipt))
                except (HTTPException, ValidationError) as error:
                    db.rollback()
                    cls.lock_keys(db,op)
                    # A business rejection is durable and has no commercial side effects.
                    known=db.query(SyncOperation).filter_by(client_operation_id=op.client_operation_id,
                        tenant_id=tenant_id,user_id=current_user.id).first()
                    if known:
                        results.append(cls.result(op,'CONFLICTO',error='La operación requiere revisar su registro previo'))
                        db.rollback();continue
                    branch=db.query(Branch).filter_by(id=op.sucursal_id,tenant_id=tenant_id).first()
                    detail=str(error.detail) if isinstance(error,HTTPException) else 'Los datos no cumplen los límites de la operación comercial'
                    status='RECHAZADA_POR_STOCK' if 'stock insuficiente' in detail.lower() else 'CONFLICTO'
                    # Do not link a second successor when rejecting a retry of a retry.
                    parent=op.supersedes_operation_id
                    if parent:
                        previous=db.query(SyncOperation).filter_by(client_operation_id=parent,
                            tenant_id=tenant_id,user_id=current_user.id).first()
                        if (not previous or previous.entity_id or previous.sync_status not in ('CONFLICTO','RECHAZADA_POR_STOCK')
                            or db.query(SyncOperation.id).filter_by(supersedes_operation_id=parent).first()):parent=None
                    record=SyncOperation(client_operation_id=op.client_operation_id,tenant_id=tenant_id,
                        branch_id=branch.id if branch else None,user_id=current_user.id,operation_type=op.operation_type,
                        client_created_at=utc(op.fecha_local),request_fingerprint=digest,supersedes_operation_id=parent,
                        sync_status=status,error_detail=detail,response_payload={'status':status})
                    db.add(record)
                    AuditService.record(db,f'SYNC_RECHAZO {op.client_operation_id}','Sincronización Offline',current_user)
                    db.commit()
                    results.append(cls.result(op,status,error=detail))
            except Exception:
                db.rollback()
                results.append(cls.result(op,'REINTENTAR',error='No se confirmó la transacción; reintenta el mismo UUID'))
        return SyncBatchResponse(processed_at=utc_now(),total_received=len(results),results=results,
            total_confirmed=sum(r.status=='CONFIRMADA' for r in results),
            total_rejected=sum(r.status not in ('CONFIRMADA','REINTENTAR') for r in results),
            total_duplicates=sum(r.is_duplicate for r in results))

    @staticmethod
    def get_sync_history(db,tenant_id,limit=100,offset=0,user_id=None):
        query=db.query(SyncOperation).filter_by(tenant_id=tenant_id)
        if user_id:query=query.filter_by(user_id=user_id)
        return query.order_by(desc(SyncOperation.created_at)).offset(offset).limit(limit).all()
