from decimal import Decimal
from datetime import datetime, timedelta, timezone
import uuid
import pytest
from fastapi import HTTPException
from tests.test_tenant_sales import shops
from tests.test_commercial_invariants import sale_data,exchange_data,inv_for
from tests.test_commercial_invariants import committed_shop
from tests.test_receipt_documents import sale
from app.services.sale_service import SaleService
from app.services.exchange_service import ExchangeService
from app.models.product_exchange import ProductExchange
from app.models.product_variant import ProductVariant
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement,MovementType
from app.schemas.product_exchange import ProductExchangeCreate
from app.models.payment_intent import PaymentIntent
from app.models.sale import PaymentMethod
from app.services.paypal_service import PayPalService,ProviderError
from app.services.payment_flow_service import PaymentFlowService
from tests.test_receipt_documents import customer

def replacement(db,shop):
    u,v,bs=shop;other=ProductVariant(producto_id=v.producto_id,sku='replacement-review',talla='L',color='Azul',precio_extra=0,activo=True)
    db.add(other);db.flush();db.add(Inventory(tenant_id=u.tenant_id,sucursal_id=bs[0].id,variante_id=other.id,stock_actual=10,stock_reservado=0));db.commit();return other

def return_payload(receipt,variant,method='EFECTIVO',*,operation_id=None,quantity=1):
    return ProductExchangeCreate(client_operation_id=operation_id or uuid.uuid4(),tipo_operacion='DEVOLUCION',
        numero_recibo=receipt,motivo='Devolución de prueba',estado_prenda='BUEN_ESTADO',
        metodo_pago_diferencia=method,items=[dict(variante_devuelta_id=variant.id,cantidad_devuelta=quantity,
            variante_entregada_id=None,cantidad_entregada=0)])

def paypal_sale_payment(db,shop,row,*,amount_usd=None):
    actor,_,branches=shop
    amount_usd=amount_usd or PayPalService.convert_bob_to_usd(row.monto_total,rate=Decimal('6.96'))
    payment=PaymentIntent(tenant_id=actor.tenant_id,user_id=actor.id,cliente_id=row.cliente_id,
        sucursal_id=branches[0].id,purpose='SALE',state='APPLIED',capture_id='CAPTURE-QA-'+uuid.uuid4().hex[:8],
        amount_bob=row.monto_total,amount_usd=amount_usd,exchange_rate=Decimal('6.96'),
        merchant_key='merchant-qa',provider_mode='sandbox',sale_id=row.id,payload={})
    db.add(payment);db.commit();return payment

def test_factory_defect_records_merma_without_increasing_sellable_stock(db_session,shops):
    u,v,bs=shops['a'];other=replacement(db_session,shops['a'])
    result=SaleService.create_presencial_sale(db_session,sale_data(bs[0],v),u.id)
    data=exchange_data(result['numero_recibo'],v,other);data.estado_prenda='DEFECTO_FABRICA'
    before=inv_for(db_session,bs[0],v).stock_actual
    result=ExchangeService.create_exchange(db_session,data,u.id)
    assert inv_for(db_session,bs[0],v).stock_actual==before
    adjustment=db_session.query(InventoryMovement).filter_by(referencia=result['numero_cambio'],tipo=MovementType.AJUSTE).one()
    assert adjustment.cantidad==-1 and adjustment.stock_despues==adjustment.stock_antes-1

def test_notification_failure_does_not_hide_committed_result(db_session,shops,monkeypatch):
    from app.services.notification_service import NotificationService
    u,v,bs=shops['a'];row=sale(db_session,shops['a']);row.descuento=0;v.producto.precio_base=Decimal('25.25');db_session.commit()
    monkeypatch.setattr(NotificationService,'create',lambda **kw:(_ for _ in ()).throw(RuntimeError('injected')))
    result=ExchangeService.create_exchange(db_session,exchange_data(row.numero_recibo,v,v),u.id)
    assert result['numero_cambio'].startswith('CAM-') and db_session.query(ProductExchange).count()==1

def test_exact_retry_returns_original_and_payload_reuse_conflicts(db_session,shops):
    u,v,bs=shops['a'];other=replacement(db_session,shops['a'])
    sold=SaleService.create_presencial_sale(db_session,sale_data(bs[0],v,count=2),u.id)
    payload=exchange_data(sold['numero_recibo'],v,other).model_dump(mode='json')
    payload['client_operation_id']=str(uuid.uuid4());data=ProductExchangeCreate(**payload)
    first=ExchangeService.create_exchange(db_session,data,u.id)
    second=ExchangeService.create_exchange(db_session,ProductExchangeCreate(**payload),u.id)
    assert first['id']==second['id'] and db_session.query(ProductExchange).count()==1
    payload['motivo']='Otra operación'
    with pytest.raises(HTTPException) as err:ExchangeService.create_exchange(db_session,ProductExchangeCreate(**payload),u.id)
    assert err.value.status_code==409

def test_quote_uses_original_discount_and_current_offer_and_revalidates(db_session,shops):
    u,v,_=shops['a'];row=sale(db_session,shops['a']);v.producto.precio_base=40;v.producto.en_oferta=True;v.producto.precio_oferta=24;db_session.commit()
    data=exchange_data(row.numero_recibo,v,v)
    quote=ExchangeService.quote_exchange(db_session,data,u.id)
    assert quote['monto_total_devuelto']=='23.25' and quote['monto_total_entregado']=='24.00' and quote['diferencia']=='0.75'
    data=ProductExchangeCreate(**(data.model_dump()|{'quote_hash':quote['quote_hash'],'metodo_pago_diferencia':'EFECTIVO'}))
    v.producto.precio_oferta=25;db_session.commit()
    with pytest.raises(HTTPException) as err:ExchangeService.create_exchange(db_session,data,u.id)
    assert err.value.status_code==409 and db_session.query(ProductExchange).count()==0

def test_quote_does_not_write_and_matches_successful_exchange_pdf(db_session,shops):
    from app.services.receipt_document_service import get_receipt_document
    from app.services.receipt_pdf_service import render_receipt_pdf
    u,v,_=shops['a'];row=sale(db_session,shops['a']);v.producto.precio_base=24;db_session.commit()
    data=exchange_data(row.numero_recibo,v,v)
    before=db_session.query(InventoryMovement).count()
    quote=ExchangeService.quote_exchange(db_session,data,u.id)
    assert db_session.query(InventoryMovement).count()==before and db_session.query(ProductExchange).count()==0
    data=ProductExchangeCreate(**(data.model_dump()|{'quote_hash':quote['quote_hash'],'metodo_pago_diferencia':'EFECTIVO'}))
    result=ExchangeService.create_exchange(db_session,data,u.id)
    assert result['diferencia']==0.75
    note=get_receipt_document(db_session,u,'exchange',result['id'])
    assert note.number==result['numero_cambio'] and render_receipt_pdf(note).startswith(b'%PDF-')

def test_concurrent_identical_retry_applies_one_change(committed_shop):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.models.branch import Branch
    from app.schemas.inventory import ReceptionCreate
    from app.services.inventory_service import InventoryService
    u,v,b,i=committed_shop[0];factory=committed_shop[1]
    with factory() as db:
        variant=db.get(ProductVariant,v)
        sold=SaleService.create_presencial_sale(db,sale_data(db.get(Branch,b),variant),u)
        InventoryService.receive_products(db,ReceptionCreate(sucursal_id=b,productos=[dict(variante_id=v,cantidad=1)]),u)
        payload=exchange_data(sold['numero_recibo'],variant,variant).model_dump(mode='json')
    barrier=Barrier(2)
    def perform():
        with factory() as db:
            barrier.wait(timeout=10)
            return str(ExchangeService.create_exchange(db,ProductExchangeCreate(**payload),u)['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(perform) for _ in range(2)]
        ids=[f.result(timeout=20) for f in futures]
    assert ids[0]==ids[1]
    with factory() as db:
        assert db.query(ProductExchange).count()==1 and db.get(Inventory,i).stock_actual==1

def test_quote_and_replay_reject_another_company(db_session,shops):
    from app.core.tenant_scope import bind_tenant
    u,v,bs=shops['a'];other=shops['b'][0]
    result=SaleService.create_presencial_sale(db_session,sale_data(bs[0],v,count=2),u.id)
    data=exchange_data(result['numero_recibo'],v,v)
    ExchangeService.create_exchange(db_session,data,u.id)
    with pytest.raises(HTTPException) as err:ExchangeService.create_exchange(db_session,data,other.id)
    assert err.value.status_code==409
    with pytest.raises(HTTPException) as err:ExchangeService.quote_exchange(db_session,data,other.id)
    assert err.value.status_code==404

def test_cash_return_has_no_replacement_restores_stock_and_emits_return_document(db_session,shops):
    from app.models.inventory import Inventory
    from app.models.product_exchange_detail import ProductExchangeDetail
    from app.schemas.sale import PresencialSaleCreate
    from app.services.receipt_document_service import get_receipt_document
    from app.services.receipt_pdf_service import render_receipt_pdf
    actor,variant,branches=shops['a'];client=customer(db_session,actor.tenant_id)
    sale_result=SaleService.create_presencial_sale(db_session,PresencialSaleCreate(sucursal_id=branches[0].id,
        cliente_id=client.id,items=[dict(variante_id=variant.id,cantidad=1)],
        pago=dict(metodo='EFECTIVO',monto_recibido=100)),actor.id)
    stock_after_sale=db_session.query(Inventory).filter_by(sucursal_id=branches[0].id,variante_id=variant.id).one().stock_actual
    data=return_payload(sale_result['numero_recibo'],variant)
    quote=ExchangeService.quote_exchange(db_session,data,actor.id)
    result=ExchangeService.create_exchange(db_session,data.model_copy(update={'quote_hash':quote['quote_hash']}),actor.id)
    detail=db_session.query(ProductExchangeDetail).filter_by(cambio_id=result['id']).one()
    doc=get_receipt_document(db_session,actor,'exchange',result['id'])
    assert result['tipo_operacion']=='DEVOLUCION' and result['diferencia']==-10
    assert result['metodo_pago_diferencia']=='EFECTIVO' and result['estado_liquidacion']=='CONFIRMADO'
    assert detail.variante_entregada_id is None and detail.cantidad_entregada==0
    assert db_session.query(Inventory).filter_by(sucursal_id=branches[0].id,variante_id=variant.id).one().stock_actual==stock_after_sale+1
    assert doc.number==result['numero_cambio'] and 'DEVOLUCIÓN' in doc.title.upper()
    assert render_receipt_pdf(doc).startswith(b'%PDF-')

def test_equal_value_exchange_requires_no_payment_and_keeps_net_stock(db_session,shops):
    from app.models.inventory import Inventory
    actor,variant,branches=shops['a'];row=sale(db_session,shops['a'])
    variant.producto.precio_base=Decimal('23.25');variant.producto.en_oferta=False;db_session.commit()
    inventory=db_session.query(Inventory).filter_by(sucursal_id=branches[0].id,variante_id=variant.id).one()
    stock_before=inventory.stock_actual
    data=exchange_data(row.numero_recibo,variant,variant)
    quote=ExchangeService.quote_exchange(db_session,data,actor.id)
    assert quote['diferencia']=='0.00'
    result=ExchangeService.create_exchange(db_session,data.model_copy(update={'quote_hash':quote['quote_hash']}),actor.id)
    assert result['diferencia']==0 and result['metodo_pago_diferencia']=='NINGUNO'
    assert db_session.query(Inventory).filter_by(sucursal_id=branches[0].id,variante_id=variant.id).one().stock_actual==stock_before

def test_wallet_return_requires_identified_client_and_credits_once(db_session,shops):
    from app.models.user_wallet import UserWallet
    from app.models.wallet_transaction import WalletTransaction
    from app.schemas.sale import PresencialSaleCreate
    actor,variant,branches=shops['a'];client=customer(db_session,actor.tenant_id)
    sale_result=SaleService.create_presencial_sale(db_session,PresencialSaleCreate(sucursal_id=branches[0].id,
        cliente_id=client.id,items=[dict(variante_id=variant.id,cantidad=1)],
        pago=dict(metodo='EFECTIVO',monto_recibido=100)),actor.id)
    data=return_payload(sale_result['numero_recibo'],variant,'BILLETERA')
    quote=ExchangeService.quote_exchange(db_session,data,actor.id)
    payload=data.model_copy(update={'quote_hash':quote['quote_hash']})
    result=ExchangeService.create_exchange(db_session,payload,actor.id)
    wallet=db_session.query(UserWallet).filter_by(user_id=client.id,tenant_id=actor.tenant_id).one()
    tx=db_session.query(WalletTransaction).filter_by(referencia_externa=f"CAMBIO_{result['id']}").one()
    replay=ExchangeService.create_exchange(db_session,payload,actor.id)
    assert wallet.saldo_actual==10 and tx.monto==10
    assert db_session.query(WalletTransaction).filter_by(referencia_externa=f"CAMBIO_{result['id']}").count()==1
    assert replay['id']==result['id']

def test_wallet_refund_is_rejected_for_an_anonymous_sale_without_stock_or_ledger_writes(db_session,shops):
    from app.schemas.sale import PresencialSaleCreate
    actor,variant,branches=shops['a']
    sale_result=SaleService.create_presencial_sale(db_session,PresencialSaleCreate(sucursal_id=branches[0].id,
        items=[dict(variante_id=variant.id,cantidad=1)],pago=dict(metodo='EFECTIVO',monto_recibido=100)),actor.id)
    data=return_payload(sale_result['numero_recibo'],variant,'BILLETERA')
    before=(db_session.query(InventoryMovement).count(),db_session.query(ProductExchange).count())
    with pytest.raises(HTTPException) as err:ExchangeService.quote_exchange(db_session,data,actor.id)
    assert err.value.status_code==409
    assert (db_session.query(InventoryMovement).count(),db_session.query(ProductExchange).count())==before

def test_paypal_refund_pending_reserves_item_then_reconciles_exactly_once(db_session,shops,monkeypatch):
    from app.models.inventory import Inventory
    from app.models.product_exchange import ProductExchange
    actor,variant,branches=shops['a'];row=sale(db_session,shops['a'],metodo_pago=PaymentMethod.PAYPAL)
    paypal_sale_payment(db_session,shops['a'],row)
    monkeypatch.setattr(PaymentFlowService,'credentials',staticmethod(lambda db,user,intent=None:
        {'client_id':'qa-client','client_secret':'qa-secret','mode':'sandbox'}))
    monkeypatch.setattr(PayPalService,'refund_capture',classmethod(lambda cls,capture_id,**kw:
        {'id':'REFUND-QA','status':'PENDING','amount':{'currency_code':'USD','value':str(kw['amount_usd'])}}))
    data=return_payload(row.numero_recibo,variant,'PAYPAL')
    quote=ExchangeService.quote_exchange(db_session,data,actor.id);payload=data.model_copy(update={'quote_hash':quote['quote_hash']})
    pending=ExchangeService.refund_paypal_exchange(db_session,payload,actor.id)
    assert pending['estado']=='PENDIENTE' and db_session.query(ProductExchange).count()==0
    assert ExchangeService.verify_receipt(db_session,row.numero_recibo,actor.tenant_id).items[0].cantidad_disponible_cambio==0
    expected_usd=PayPalService.convert_bob_to_usd(Decimal(quote['monto_total_devuelto']),rate=Decimal('6.96'))
    monkeypatch.setattr(PayPalService,'get_refund_details',classmethod(lambda cls,refund_id,**kw:
        {'id':refund_id,'status':'COMPLETED','amount':{'currency_code':'USD','value':str(expected_usd)}}))
    complete=ExchangeService.refund_paypal_exchange(db_session,payload,actor.id)
    replay=ExchangeService.refund_paypal_exchange(db_session,payload,actor.id)
    assert complete['tipo_operacion']=='DEVOLUCION' and complete['referencia_reembolso']=='REFUND-QA'
    assert replay['id']==complete['id'] and db_session.query(ProductExchange).count()==1
    assert ExchangeService.verify_receipt(db_session,row.numero_recibo,actor.tenant_id).items[0].cantidad_disponible_cambio==0

def test_paypal_refund_cap_is_server_enforced_before_calling_provider(db_session,shops,monkeypatch):
    actor,variant,_=shops['a'];row=sale(db_session,shops['a'],metodo_pago=PaymentMethod.PAYPAL)
    paypal_sale_payment(db_session,shops['a'],row,amount_usd=Decimal('0.01'))
    called=[]
    monkeypatch.setattr(PaymentFlowService,'credentials',staticmethod(lambda db,user,intent=None:
        {'client_id':'qa-client','client_secret':'qa-secret','mode':'sandbox'}))
    monkeypatch.setattr(PayPalService,'refund_capture',classmethod(lambda cls,*a,**kw:called.append(True)))
    data=return_payload(row.numero_recibo,variant,'PAYPAL')
    quote=ExchangeService.quote_exchange(db_session,data,actor.id)
    with pytest.raises(HTTPException) as err:
        ExchangeService.refund_paypal_exchange(db_session,data.model_copy(update={'quote_hash':quote['quote_hash']}),actor.id)
    assert err.value.status_code==409 and not called

def test_paypal_timeout_keeps_the_same_reserved_idempotency_key(db_session,shops,monkeypatch):
    actor,variant,branches=shops['a'];row=sale(db_session,shops['a'],metodo_pago=PaymentMethod.PAYPAL)
    payment=paypal_sale_payment(db_session,shops['a'],row)
    monkeypatch.setattr(PaymentFlowService,'credentials',staticmethod(lambda db,user,intent=None:
        {'client_id':'qa-client','client_secret':'qa-secret','mode':'sandbox'}))
    request_ids=[]
    def timeout(capture_id,**kw):
        request_ids.append(kw['request_id']);raise ProviderError(504,'timeout')
    monkeypatch.setattr(PayPalService,'refund_capture',classmethod(lambda cls,capture_id,**kw:timeout(capture_id,**kw)))
    data=return_payload(row.numero_recibo,variant,'PAYPAL')
    quote=ExchangeService.quote_exchange(db_session,data,actor.id);payload=data.model_copy(update={'quote_hash':quote['quote_hash']})
    pending=ExchangeService.refund_paypal_exchange(db_session,payload,actor.id)
    assert pending['estado']=='PENDIENTE'
    saved=db_session.get(PaymentIntent,payment.id)
    claim=ExchangeService._paypal_refund_claims(saved)[str(data.client_operation_id)]
    assert claim['state']=='PENDING'
    monkeypatch.setattr(PayPalService,'refund_capture',classmethod(lambda cls,capture_id,**kw:
        (request_ids.append(kw['request_id']) or {'id':'REFUND-RETRY-QA','status':'PENDING'})))
    retry=ExchangeService.refund_paypal_exchange(db_session,payload,actor.id)
    assert retry['estado']=='PENDIENTE' and len(request_ids)==2 and request_ids[0]==request_ids[1]
    assert db_session.query(ProductExchange).count()==0
    assert ExchangeService.verify_receipt(db_session,row.numero_recibo,actor.tenant_id).items[0].cantidad_disponible_cambio==0

def test_paypal_refund_is_rejected_when_original_sale_was_not_paid_with_paypal(db_session,shops,monkeypatch):
    actor,variant,_=shops['a'];row=sale(db_session,shops['a'],metodo_pago=PaymentMethod.EFECTIVO)
    called=[]
    monkeypatch.setattr(PayPalService,'refund_capture',classmethod(lambda cls,*a,**kw:called.append(True)))
    data=return_payload(row.numero_recibo,variant,'PAYPAL')
    quote=ExchangeService.quote_exchange(db_session,data,actor.id)
    with pytest.raises(HTTPException) as err:
        ExchangeService.refund_paypal_exchange(db_session,data.model_copy(update={'quote_hash':quote['quote_hash']}),actor.id)
    assert err.value.status_code==409 and not called
    assert db_session.query(ProductExchange).count()==0

def test_definitive_paypal_refund_failure_releases_claim_for_cash_resolution(db_session,shops,monkeypatch):
    from app.models.product_exchange import ProductExchange
    actor,variant,_=shops['a'];row=sale(db_session,shops['a'],metodo_pago=PaymentMethod.PAYPAL)
    paypal_sale_payment(db_session,shops['a'],row)
    monkeypatch.setattr(PaymentFlowService,'credentials',staticmethod(lambda db,user,intent=None:
        {'client_id':'qa-client','client_secret':'qa-secret','mode':'sandbox'}))
    monkeypatch.setattr(PayPalService,'refund_capture',classmethod(lambda cls,*args,**kw:
        {'id':'REFUND-FAILED-QA','status':'FAILED'}))
    paypal=return_payload(row.numero_recibo,variant,'PAYPAL')
    quote=ExchangeService.quote_exchange(db_session,paypal,actor.id)
    paypal=paypal.model_copy(update={'quote_hash':quote['quote_hash']})
    with pytest.raises(HTTPException) as err:ExchangeService.refund_paypal_exchange(db_session,paypal,actor.id)
    assert err.value.status_code==409
    assert ExchangeService.verify_receipt(db_session,row.numero_recibo,actor.tenant_id).items[0].cantidad_disponible_cambio==1
    cash=return_payload(row.numero_recibo,variant,'EFECTIVO')
    cash= cash.model_copy(update={'quote_hash':quote['quote_hash']})
    result=ExchangeService.create_exchange(db_session,cash,actor.id)
    assert result['metodo_pago_diferencia']=='EFECTIVO' and result['tipo_operacion']=='DEVOLUCION'
    assert db_session.query(ProductExchange).count()==1

def test_return_window_expires_after_exactly_thirty_days(db_session,shops):
    actor,variant,_=shops['a'];row=sale(db_session,shops['a'])
    row.created_at=datetime.now(timezone.utc)-timedelta(days=30,seconds=1);db_session.commit()
    verified=ExchangeService.verify_receipt(db_session,row.numero_recibo,actor.tenant_id)
    assert not verified.valido and not verified.dentro_del_plazo
    with pytest.raises(HTTPException) as err:
        ExchangeService.quote_exchange(db_session,return_payload(row.numero_recibo,variant),actor.id)
    assert err.value.status_code==400
