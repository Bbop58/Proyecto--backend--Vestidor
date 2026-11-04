import uuid
from datetime import datetime, timedelta, timezone
import pytest
from tests.test_tenant_sales import shops
from tests.test_tenant_contract import headers
from app.models.sale import Sale
from app.models.reservation import Reservation
from app.models.sync_operation import SyncOperation
from app.models.audit_log import AuditLog
from app.models.inventory import Inventory
from app.schemas.sync import SyncBatchRequest, SyncOperationItemRequest
from app.services.sync_service import SyncService
from app.services.audit_service import AuditService


def operation(user,variant,branch,*,kind='VENTA_CONTADO_OFFLINE',key=None,count=1):
    return SyncOperationItemRequest(client_operation_id=key or uuid.uuid4(), operation_type=kind,
        sucursal_id=branch.id,cliente_id=user.id if kind=='RESERVA_OFFLINE' else None,
        fecha_local=datetime.now(timezone.utc),items=[dict(variante_id=variant.id,cantidad=count,precio_unitario=10)],
        monto_total=count*10,metodo_pago='RESERVA' if kind=='RESERVA_OFFLINE' else 'EFECTIVO')


def send(db,user,op):
    return SyncService.process_sync_batch(db,SyncBatchRequest(operations=[op]),user,user.tenant_id).results[0]


def test_replay_with_changed_content_is_conflict(db_session,shops):
    u,v,bs=shops['a'];op=operation(u,v,bs[0]);first=send(db_session,u,op)
    assert first.status=='CONFIRMADA'
    altered=op.model_copy(deep=True);altered.items[0].cantidad=2;altered.monto_total=20
    assert send(db_session,u,altered).status=='CONFLICTO'
    assert db_session.query(Sale).count()==1


def test_offline_reservation_creates_hold_not_sale(db_session,shops):
    u,v,bs=shops['a'];result=send(db_session,u,operation(u,v,bs[0],kind='RESERVA_OFFLINE'))
    assert result.status=='CONFIRMADA'
    assert db_session.query(Sale).count()==0
    reserve=db_session.query(Reservation).one()
    assert result.server_entity_id==reserve.id and result.receipt_number==reserve.codigo
    inv=db_session.query(Inventory).filter_by(sucursal_id=bs[0].id,variante_id=v.id).one()
    assert (inv.stock_actual,inv.stock_reservado)==(10,1)


def test_customer_can_sync_own_reservation_without_cashier_permission(client,db_session,shops):
    from app.models.role import Role
    u,v,bs=shops['a'];u.role_id=db_session.query(Role).filter_by(name='cliente').one().id;db_session.commit()
    op=operation(u,v,bs[0],kind='RESERVA_OFFLINE')
    response=client.post('/api/v1/sync/batch',headers=headers(u),json={'operations':[op.model_dump(mode='json')]})
    assert response.status_code==200,response.text
    assert response.json()['results'][0]['status']=='CONFIRMADA'


def test_sync_has_atomic_audit_record(db_session,shops):
    u,v,bs=shops['a'];send(db_session,u,operation(u,v,bs[0]))
    logs=db_session.query(AuditLog).filter_by(module='Sincronizaci\u00f3n Offline').all()
    assert len(logs)==1
    assert logs[0].tenant_id==u.tenant_id and logs[0].user_id==u.id


def test_failure_before_audit_commit_is_retryable_without_partial_sale(db_session,shops,monkeypatch):
    u,v,bs=shops['a'];op=operation(u,v,bs[0])
    with monkeypatch.context() as patch:
        patch.setattr(AuditService,'record',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('injected audit failure')),raising=False)
        result=send(db_session,u,op)
    assert result.status=='REINTENTAR'
    assert db_session.query(Sale).count()==db_session.query(SyncOperation).count()==0
    assert db_session.query(Inventory).filter_by(sucursal_id=bs[0].id,variante_id=v.id).one().stock_actual==10
    assert send(db_session,u,op).status=='CONFIRMADA'


def test_expired_offline_reservation_is_not_rejuvenated(db_session,shops):
    u,v,bs=shops['a'];op=operation(u,v,bs[0],kind='RESERVA_OFFLINE')
    op.fecha_local=datetime.now(timezone.utc)-timedelta(days=2)
    assert send(db_session,u,op).status=='CONFLICTO'
    assert db_session.query(Reservation).count()==0


def test_validation_conflict_is_durable(db_session,shops):
    u,v,bs=shops['a'];op=operation(u,v,bs[0]);op.monto_total=1
    assert send(db_session,u,op).status=='CONFLICTO'
    assert db_session.query(SyncOperation).count()==1
    assert send(db_session,u,op).is_duplicate


def test_legacy_confirmed_row_binds_only_matching_real_entity(db_session,shops):
    u,v,bs=shops['a'];op=operation(u,v,bs[0]);first=send(db_session,u,op)
    row=db_session.query(SyncOperation).one();row.request_fingerprint=None;db_session.commit()
    wrong=op.model_copy(deep=True);wrong.monto_total=1
    assert send(db_session,u,wrong).status=='CONFLICTO'
    assert db_session.query(SyncOperation).one().request_fingerprint is None
    assert send(db_session,u,op).is_duplicate
    assert db_session.query(SyncOperation).one().request_fingerprint==SyncService.fingerprint(op)
    assert db_session.query(Sale).count()==1


def test_one_explicit_successor_for_terminal_rejection(db_session,shops):
    u,v,bs=shops['a'];bad=operation(u,v,bs[0]);bad.monto_total=1
    assert send(db_session,u,bad).status=='CONFLICTO'
    child=operation(u,v,bs[0]);child.supersedes_operation_id=bad.client_operation_id
    assert send(db_session,u,child).status=='CONFIRMADA'
    second=operation(u,v,bs[0]);second.supersedes_operation_id=bad.client_operation_id
    assert send(db_session,u,second).status=='CONFLICTO'
    assert db_session.query(Sale).count()==1
    assert db_session.query(SyncOperation).filter_by(supersedes_operation_id=bad.client_operation_id).count()==1


def test_confirmed_operation_cannot_be_reissued(db_session,shops):
    u,v,bs=shops['a'];first=operation(u,v,bs[0]);send(db_session,u,first)
    child=operation(u,v,bs[0]);child.supersedes_operation_id=first.client_operation_id
    assert send(db_session,u,child).status=='CONFLICTO'
    assert db_session.query(Sale).count()==1


def test_mixed_batch_preserves_success_and_retryable_failure(db_session,shops,monkeypatch):
    u,v,bs=shops['a'];first=operation(u,v,bs[0]);second=operation(u,v,bs[0])
    real=AuditService.record
    def audit(db,action,*args):
        if str(second.client_operation_id) in action:raise RuntimeError('injected second operation failure')
        return real(db,action,*args)
    with monkeypatch.context() as patch:
        patch.setattr(AuditService,'record',audit)
        result=SyncService.process_sync_batch(db_session,SyncBatchRequest(operations=[first,second]),u,u.tenant_id)
    assert [r.status for r in result.results]==['CONFIRMADA','REINTENTAR']
    assert db_session.query(Sale).count()==db_session.query(SyncOperation).count()==1
    assert send(db_session,u,second).status=='CONFIRMADA'
    assert db_session.query(Sale).count()==2


def test_recovery_preview_verifies_owner_without_effects(client,db_session,shops):
    u,v,bs=shops['a'];foreign,fv,fbs=shops['b']
    own=operation(u,v,bs[0]).model_dump(mode='json');own['cajero_id']=str(u.id)
    unknown={**own,'client_operation_id':str(uuid.uuid4()),'cajero_id':None}
    wrong={**own,'client_operation_id':str(uuid.uuid4()),'sucursal_id':str(fbs[0].id)}
    fake_confirmed={**own,'client_operation_id':str(uuid.uuid4()),'local_status':'CONFIRMADA'}
    response=client.post('/api/v1/sync/recovery/preview',headers=headers(u),json={
        'entries':[{'channel':'SYNC','operation':raw} for raw in [own,unknown,wrong,fake_confirmed]]})
    assert response.status_code==200,response.text
    assert [r['eligible'] for r in response.json()['results']]==[True,False,False,False]
    assert db_session.query(SyncOperation).count()==db_session.query(Sale).count()==0


def test_foreign_uuid_and_operation_status_do_not_leak_entity(client,db_session,shops):
    u,v,bs=shops['a'];other,ov,obs=shops['b']
    op=operation(u,v,bs[0]);send(db_session,u,op)
    forged=operation(other,ov,obs[0],key=op.client_operation_id)
    result=send(db_session,other,forged)
    assert result.status=='CONFLICTO' and result.server_entity_id is None and result.receipt_number is None
    assert client.get(f'/api/v1/sync/operations/{op.client_operation_id}',headers=headers(other)).status_code==404


from tests.test_commercial_invariants import committed_shop

@pytest.mark.parametrize('changed',[False,True])
def test_concurrent_replay_has_one_effect(committed_shop,changed):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.models.user import User
    from app.models.role import Role
    from app.models.permission import Permission
    ids,factory=committed_shop;uid,vid,bid,_=ids
    with factory() as db:
        user=db.get(User,uid)
        permission=db.query(Permission).filter_by(code='ventas.create').first()
        if not permission:
            permission=Permission(code='ventas.create',module='ventas',method='POST',path='/ventas',description='Crear ventas');db.add(permission);db.flush()
        db.get(Role,user.role_id).permissions.append(permission);db.commit()
        user=db.get(User,uid)
        variant=type('Variant',(),{'id':vid})();branch=type('Branch',(),{'id':bid})()
        original=operation(user,variant,branch)
    barrier=Barrier(2)
    def run(index):
        with factory() as db:
            user=db.get(User,uid);op=original.model_copy(deep=True)
            if changed and index:op.monto_total=1
            barrier.wait(timeout=20)
            return send(db,user,op)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,[0,1]))
    with factory() as db:
        count=db.query(SyncOperation).filter_by(user_id=uid).count()
        assert count==1
        assert db.query(AuditLog).filter_by(user_id=uid,module='Sincronizaci\u00f3n Offline').count()==1
        if changed:
            assert sum(r.status=='CONFLICTO' for r in results)>=1
            assert db.query(Sale).filter_by(cajero_id=uid).count() in (0,1)
        else:
            assert all(r.status=='CONFIRMADA' for r in results)
            assert sum(r.is_duplicate for r in results)==1
            assert db.query(Sale).filter_by(cajero_id=uid).count()==1


def test_wallet_rejection_is_durable_after_stock_recovers(db_session,shops):
    from app.services.wallet_service import WalletService
    from tests.test_payment_invariants import order
    u,v,bs=shops['a'];WalletService.topup_cash_admin(db_session,u,u.id,100)
    inv=db_session.query(Inventory).filter_by(sucursal_id=bs[0].id,variante_id=v.id).one();inv.stock_actual=0;db_session.commit()
    op=order(bs[0],v)
    first=WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[op])
    assert first['results'][0]['status']=='COMPENSADA_POR_STOCK'
    inv.stock_actual=10;db_session.commit()
    replay=WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[op])
    assert replay['results'][0]['status']=='COMPENSADA_POR_STOCK'
    assert db_session.query(Sale).count()==0
    assert db_session.query(SyncOperation).count()==1
    assert replay['nuevo_saldo']==100


def test_wallet_audit_failure_does_not_leave_debit_or_sale(db_session,shops,monkeypatch):
    from app.services.wallet_service import WalletService
    from app.models.wallet_transaction import WalletTransaction
    from app.models.user_wallet import UserWallet
    from tests.test_payment_invariants import order
    u,v,bs=shops['a'];WalletService.topup_cash_admin(db_session,u,u.id,100)
    op=order(bs[0],v)
    with monkeypatch.context() as patch:
        patch.setattr(AuditService,'record',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('injected audit failure')))
        with pytest.raises(RuntimeError):WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[op])
    assert db_session.query(Sale).count()==db_session.query(SyncOperation).count()==0
    assert db_session.query(UserWallet).one().saldo_actual==100
    assert db_session.query(WalletTransaction).count()==1
    assert WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[op])['exitosas']==1


def test_operation_uuid_cannot_be_reused_across_cash_and_wallet(db_session,shops):
    from app.services.wallet_service import WalletService
    from tests.test_payment_invariants import order
    u,v,bs=shops['a'];WalletService.topup_cash_admin(db_session,u,u.id,100)
    paid=order(bs[0],v)
    assert WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[paid])['exitosas']==1
    forged=operation(u,v,bs[0],key=paid.client_operation_id)
    assert send(db_session,u,forged).status=='CONFLICTO'
    assert db_session.query(Sale).count()==1
    cash=operation(u,v,bs[0]);assert send(db_session,u,cash).status=='CONFIRMADA'
    assert WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[order(bs[0],v,operation=cash.client_operation_id)])['fallidas']==1
    assert db_session.query(Sale).count()==2
