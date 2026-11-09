from decimal import Decimal
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

def replacement(db,shop):
    u,v,bs=shop;other=ProductVariant(producto_id=v.producto_id,sku='replacement-review',talla='L',color='Azul',precio_extra=0,activo=True)
    db.add(other);db.flush();db.add(Inventory(tenant_id=u.tenant_id,sucursal_id=bs[0].id,variante_id=other.id,stock_actual=10,stock_reservado=0));db.commit();return other

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
