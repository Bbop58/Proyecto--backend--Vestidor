"""Recovery must restore the exported graph, without modifying another company."""
import copy
import json
import hashlib
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from app.models.product import Product
from app.models.inventory import Inventory
from app.services.backup_service import BackupService, json_serializer
from app.core.tenant_scope import bind_tenant
from tests.test_tenant_sales import shops


@pytest.fixture(autouse=True)
def commercial_restore_permission(monkeypatch,shops):
    from app.config import settings
    # SaaS S1 explicitly moves private restore to an authorized company actor.
    monkeypatch.setattr(settings,'PLATFORM_ADMIN_USER_IDS',[])


def payload(db,user):
    bind_tenant(db,user.tenant_id,user.id)
    return json.loads(json.dumps(BackupService.create_backup(db,user),default=json_serializer))


def checksum(backup):
    backup['checksum_sha256']=hashlib.sha256(json.dumps(backup['data'],default=json_serializer,sort_keys=True).encode()).hexdigest()


def test_full_graph_roundtrip_restores_inventory_and_removes_post_backup_rows(db_session,shops):
    a,va,ba=shops['a'];b,vb,_=shops['b']
    backup=payload(db_session,a)
    original_price=va.producto.precio_base
    original_stock=db_session.query(Inventory).filter_by(sucursal_id=ba[0].id).one().stock_actual
    va.producto.precio_base=333
    db_session.query(Inventory).filter_by(sucursal_id=ba[0].id).one().stock_actual=8
    extra=Product(tenant_id=a.tenant_id,categoria_id=va.producto.categoria_id,nombre='After backup',precio_base=5,activo=True)
    db_session.add(extra);db_session.commit();extra_id=extra.id
    result=BackupService.restore_backup(db_session,backup,a)
    assert result['success'] is True
    assert db_session.get(Product,va.producto_id).precio_base==original_price
    assert db_session.query(Inventory).filter_by(sucursal_id=ba[0].id).one().stock_actual==original_stock
    assert db_session.get(Product,extra_id) is None
    other=db_session.connection().execute(text('SELECT precio_base FROM productos WHERE id=:id'),{'id':vb.producto_id}).scalar_one()
    assert other==10


def test_restore_requires_scope_and_checksum_and_rejects_foreign_rows(db_session,shops):
    a,va,_=shops['a'];b,vb,_=shops['b']
    backup=payload(db_session,a)
    forged=copy.deepcopy(backup);forged.pop('checksum_sha256')
    with pytest.raises(HTTPException) as error:BackupService.restore_backup(db_session,forged,a)
    assert error.value.status_code in (400,409)
    forged=copy.deepcopy(backup);forged['tenant_id']=str(b.tenant_id)
    with pytest.raises(HTTPException) as error:BackupService.restore_backup(db_session,forged,a)
    assert error.value.status_code==403
    forged=copy.deepcopy(backup);forged['data']['productos'][0]['tenant_id']=str(b.tenant_id);checksum(forged)
    with pytest.raises(HTTPException) as error:BackupService.restore_backup(db_session,forged,a)
    assert error.value.status_code in (400,403,404)
    assert db_session.get(Product,va.producto_id).precio_base==10


def test_restore_bad_reference_rolls_back_without_partial_changes(db_session,shops):
    a,va,_=shops['a'];_,vb,_=shops['b']
    backup=payload(db_session,a)
    backup['data']['productos'][0]['nombre']='Must not persist'
    backup['data']['variantes'][0]['producto_id']=str(vb.producto_id)
    checksum(backup)
    with pytest.raises(HTTPException):BackupService.restore_backup(db_session,backup,a)
    assert db_session.get(Product,va.producto_id).nombre!='Must not persist'


def test_restore_cannot_rewind_platform_state_or_upgrade_old_format(db_session,shops):
    from app.models.tenant import Tenant
    a,_,_=shops['a'];backup=payload(db_session,a)
    db_session.connection().execute(Tenant.__table__.update().where(Tenant.id==a.tenant_id).values(platform_revision=7))
    db_session.commit()
    BackupService.restore_backup(db_session,backup,a)
    assert db_session.get(Tenant,a.tenant_id).platform_revision==7
    legacy=copy.deepcopy(backup);legacy['version']='4.0.0'
    with pytest.raises(HTTPException) as error:BackupService.restore_backup(db_session,legacy,a)
    assert error.value.status_code==409


def test_restore_cannot_reinstate_subscription_terms(db_session,shops):
    a,_,_=shops['a'];backup=payload(db_session,a)
    # Even a recomputed checksum cannot grant another year through a backup.
    backup['data']['reference_subscriptions'][0]['fecha_vencimiento']='2099-12-31'
    checksum(backup)
    with pytest.raises(HTTPException) as error:BackupService.restore_backup(db_session,backup,a)
    assert error.value.status_code==409


def test_restore_preserves_wallet_signature_and_ledger(db_session,shops):
    from app.services.wallet_service import WalletService
    from app.models.user_wallet import UserWallet
    a,_,_=shops['a']
    WalletService.topup_cash_admin(db_session,a,a.id,100)
    backup=payload(db_session,a)
    WalletService.topup_cash_admin(db_session,a,a.id,25)
    BackupService.restore_backup(db_session,backup,a)
    wallet=db_session.query(UserWallet).filter_by(user_id=a.id).one()
    assert wallet.saldo_actual==100
    assert WalletService.verify_wallet_integrity(wallet)
    assert db_session.connection().execute(text('SELECT count(*) FROM wallet_transactions')).scalar_one()==1


def test_restore_failure_during_audit_rolls_back_every_table(db_session,shops,monkeypatch):
    from app.services.audit_service import AuditService
    a,va,_=shops['a'];backup=payload(db_session,a)
    va.producto.nombre='Latest good data';db_session.commit()
    def fail(*args,**kwargs):raise RuntimeError('injected audit failure')
    monkeypatch.setattr(AuditService,'record',fail)
    with pytest.raises(HTTPException) as error:BackupService.restore_backup(db_session,backup,a)
    assert error.value.status_code==409
    assert db_session.get(Product,va.producto_id).nombre=='Latest good data'


def test_restore_endpoint_keeps_company_permission_and_scope(client,db_session,shops):
    from tests.test_tenant_contract import headers
    a,_,_=shops['a'];b,_,_=shops['b']
    backup=payload(db_session,a)
    uploaded={'file':('backup.json',json.dumps(backup).encode(),'application/json')}
    db_session.info.pop('tenant_scope',None)
    denied=client.post('/api/v1/backups/restore',headers=headers(b),files=uploaded)
    assert denied.status_code==403
    response=client.post('/api/v1/backups/restore',headers=headers(a),files=uploaded)
    assert response.status_code==200,response.text
    assert db_session.get(Product,shops['b'][1].producto_id).tenant_id==b.tenant_id
