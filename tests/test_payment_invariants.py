import uuid
from pathlib import Path

RECONCILIATION_SCRIPT = str(Path(__file__).resolve().parents[1]/"scripts"/"reconcile_wallet_signatures.py")
from decimal import Decimal
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from tests.test_tenant_sales import shops
from tests.test_tenant_contract import headers
from tests.test_commercial_invariants import sale_data
from app.models.sale import Sale
from app.models.user_wallet import UserWallet
from app.models.wallet_transaction import WalletTransaction
from app.schemas.wallet import OfflineOrderPayload
from app.services.sale_service import SaleService
from app.services.wallet_service import WalletService
from app.services.paypal_service import PayPalService


def order(branch,variant,amount=10,count=1,operation=None):
    return OfflineOrderPayload(client_operation_id=operation or uuid.uuid4(),sucursal_id=branch.id,
        items=[dict(variante_id=variant.id,cantidad=count,precio_unitario=10)],monto_total=amount)


def test_failed_provider_capture_never_credits_wallet(client,db_session,shops,monkeypatch):
    u,_,_=shops['a']
    def failed(*args,**kwargs): raise HTTPException(502,'Provider rejected')
    monkeypatch.setattr(PayPalService,'capture_order',failed)
    response=client.post('/api/v1/wallet/topup/paypal',headers=headers(u),json={
        'order_id':'DECLINED-ORDER','monto_bob':1000,'monto_usd':1})
    assert response.status_code in (400,404,409,502),response.text
    assert db_session.query(WalletTransaction).count()==0
    assert all(w.saldo_actual==0 for w in db_session.query(UserWallet).all())


def test_unverified_paypal_credit_service_is_rejected(db_session,shops):
    u,_,_=shops['a']
    with pytest.raises(HTTPException):
        WalletService.topup_paypal(db_session,u.id,u.tenant_id,'FABRICATED',1,1000)
    assert db_session.query(WalletTransaction).count()==0


def test_public_digital_reference_is_not_payment_proof(client,db_session,shops):
    u,v,bs=shops['a']
    response=client.post('/api/v1/ventas/digital',headers=headers(u),json={
        'sucursal_id':str(bs[0].id),'token_pago':'invented',
        'items':[dict(variante_id=str(v.id),cantidad=1)]})
    assert response.status_code in (403,409),response.text
    assert db_session.query(Sale).count()==0


def test_cashier_cannot_invent_a_catalog_price(db_session,shops):
    u,v,bs=shops['a']
    with pytest.raises(HTTPException):
        SaleService.create_presencial_sale(db_session,sale_data(bs[0],v,precio_unitario=0.01),u.id)
    assert db_session.query(Sale).count()==0


def test_paypal_method_on_manual_sale_needs_server_confirmation(db_session,shops):
    u,v,bs=shops['a'];data=sale_data(bs[0],v)
    from app.models.sale import PaymentMethod
    data.pago.metodo=PaymentMethod.PAYPAL;data.pago.referencia='FAKE-CAPTURE'
    with pytest.raises(HTTPException): SaleService.create_presencial_sale(db_session,data,u.id)


def test_tampered_balance_cannot_be_resealed_by_a_topup(db_session,shops):
    u,_,_=shops['a'];wallet=WalletService.topup_cash_admin(db_session,u,u.id,100)
    wallet.saldo_actual=500;db_session.commit()
    with pytest.raises(HTTPException): WalletService.topup_cash_admin(db_session,u,u.id,10)
    assert db_session.query(WalletTransaction).count()==1


def test_wallet_held_balance_cannot_be_spent(db_session,shops):
    u,v,bs=shops['a'];wallet=WalletService.topup_cash_admin(db_session,u,u.id,100)
    wallet.saldo_retenido=95;WalletService._seal(wallet);db_session.commit()
    result=WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[order(bs[0],v)])
    assert result['fallidas']==1
    assert db_session.query(Sale).count()==0


def test_wallet_total_must_match_server_items(db_session,shops):
    u,v,bs=shops['a'];WalletService.topup_cash_admin(db_session,u,u.id,100)
    result=WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[order(bs[0],v,amount=1)])
    assert result['fallidas']==1
    assert db_session.query(Sale).count()==0


def test_wallet_replay_with_different_content_is_not_success(db_session,shops):
    u,v,bs=shops['a'];WalletService.topup_cash_admin(db_session,u,u.id,100)
    key=uuid.uuid4()
    result=WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[order(bs[0],v,operation=key)])
    assert result['exitosas']==1
    result=WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[order(bs[0],v,amount=20,count=2,operation=key)])
    assert result['fallidas']==1
    assert db_session.query(Sale).count()==1


def test_negative_internal_cash_credit_rejected(db_session,shops):
    u,_,_=shops['a']
    with pytest.raises(HTTPException): WalletService.topup_cash_admin(db_session,u,u.id,-10)
    assert db_session.query(WalletTransaction).count()==0


from tests.support_payments import fake_paypal, start_payment


@pytest.mark.parametrize('purpose',['SALE','WALLET'])
def test_verified_payment_applied_once(db_session,shops,fake_paypal,purpose):
    from app.services.payment_flow_service import PaymentFlowService
    u,v,bs=shops['a'];response=start_payment(db_session,u,v,bs[0],purpose=purpose)
    oid=response['order_id']
    first=PaymentFlowService.capture(db_session,u,oid,purpose=purpose)
    second=PaymentFlowService.capture(db_session,u,oid,purpose=purpose)
    assert fake_paypal['captures']==1
    if purpose=='SALE':
        assert first['venta']['id']==second['venta']['id']
        assert db_session.query(Sale).count()==1
    else:
        assert second.saldo_actual==10
        assert db_session.query(WalletTransaction).count()==1


@pytest.mark.parametrize('fault',['currency','amount','custom_id','capture_status','order_id'])
def test_untrusted_capture_cannot_apply(db_session,shops,fake_paypal,monkeypatch,fault):
    from app.services.payment_flow_service import PaymentFlowService
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    original=PayPalService.capture_order
    def capture(*args,**kwargs):
        response=original(*args,**kwargs);raw=response['raw_response'];unit=raw['purchase_units'][0];cap=unit['payments']['captures'][0]
        if fault=='currency':cap['amount']['currency_code']='BOB'
        if fault=='amount':cap['amount']['value']='0.01'
        if fault=='custom_id':unit['custom_id']='another-order'
        if fault=='capture_status':cap['status']='PENDING'
        if fault=='order_id':raw['id']='another-order'
        return response
    monkeypatch.setattr(PayPalService,'capture_order',capture)
    with pytest.raises(HTTPException):PaymentFlowService.capture(db_session,u,oid)
    assert db_session.query(Sale).count()==0


def test_capture_timeout_recovers_the_same_remote_charge(db_session,shops,fake_paypal,monkeypatch):
    from app.services.payment_flow_service import PaymentFlowService
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    original=PayPalService.capture_order
    def uncertain(*args,**kwargs):original(*args,**kwargs);raise HTTPException(504,'response lost')
    monkeypatch.setattr(PayPalService,'capture_order',uncertain)
    with pytest.raises(HTTPException): PaymentFlowService.capture(db_session,u,oid)
    result=PaymentFlowService.capture(db_session,u,oid)
    assert result['venta']['monto_total']==10
    assert fake_paypal['captures']==1
    assert db_session.query(Sale).count()==1


@pytest.mark.parametrize('refund_failure',[False,True])
def test_sale_failure_after_charge_persists_compensation(db_session,shops,fake_paypal,monkeypatch,refund_failure):
    from app.services.payment_flow_service import PaymentFlowService
    from app.models.payment_intent import PaymentIntent
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    monkeypatch.setattr(SaleService,'create_presencial_sale',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('injected failure')))
    if refund_failure:
        monkeypatch.setattr(PayPalService,'refund_capture',lambda *a,**kw:(_ for _ in ()).throw(HTTPException(504,'timeout')))
    with pytest.raises(HTTPException):PaymentFlowService.capture(db_session,u,oid)
    intent=db_session.query(PaymentIntent).filter_by(provider_order_id=oid).one()
    assert intent.state==('REFUND_PENDING' if refund_failure else 'REFUNDED')
    assert intent.capture_id
    assert db_session.query(Sale).count()==0
    with pytest.raises(HTTPException):PaymentFlowService.capture(db_session,u,oid)
    assert fake_paypal['captures']==1


def test_topup_callback_amount_is_not_authority(client,db_session,shops,fake_paypal):
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0],purpose='WALLET')['order_id']
    response=client.post('/api/v1/wallet/topup/paypal',headers=headers(u),json={
        'order_id':oid,'monto_bob':99999,'monto_usd':9999})
    assert response.status_code==200,response.text
    assert response.json()['saldo_actual']==10


@pytest.mark.parametrize('purpose',['SALE','WALLET'])
def test_other_company_cannot_capture_the_order(db_session,shops,fake_paypal,purpose):
    from app.services.payment_flow_service import PaymentFlowService
    u,v,bs=shops['a'];other,_,_=shops['b'];oid=start_payment(db_session,u,v,bs[0],purpose=purpose)['order_id']
    with pytest.raises(HTTPException):PaymentFlowService.capture(db_session,other,oid,purpose=purpose)
    assert fake_paypal['captures']==0


def test_payment_purpose_cannot_be_switched(db_session,shops,fake_paypal):
    from app.services.payment_flow_service import PaymentFlowService
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    with pytest.raises(HTTPException):PaymentFlowService.capture(db_session,u,oid,purpose='WALLET')
    assert fake_paypal['captures']==0


def test_cash_topup_retry_and_changed_amount(db_session,shops):
    u,_,_=shops['a'];key=uuid.uuid4()
    WalletService.topup_cash_admin(db_session,u,u.id,10,client_operation_id=key)
    wallet=WalletService.topup_cash_admin(db_session,u,u.id,10,client_operation_id=key)
    assert wallet.saldo_actual==10 and wallet.nonce_secuencia==1
    with pytest.raises(HTTPException):WalletService.topup_cash_admin(db_session,u,u.id,20,client_operation_id=key)
    assert db_session.query(WalletTransaction).count()==1


def test_create_request_retries_after_price_change_without_new_provider_order(db_session,shops,fake_paypal):
    u,v,bs=shops['a'];key=uuid.uuid4()
    first=start_payment(db_session,u,v,bs[0],key=key)
    v.producto.precio_base=20;db_session.commit()
    second=start_payment(db_session,u,v,bs[0],key=key)
    assert first['order_id']==second['order_id']
    assert len(fake_paypal['orders'])==1


def test_price_change_before_capture_is_not_charged(db_session,shops,fake_paypal):
    from app.services.payment_flow_service import PaymentFlowService
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    v.producto.precio_base=20;db_session.commit()
    with pytest.raises(HTTPException):PaymentFlowService.capture(db_session,u,oid)
    assert fake_paypal['captures']==0


def test_capture_callback_cannot_replace_items(client,db_session,shops,fake_paypal):
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    response=client.post('/api/v1/payments/paypal/capture-order',headers=headers(u),json={
        'order_id':oid,'sucursal_id':str(bs[0].id),'detalles':[dict(variante_id=str(v.id),cantidad=2)]})
    assert response.status_code==409,response.text
    assert fake_paypal['captures']==0


def test_wallet_sale_cancellation_refunds_ledger_once(db_session,shops):
    u,v,bs=shops['a'];WalletService.topup_cash_admin(db_session,u,u.id,100)
    WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[order(bs[0],v)])
    sale=db_session.query(Sale).one()
    SaleService.cancel_sale(db_session,sale.id,'Devolución de prueba',u.id)
    wallet=db_session.query(UserWallet).one()
    assert wallet.saldo_actual==100 and WalletService.verify_wallet_integrity(wallet)
    assert db_session.query(WalletTransaction).count()==3
    with pytest.raises(HTTPException):SaleService.cancel_sale(db_session,sale.id,'Repetida',u.id)
    assert db_session.query(WalletTransaction).count()==3


def test_paypal_cancel_waits_for_confirmed_refund(db_session,shops,fake_paypal,monkeypatch):
    from app.services.payment_flow_service import PaymentFlowService
    from app.models.inventory import Inventory
    from app.models.payment_intent import PaymentIntent
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    result=PaymentFlowService.capture(db_session,u,oid);sid=result['venta']['id']
    monkeypatch.setattr(PayPalService,'refund_capture',lambda *a,**kw:dict(id='REF-PENDING',status='PENDING'))
    with pytest.raises(HTTPException):PaymentFlowService.refund_sale(db_session,u,sid,'Cancelar')
    assert db_session.get(Sale,sid).estado.value=='COMPLETADA'
    assert db_session.query(PaymentIntent).one().state=='REFUND_PENDING'
    amount=fake_paypal['orders'][oid]['purchase_units'][0]['amount']
    monkeypatch.setattr(PayPalService,'get_refund_details',lambda *a,**kw:dict(id='REF-PENDING',status='COMPLETED',amount=amount))
    PaymentFlowService.refund_sale(db_session,u,sid,'Cancelar')
    PaymentFlowService.refund_sale(db_session,u,sid,'Cancelar otra vez')
    assert db_session.get(Sale,sid).estado.value=='CANCELADA'
    inv=db_session.query(Inventory).filter_by(sucursal_id=bs[0].id,variante_id=v.id).one()
    assert inv.stock_actual==10


def test_offer_and_coupon_are_computed_and_consumed_on_server(db_session,shops):
    from app.models.coupon import Coupon
    u,v,bs=shops['a'];v.producto.en_oferta=True;v.producto.precio_oferta=8
    coupon=Coupon(tenant_id=u.tenant_id,codigo='ONCE',porcentaje_descuento=25,monto_minimo=0,max_usos=1,usos_actuales=0,activo=True)
    db_session.add(coupon);db_session.commit()
    data=sale_data(bs[0],v);data.codigo_cupon='ONCE'
    sale=SaleService.create_presencial_sale(db_session,data,u.id)
    assert sale['monto_total']==6
    assert db_session.query(Sale).one().monto_subtotal==8
    assert coupon.usos_actuales==1
    with pytest.raises(HTTPException):SaleService.create_presencial_sale(db_session,data,u.id)
    assert db_session.query(Sale).count()==1


def test_cheaper_exchange_credits_identified_customer_wallet(db_session,shops):
    from app.services.exchange_service import ExchangeService
    from tests.test_commercial_invariants import exchange_data
    u,v,bs=shops['a'];data=sale_data(bs[0],v);data.cliente_id=u.id
    sale=SaleService.create_presencial_sale(db_session,data,u.id)
    v.producto.precio_base=5;db_session.commit()
    result=ExchangeService.create_exchange(db_session,exchange_data(sale['numero_recibo'],v,v),u.id)
    assert result['diferencia']==-5
    assert result['metodo_pago_diferencia']=='BILLETERA'
    wallet=db_session.query(UserWallet).one()
    assert wallet.saldo_actual==5 and WalletService.verify_wallet_integrity(wallet)
    assert db_session.query(WalletTransaction).one().monto==5


def test_exchange_does_not_refund_the_discount_as_if_it_was_paid(db_session,shops):
    from app.models.coupon import Coupon
    from app.services.exchange_service import ExchangeService
    from tests.test_commercial_invariants import exchange_data
    u,v,bs=shops['a'];coupon=Coupon(tenant_id=u.tenant_id,codigo='HALF',porcentaje_descuento=50,monto_minimo=0,activo=True)
    db_session.add(coupon);db_session.commit()
    data=sale_data(bs[0],v);data.cliente_id=u.id;data.codigo_cupon='HALF'
    sale=SaleService.create_presencial_sale(db_session,data,u.id)
    v.producto.precio_base=5;db_session.commit()
    result=ExchangeService.create_exchange(db_session,exchange_data(sale['numero_recibo'],v,v),u.id)
    assert result['monto_total_devuelto']==5
    assert result['diferencia']==0
    assert db_session.query(WalletTransaction).count()==0


from tests.test_commercial_invariants import committed_shop


@pytest.mark.parametrize('operation',['create','cash-same','cash-different','debit','debit-same','capture'])
def test_concurrent_money_operations(committed_shop,fake_paypal,operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import text
    from app.models.user import User
    from app.models.product_variant import ProductVariant
    from app.models.branch import Branch
    from app.models.inventory import Inventory
    from app.models.permission import Permission
    from app.services.payment_flow_service import PaymentFlowService
    user_id,variant_id,branch_id,inventory_id=committed_shop[0];factory=committed_shop[1]
    with factory() as db:
        user=db.get(User,user_id)
        permission=db.query(Permission).filter_by(code='ventas.create').first()
        if not permission:
            permission=Permission(code='ventas.create',module='Ventas',method='POST',path='/ventas')
            db.add(permission)
        user.role.permissions.append(permission)
        db.get(Inventory,inventory_id).stock_actual=10;db.commit()
        if operation.startswith('debit'):
            WalletService.topup_cash_admin(db,user,user.id,15 if operation=='debit' else 50)
        if operation=='capture':
            oid=start_payment(db,user,db.get(ProductVariant,variant_id),db.get(Branch,branch_id))['order_id']
    key=uuid.uuid4();barrier=Barrier(2)
    def perform(index):
        with factory() as db:
            db.execute(text("SET LOCAL lock_timeout='8s'"))
            user=db.get(User,user_id);variant=db.get(ProductVariant,variant_id);branch=db.get(Branch,branch_id)
            barrier.wait(timeout=10)
            if operation=='create':return str(WalletService.get_or_create_wallet(db,user_id,user.tenant_id).id)
            if operation.startswith('cash'):
                return WalletService.topup_cash_admin(db,user,user.id,10,
                    client_operation_id=key if operation=='cash-same' else uuid.uuid4()).saldo_actual
            if operation.startswith('debit'):
                result=WalletService.sync_offline_orders_batch(db,user_id,user.tenant_id,[order(branch,variant,
                    operation=key if operation=='debit-same' else uuid.uuid4())])
                return result['exitosas']
            return str(PaymentFlowService.capture(db,user,oid)['venta']['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(perform,i) for i in range(2)]
        results=[future.result(timeout=30) for future in futures]
    with factory() as db:
        if operation=='capture':
            assert results[0]==results[1] and fake_paypal['captures']==1
            assert db.query(Sale).count()==1
        else:
            wallet=db.query(UserWallet).one()
            assert WalletService.verify_wallet_integrity(wallet)
            if operation=='create':assert results[0]==results[1]
            if operation=='cash-same':assert wallet.saldo_actual==10 and wallet.nonce_secuencia==1
            if operation=='cash-different':assert wallet.saldo_actual==20 and wallet.nonce_secuencia==2
            if operation=='debit':assert sum(results)==1 and wallet.saldo_actual==5
            if operation=='debit-same':assert sum(results)==2 and wallet.saldo_actual==40 and db.query(Sale).count()==1


def test_unsigned_wallet_is_not_silently_certified(db_session,shops):
    u,_,_=shops['a'];wallet=WalletService.topup_cash_admin(db_session,u,u.id,10)
    wallet.firma_balance=None;db_session.commit()
    with pytest.raises(HTTPException):WalletService.get_or_create_wallet(db_session,u.id,u.tenant_id)
    assert db_session.get(UserWallet,wallet.id).firma_balance is None


def test_missing_private_wallet_key_stops_credit(db_session,shops,monkeypatch):
    from app.config import settings
    u,_,_=shops['a'];monkeypatch.setattr(settings,'WALLET_SIGNING_KEY',None)
    with pytest.raises(HTTPException):WalletService.topup_cash_admin(db_session,u,u.id,10)
    assert db_session.query(WalletTransaction).count()==0


def test_wallet_signature_includes_tenant_and_retained_balance():
    uid=uuid.uuid4();tenant=uuid.uuid4()
    base=WalletService._compute_signature(uid,100,1,tenant,0)
    assert WalletService._compute_signature(uid,100,1,uuid.uuid4(),0)!=base
    assert WalletService._compute_signature(uid,100,1,tenant,10)!=base


@pytest.mark.parametrize('actual,held',[(Decimal('-1'),0),(10,11),(10,-1),(Decimal('NaN'),0)])
def test_database_rejects_invalid_wallet_balance(db_session,shops,actual,held):
    from sqlalchemy.exc import IntegrityError
    u,_,_=shops['a'];wallet=WalletService.topup_cash_admin(db_session,u,u.id,10)
    wallet.saldo_actual=actual;wallet.saldo_retenido=held
    with pytest.raises(IntegrityError):db_session.commit()
    db_session.rollback()


def test_database_rejects_incoherent_ledger(db_session,shops):
    from sqlalchemy.exc import IntegrityError
    u,_,_=shops['a'];wallet=WalletService.topup_cash_admin(db_session,u,u.id,10)
    db_session.add(WalletTransaction(wallet_id=wallet.id,tenant_id=u.tenant_id,tipo='INVALID',
        monto=10,saldo_anterior=10,saldo_posterior=999,origen='SERVER',estado_sync='CONCILIADO'))
    with pytest.raises(IntegrityError):db_session.commit()
    db_session.rollback()


def test_pending_capture_can_be_reconciled_without_recapturing(db_session,shops,fake_paypal,monkeypatch):
    from app.services.payment_flow_service import PaymentFlowService
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    original=PayPalService.capture_order
    def pending(*args,**kwargs):
        result=original(*args,**kwargs)
        result['raw_response']['purchase_units'][0]['payments']['captures'][0]['status']='PENDING'
        return result
    monkeypatch.setattr(PayPalService,'capture_order',pending)
    with pytest.raises(HTTPException):PaymentFlowService.capture(db_session,u,oid)
    fake_paypal['orders'][oid]['purchase_units'][0]['payments']['captures'][0]['status']='COMPLETED'
    PaymentFlowService.capture(db_session,u,oid)
    assert fake_paypal['captures']==1 and db_session.query(Sale).count()==1


def test_wrong_refund_amount_never_annuls_sale(db_session,shops,fake_paypal,monkeypatch):
    from app.services.payment_flow_service import PaymentFlowService
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    sale=PaymentFlowService.capture(db_session,u,oid)['venta']
    monkeypatch.setattr(PayPalService,'refund_capture',lambda *a,**kw:dict(id='WRONG-REFUND',
        status='COMPLETED',amount={'currency_code':'USD','value':'0.01'}))
    with pytest.raises(HTTPException):PaymentFlowService.refund_sale(db_session,u,sale['id'],'Cancelar')
    assert db_session.get(Sale,sale['id']).estado.value=='COMPLETADA'


def test_capture_id_cannot_credit_two_orders(db_session,shops,fake_paypal,monkeypatch):
    from app.services.payment_flow_service import PaymentFlowService
    from app.models.payment_intent import PaymentIntent
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    PaymentFlowService.capture(db_session,u,oid)
    existing=db_session.query(PaymentIntent).filter_by(provider_order_id=oid).one()
    capture_id=existing.capture_id
    second=start_payment(db_session,u,v,bs[0])['order_id']
    original=PayPalService.capture_order
    def duplicate(*args,**kwargs):
        response=original(*args,**kwargs)
        response['raw_response']['purchase_units'][0]['payments']['captures'][0]['id']=capture_id
        return response
    monkeypatch.setattr(PayPalService,'capture_order',duplicate)
    with pytest.raises(HTTPException):PaymentFlowService.capture(db_session,u,second)
    assert db_session.query(Sale).count()==1
    assert db_session.query(PaymentIntent).filter_by(provider_order_id=second).one().state=='REVIEW'


def test_paypal_reservation_uses_reserved_price_and_customer(db_session,shops,fake_paypal):
    from app.schemas.paypal import PayPalCreateOrderRequest
    from app.services.payment_flow_service import PaymentFlowService
    from tests.test_commercial_invariants import reserve
    from app.models.reservation import Reservation
    from app.models.inventory import Inventory
    u,v,bs=shops['a'];reservation=reserve(db_session,u,v,bs[0])
    v.producto.precio_base=20;db_session.commit()
    request=PayPalCreateOrderRequest(sucursal_id=bs[0].id,monto_bob=20,
        detalles=[dict(variante_id=v.id,cantidad=2,reserva_id=reservation['id'])])
    created=PaymentFlowService.create(db_session,u,request)
    sale=PaymentFlowService.capture(db_session,u,created['order_id'])['venta']
    assert sale['cliente_id']==u.id and sale['monto_total']==20
    assert db_session.get(Reservation,reservation['id']).estado.value=='COMPLETADA'
    assert db_session.query(Inventory).filter_by(sucursal_id=bs[0].id,variante_id=v.id).one().stock_reservado==0


def test_anonymous_cheaper_exchange_requires_customer(db_session,shops):
    from app.services.exchange_service import ExchangeService
    from tests.test_commercial_invariants import exchange_data
    u,v,bs=shops['a'];sale=SaleService.create_presencial_sale(db_session,sale_data(bs[0],v),u.id)
    v.producto.precio_base=5;db_session.commit()
    with pytest.raises(HTTPException):ExchangeService.create_exchange(db_session,exchange_data(sale['numero_recibo'],v,v),u.id)
    assert db_session.query(WalletTransaction).count()==0


def test_reconciliation_tool_requires_matching_ledger_before_resigning(committed_shop):
    import os,subprocess,sys
    from app.models.user import User
    from app.models.permission import Permission
    from tests.conftest import TEST_DATABASE_URL
    user_id,_,_,_=committed_shop[0];factory=committed_shop[1]
    with factory() as db:
        user=db.get(User,user_id)
        permission=Permission(code='ventas.create',module='Ventas',method='POST',path='/ventas')
        db.add(permission);user.role.permissions.append(permission);db.commit()
        wallet=WalletService.topup_cash_admin(db,user,user_id,10)
        wallet.firma_balance='legacy';db.commit();wallet_id=wallet.id
    schema=factory.kw['bind'].get_execution_options()['schema_translate_map'][None]
    environment={**os.environ,'WALLET_RECONCILIATION_DATABASE_URL':TEST_DATABASE_URL,
                 'PGOPTIONS':f'-c search_path={schema},public'}
    result=subprocess.run([sys.executable,RECONCILIATION_SCRIPT,'--apply'],env=environment,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    with factory() as db:
        wallet=db.get(UserWallet,wallet_id);assert WalletService.verify_wallet_integrity(wallet)
        signature=wallet.firma_balance;wallet.saldo_actual=999;db.commit()
    result=subprocess.run([sys.executable,RECONCILIATION_SCRIPT,'--apply'],env=environment,capture_output=True,text=True,timeout=90)
    assert result.returncode!=0
    with factory() as db:assert db.get(UserWallet,wallet_id).firma_balance==signature


def test_paypal_transport_sets_idempotency_and_timeout(monkeypatch):
    import json
    import urllib.request
    captured=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'id':'ORDER-TRANSPORT','status':'APPROVED'}).encode()
    def urlopen(request,timeout):captured.append((request,timeout));return Response()
    monkeypatch.setattr(urllib.request,'urlopen',urlopen)
    monkeypatch.setattr(PayPalService,'get_access_token',classmethod(lambda cls,*a,**kw:'test-token'))
    PayPalService.capture_order('ORDER-TRANSPORT',request_id='stable-operation')
    request,timeout=captured[0]
    assert request.get_header('Paypal-request-id')=='stable-operation'
    assert timeout==20


def test_cash_receipt_is_stable_and_uses_the_credited_cents(client,db_session,shops):
    u,_,_=shops['a'];key=str(uuid.uuid4())
    payload={'cliente_id':str(u.id),'monto_bs':10.555,'client_operation_id':key}
    first=client.post('/api/v1/wallet/admin/topup-cash',headers=headers(u),json=payload)
    second=client.post('/api/v1/wallet/admin/topup-cash',headers=headers(u),json=payload)
    assert first.status_code==second.status_code==200,(first.text,second.text)
    assert first.json()['recibo_caja']==second.json()['recibo_caja']
    assert key in first.json()['recibo_caja']['numero_comprobante']
    assert first.json()['recibo_caja']['monto']==10.56
    assert second.json()['nuevo_saldo']==10.56


def test_new_checkout_recovers_unknown_previous_capture_before_creating_another(db_session,shops,fake_paypal,monkeypatch):
    from app.services.payment_flow_service import PaymentFlowService
    u,v,bs=shops['a'];oid=start_payment(db_session,u,v,bs[0])['order_id']
    original=PayPalService.capture_order
    def uncertain(*args,**kwargs):original(*args,**kwargs);raise HTTPException(504,'response lost')
    monkeypatch.setattr(PayPalService,'capture_order',uncertain)
    with pytest.raises(HTTPException):PaymentFlowService.capture(db_session,u,oid)
    with pytest.raises(HTTPException):start_payment(db_session,u,v,bs[0])
    assert fake_paypal['captures']==1 and len(fake_paypal['orders'])==1
    assert db_session.query(Sale).count()==1


def test_reconciliation_refuses_historical_unverified_paypal_credit(committed_shop):
    import os,subprocess,sys
    from tests.conftest import TEST_DATABASE_URL
    from app.models.user import User
    user_id,_,_,_=committed_shop[0];factory=committed_shop[1]
    with factory() as db:
        user=db.get(User,user_id)
        wallet=WalletService.get_or_create_wallet(db,user_id,user.tenant_id)
        wallet.saldo_actual=10;wallet.nonce_secuencia=1;wallet.firma_balance='legacy'
        db.add(WalletTransaction(wallet_id=wallet.id,tenant_id=user.tenant_id,tipo='RECARGA_PAYPAL',
            monto=10,saldo_anterior=0,saldo_posterior=10,referencia_externa='PAYPAL_UNVERIFIED',
            origen='MOBILE',estado_sync='CONCILIADO'))
        db.commit();wid=wallet.id
    result=subprocess.run([sys.executable,RECONCILIATION_SCRIPT,'--apply'],
        env={**os.environ,'WALLET_RECONCILIATION_DATABASE_URL':TEST_DATABASE_URL,
             'PGOPTIONS':'-c search_path='+factory.kw['bind'].get_execution_options()['schema_translate_map'][None]+',public'},
        capture_output=True,text=True,timeout=90)
    assert result.returncode!=0
    with factory() as db:assert db.get(UserWallet,wid).firma_balance=='legacy'


def test_wallet_payment_is_separate_from_provider_and_cannot_be_fabricated(db_session,shops):
    from app.models.sale import PaymentMethod
    u,v,bs=shops['a'];WalletService.topup_cash_admin(db_session,u,u.id,100)
    WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[order(bs[0],v)])
    summary=SaleService.get_summary_report(db_session,tenant_id=u.tenant_id)
    assert summary['ventas_billetera']==10 and summary['ventas_paypal']==0
    assert db_session.query(Sale).one().metodo_pago==PaymentMethod.BILLETERA
    data=sale_data(bs[0],v);data.pago.metodo=PaymentMethod.BILLETERA
    with pytest.raises(HTTPException):SaleService.create_presencial_sale(db_session,data,u.id)
    assert db_session.query(UserWallet).one().saldo_actual==90
