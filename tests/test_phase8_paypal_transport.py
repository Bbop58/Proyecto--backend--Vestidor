"""Real transport contract shape: capture POST may return a minimal resource."""
import copy
import pytest
from app.services.paypal_service import PayPalService, ProviderError
from app.services.payment_flow_service import PaymentFlowService
from tests.test_tenant_sales import shops
from tests.support_payments import start_payment
from app.models.sale import Sale
from app.models.inventory import Inventory


def test_provider_headers_request_full_resource_for_capture_and_refund(monkeypatch):
    monkeypatch.setattr(PayPalService,'get_access_token',classmethod(lambda cls,*a,**kw:'test-token'))
    headers=PayPalService._headers(request_id='same-operation')
    assert headers['Prefer']=='return=representation'
    assert headers['PayPal-Request-Id']=='same-operation'


@pytest.mark.parametrize('already_captured',[False,True])
def test_capture_resolves_minimal_response_via_get_not_another_capture(monkeypatch,already_captured):
    counts={'post':0,'get':0}
    full={'id':'ORDER-QA','status':'COMPLETED','purchase_units':[{'custom_id':'internal-id','payments':{'captures':[{'id':'CAPTURE-QA'}]}}]}
    monkeypatch.setattr(PayPalService,'get_access_token',classmethod(lambda cls,*a,**kw:'test-token'))
    def request(url,method,headers,payload=None,**kwargs):
        if method=='POST':
            counts['post']+=1
            if already_captured:raise ProviderError(400,'Completed','ORDER_ALREADY_CAPTURED')
            return {'id':'ORDER-QA','status':'COMPLETED'}
        assert method=='GET' and url.endswith('/orders/ORDER-QA')
        counts['get']+=1
        return copy.deepcopy(full)
    monkeypatch.setattr(PayPalService,'_request',request)
    result=PayPalService.capture_order('ORDER-QA',mode='sandbox',request_id='same-operation')
    assert result['raw_response']==full
    assert counts=={'post':1,'get':1}


def test_first_confirm_applies_minimal_capture_response_and_retry_is_idempotent(db_session,shops,monkeypatch):
    remote={'captures':0,'order':None}
    monkeypatch.setattr(PaymentFlowService,'credentials',staticmethod(lambda *a:dict(client_id='test-client',client_secret='test-only',mode='sandbox',brand_name='Test')))
    monkeypatch.setattr(PayPalService,'get_access_token',classmethod(lambda cls,*a,**kw:'test-token'))
    def request(url,method,headers,payload=None,**kwargs):
        if method=='POST' and url.endswith('/checkout/orders'):
            remote['order']={'id':'ORDER-QA','status':'APPROVED','purchase_units':copy.deepcopy(payload['purchase_units'])}
            return {'id':'ORDER-QA','status':'CREATED','links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow'}]}
        if method=='GET':return copy.deepcopy(remote['order'])
        assert method=='POST' and url.endswith('/orders/ORDER-QA/capture')
        remote['captures']+=1
        remote['order']['status']='COMPLETED'
        unit=remote['order']['purchase_units'][0]
        unit['payments']={'captures':[{'id':'CAPTURE-QA','status':'COMPLETED','amount':copy.deepcopy(unit['amount'])}]}
        return {'id':'ORDER-QA','status':'COMPLETED'}
    monkeypatch.setattr(PayPalService,'_request',request)
    user,variant,branches=shops['a']
    stock=db_session.query(Inventory).filter_by(sucursal_id=branches[0].id).one()
    before=stock.stock_actual
    order=start_payment(db_session,user,variant,branches[0])
    first=PaymentFlowService.capture(db_session,user,order['order_id'])
    repeated=PaymentFlowService.capture(db_session,user,order['order_id'])
    assert first['venta']['id']==repeated['venta']['id']
    assert first['paypal_capture_id']==repeated['paypal_capture_id']
    assert remote['captures']==1 and db_session.query(Sale).count()==1
    db_session.refresh(stock)
    assert stock.stock_actual==before-1
