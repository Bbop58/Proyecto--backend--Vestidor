from datetime import timedelta
import pytest
from sqlalchemy import update
from tests.test_saas_subscription_contracts import contract_setup,quote,accept,decide
from tests.support_saas import saas_actors,headers
from app.models.subscription_term import SubscriptionTerm as Term
from app.core.billing_calendar import business_today
from app.services.entitlement_service import EntitlementService as Entitlements,Purpose
from tests.test_saas_plan_catalog import draft,transition
from tests.test_tenant_sales import shops
from app.models import Tenant,Branch,Sale,SyncOperation,Inventory,UserWallet
from app.services.wallet_service import WalletService
from decimal import Decimal
import uuid
from fastapi import HTTPException
from tests.support_payments import fake_paypal,start_payment


def test_period_features_override_plan_without_anticipating_future(client,db_session,contract_setup,monkeypatch):
    setup=contract_setup
    version=draft(client,setup['operator'],str(setup['plan'].id),precio_mensual_bob='150.00',max_sucursales=2,
        features={'reportes_ia':False,'analitica_avanzada':False,'offline':False})
    assert transition(client,setup['operator'],version,'publish').status_code==200
    requested,_=accept(client,setup);assert decide(client,setup,requested).status_code==200
    assert Entitlements.resolve(db_session,setup['admin'])['features']['reportes_ia'] is True
    legacy=db_session.query(Term).filter_by(origen='REFERENCIA_LEGADA').one()
    monkeypatch.setattr('app.core.billing_calendar.business_today',lambda:legacy.fecha_fin)
    effective=Entitlements.resolve(db_session,setup['admin'])
    assert effective['features']=={'reportes_ia':False,'analitica_avanzada':False,'offline':False}
    assert effective['term']['precio_mensual_bob']==150
    assert client.post('/api/v1/reportes-voz/query',headers=headers(setup['admin']),json={}).status_code==403
    assert client.get('/api/v1/analytics/forecast',headers=headers(setup['admin'])).status_code==403
    assert client.get('/api/v1/analytics/segments',headers=headers(setup['admin'])).status_code==403
    assert client.post('/api/v1/analytics/train',headers=headers(setup['admin'])).status_code==403
    current=client.get('/api/v1/subscriptions/current',headers=headers(setup['admin'])).json()
    assert current['plan']['precio_mensual_bob']==150 and current['sucursales_maximas']==2


def test_no_implicit_renewal_and_history_is_readable(client,db_session,contract_setup,monkeypatch):
    setup=contract_setup;term=db_session.query(Term).one();before=db_session.query(Term).count()
    monkeypatch.setattr('app.core.billing_calendar.business_today',lambda:term.fecha_fin)
    result=Entitlements.resolve(db_session,setup['admin'])
    assert result['active'] is False and not any(result['features'].values())
    assert db_session.query(Term).count()==before
    assert client.get('/api/v1/ventas/mis-compras',headers=headers(setup['admin'])).status_code==200
    assert client.get('/api/v1/subscriptions/current',headers=headers(setup['admin'])).json()['service_restricted'] is True


def test_paid_plan_does_not_grant_staff_permission_and_unknown_feature_denied(client,db_session,contract_setup):
    setup=contract_setup
    assert Entitlements.resolve(db_session,setup['customer'])['features']['reportes_ia'] is True
    assert client.get('/api/v1/analytics/forecast',headers=headers(setup['customer'])).status_code==403
    with pytest.raises(HTTPException):Entitlements.check(db_session,setup['admin'],'usuarios_ilimitados')
    with pytest.raises(HTTPException):Entitlements.check(db_session,setup['admin'],purpose='TRUST_CLIENT')


def test_expired_company_only_deactivates_own_branch(client,db_session,contract_setup,monkeypatch):
    setup=contract_setup
    branch=Branch(tenant_id=setup['admin'].tenant_id,nombre='Keep name',ciudad='SC',direccion='Test',activa=True)
    foreign=Branch(tenant_id=setup['other'].tenant_id,nombre='Other',ciudad='SC',direccion='Test',activa=True)
    db_session.add_all([branch,foreign]);db_session.commit();own_id,other_id=branch.id,foreign.id
    end=db_session.query(Term).one().fecha_fin
    monkeypatch.setattr('app.core.billing_calendar.business_today',lambda:end)
    assert client.put(f'/api/v1/sucursales/{own_id}',headers=headers(setup['admin']),json={'activa':False,'nombre':'Unexpected'}).status_code==403
    assert client.post(f'/api/v1/sucursales/{other_id}/deactivate',headers=headers(setup['admin'])).status_code==404
    assert client.post(f'/api/v1/sucursales/{own_id}/deactivate',headers=headers(setup['admin'])).status_code==200
    assert db_session.get(Branch,own_id).activa is False and db_session.get(Branch,own_id).nombre=='Keep name'
    assert db_session.query(Inventory).count()==0


def wallet_order(variant,branch,operation_id,authorization=None):
    return {'client_operation_id':str(operation_id),'sucursal_id':str(branch.id),'items':[
        {'variante_id':str(variant.id),'cantidad':1,'precio_unitario':10}],
        'monto_total':10,'offline_authorization':authorization,'fecha_local':'2000-01-01T00:00:00Z'}


def test_disabled_license_preserves_unproven_wallet_pending_and_signed_uuid_can_reconcile(client,db_session,shops):
    from tests.test_tenant_contract import headers as shop_headers
    actor,variant,branches=shops['a'];auth=shop_headers(actor)
    WalletService.topup_cash_admin(db_session,actor,actor.id,100)
    grant=client.post('/api/v1/sync/authorizations',headers=auth,json={'channel':'WALLET','count':1}).json()['grants'][0]
    db_session.connection().execute(update(Tenant).where(Tenant.id==actor.tenant_id).values(activo=False))
    db_session.commit();db_session.expire_all()
    unproven=wallet_order(variant,branches[0],uuid.uuid4())
    response=client.post('/api/v1/wallet/sync-offline-orders',headers=auth,json={'orders':[unproven]})
    assert response.status_code==200,response.text
    assert response.json()['results'][0]['requires_review'] is True
    assert db_session.query(Sale).count()==0 and db_session.query(SyncOperation).count()==0
    assert db_session.query(UserWallet).one().saldo_actual==100
    permitted=wallet_order(variant,branches[0],grant['client_operation_id'],grant['authorization'])
    response=client.post('/api/v1/wallet/sync-offline-orders',headers=auth,json={'orders':[permitted]})
    assert response.status_code==200,response.text
    assert response.json()['results'][0]['status']=='CONFIRMADA'
    repeated=client.post('/api/v1/wallet/sync-offline-orders',headers=auth,json={'orders':[permitted]})
    assert repeated.json()['results']==response.json()['results']
    assert db_session.query(Sale).count()==1 and db_session.query(UserWallet).one().saldo_actual==90


def test_offline_authorization_cannot_cross_identity_operation_or_channel(client,db_session,shops):
    from tests.test_tenant_contract import headers as shop_headers
    actor,variant,branches=shops['a'];other,_,_=shops['b']
    grant=client.post('/api/v1/sync/authorizations',headers=shop_headers(actor),json={'channel':'WALLET','count':1}).json()['grants'][0]
    db_session.connection().execute(update(Tenant).values(activo=False));db_session.commit();db_session.expire_all()
    for user,op,channel in [(other,grant['client_operation_id'],'WALLET'),(actor,uuid.uuid4(),'WALLET'),(actor,grant['client_operation_id'],'SYNC')]:
        with pytest.raises(HTTPException):Entitlements.reconcile(db_session,user,op,channel,'COMPRA_BILLETERA_OFFLINE',grant['authorization'])


def test_no_license_all_commercial_writers_reject_before_body_or_effect(client,db_session,contract_setup,monkeypatch):
    from app.main import app
    import re
    setup=contract_setup;end=db_session.query(Term).one().fecha_fin
    monkeypatch.setattr('app.core.billing_calendar.business_today',lambda:end)
    exempt=('/api/v1/auth/','/api/v1/platform/','/api/v1/subscriptions/','/api/v1/sync/')
    count=0
    for path,methods in app.openapi()['paths'].items():
        if not path.startswith('/api/v1/') or path.startswith(exempt) or path in (
            '/api/v1/users/me','/api/v1/users/me/change-password','/api/v1/wallet/sync-offline-orders') or path.endswith(('/deactivate','/resume')):continue
        for method in set(methods)&{'post','put','patch','delete'}:
            concrete=re.sub(r'\{[^}]+\}',str(uuid.UUID(int=0)),path)
            response=client.request(method.upper(),concrete,headers=headers(setup['admin']),json={})
            assert response.status_code==403,(method,path,response.text)
            count+=1
    assert count>55


def test_expired_period_cannot_create_business_data(client,db_session,contract_setup):
    setup=contract_setup
    # Simulate a date after the immutable legacy period, not forged input.
    from unittest.mock import patch
    with patch('app.core.billing_calendar.business_today',return_value=business_today()+timedelta(days=40)):
        response=client.post('/api/v1/categorias',headers=headers(setup['admin']),json={'nombre':'Expired write','activa':True})
    assert response.status_code==403,response.text


def test_suspended_manager_can_login_for_contract_only(client,db_session,contract_setup):
    from app.models.tenant import Tenant
    setup=contract_setup;actor=setup['admin']
    db_session.connection().execute(update(Tenant).where(Tenant.id==actor.tenant_id).values(activo=False))
    db_session.commit();db_session.expire_all()
    response=client.post('/api/v1/auth/login',json={'email':actor.email,'password':'SaaSOnlyTest123!','tenant_id':str(actor.tenant_id)})
    assert response.status_code==200,response.text
    assert response.json()['user']['service_restricted'] is True
    auth={'Authorization':'Bearer '+response.json()['access_token']}
    assert client.get('/api/v1/subscriptions/contract',headers=auth).status_code==200
    assert client.post('/api/v1/categorias',headers=auth,json={'nombre':'Forbidden recovery write'}).status_code==403


def test_prior_paypal_intent_reconciles_suspended_company_once(client,db_session,shops,fake_paypal):
    from tests.test_tenant_contract import headers as shop_headers
    actor,variant,branches=shops['a']
    started=start_payment(db_session,actor,variant,branches[0]);order=started['order_id']
    db_session.connection().execute(update(Tenant).where(Tenant.id==actor.tenant_id).values(activo=False))
    db_session.commit();db_session.expire_all()
    auth=shop_headers(actor)
    assert client.get(f'/api/v1/payments/paypal/orders/{order}/status',headers=auth).status_code==200
    result=client.post(f'/api/v1/payments/paypal/orders/{order}/resume',headers=auth)
    assert result.status_code==200,result.text
    assert client.post(f'/api/v1/payments/paypal/orders/{order}/resume',headers=auth).status_code==200
    assert fake_paypal['captures']==1 and db_session.query(Sale).count()==1
    assert 'contract_reconciliation' not in db_session.info


def test_unproven_prior_paypal_order_never_starts_remote_charge(client,db_session,shops,fake_paypal):
    from tests.test_tenant_contract import headers as shop_headers
    from app.models.payment_intent import PaymentIntent
    actor,variant,branches=shops['a'];other=shops['b'][0]
    order=start_payment(db_session,actor,variant,branches[0])['order_id']
    intent=db_session.query(PaymentIntent).one()
    intent.payload={**intent.payload,'license_term_id':None}
    db_session.connection().execute(update(Tenant).where(Tenant.id==actor.tenant_id).values(activo=False))
    db_session.commit();db_session.expire_all()
    route=f'/api/v1/payments/paypal/orders/{order}/resume'
    assert client.post(route,headers=shop_headers(other)).status_code==404
    response=client.post(route,headers=shop_headers(actor))
    assert response.status_code==409,response.text
    assert fake_paypal['captures']==0 and db_session.query(Sale).count()==0


def test_signed_sync_grant_preserves_cash_protocol_and_unproven_row(client,db_session,shops):
    from tests.test_tenant_contract import headers as shop_headers
    from datetime import datetime,timezone
    actor,variant,branches=shops['a'];auth=shop_headers(actor)
    grant=client.post('/api/v1/sync/authorizations',headers=auth,json={'channel':'SYNC','count':1}).json()['grants'][0]
    db_session.connection().execute(update(Tenant).where(Tenant.id==actor.tenant_id).values(activo=False))
    db_session.commit();db_session.expire_all()
    op={'client_operation_id':str(uuid.uuid4()),'operation_type':'VENTA_CONTADO_OFFLINE',
        'sucursal_id':str(branches[0].id),'cajero_id':str(actor.id),'fecha_local':datetime.now(timezone.utc).isoformat(),
        'items':[{'variante_id':str(variant.id),'cantidad':1,'precio_unitario':10}],
        'monto_total':10,'monto_efectivo_recibido':10,'cambio_entregado':0}
    unproven=client.post('/api/v1/sync/batch',headers=auth,json={'operations':[op]})
    assert unproven.json()['results'][0]['requires_review'] is True
    assert db_session.query(SyncOperation).count()==0
    op.update(client_operation_id=grant['client_operation_id'],offline_authorization=grant['authorization'])
    confirmed=client.post('/api/v1/sync/batch',headers=auth,json={'operations':[op]})
    assert confirmed.json()['results'][0]['status']=='CONFIRMADA',confirmed.text
    assert client.post('/api/v1/sync/batch',headers=auth,json={'operations':[op]}).json()['results'][0]['is_duplicate'] is True
    assert db_session.query(Sale).count()==1 and db_session.query(UserWallet).count()==0


def test_expired_offline_grant_never_trusts_client_date(client,db_session,shops):
    import jwt
    from tests.test_tenant_contract import headers as shop_headers
    actor,_,_=shops['a'];auth=shop_headers(actor)
    grant=client.post('/api/v1/sync/authorizations',headers=auth,json={'channel':'SYNC','count':1}).json()['grants'][0]
    claims=jwt.decode(grant['authorization'],options={'verify_signature':False})
    claims['exp']=1
    expired=jwt.encode(claims,Entitlements._key(),algorithm='HS256')
    db_session.connection().execute(update(Tenant).where(Tenant.id==actor.tenant_id).values(activo=False))
    db_session.commit();db_session.expire_all()
    with pytest.raises(HTTPException) as failure:
        Entitlements.reconcile(db_session,actor,grant['client_operation_id'],'SYNC','VENTA_CONTADO_OFFLINE',expired)
    assert failure.value.status_code==409


def test_future_reduction_reports_excess_without_deleting_branches(client,db_session,contract_setup,monkeypatch):
    from app.services.branch_service import BranchService
    from app.schemas.branch import BranchCreate
    setup=contract_setup;actor=setup['admin']
    version=draft(client,setup['operator'],str(setup['plan'].id),precio_mensual_bob='150.00',max_sucursales=2,
        features={'reportes_ia':False,'analitica_avanzada':False,'offline':False})
    assert transition(client,setup['operator'],version,'publish').status_code==200
    requested,_=accept(client,setup);assert decide(client,setup,requested).status_code==200
    for i in range(3):BranchService.create(db_session,BranchCreate(nombre=f'Growth {i}',ciudad='SC',direccion='Test'),actor.tenant_id)
    legacy=db_session.query(Term).filter_by(origen='REFERENCIA_LEGADA').one()
    monkeypatch.setattr('app.core.billing_calendar.business_today',lambda:legacy.fecha_fin)
    effective=Entitlements.resolve(db_session,actor)
    assert effective['exceso_sucursales']==1 and effective['max_sucursales']==2
    assert db_session.query(Branch).filter_by(tenant_id=actor.tenant_id,activa=True).count()==3
    with pytest.raises(HTTPException):BranchService.create(db_session,BranchCreate(nombre='Overflow',ciudad='SC',direccion='Test'),actor.tenant_id)
    branch=db_session.query(Branch).filter_by(tenant_id=actor.tenant_id).first()
    assert client.post(f'/api/v1/sucursales/{branch.id}/deactivate',headers=headers(actor)).status_code==200
    assert Entitlements.resolve(db_session,actor)['exceso_sucursales']==0
