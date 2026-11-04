import pytest
from app.services.paypal_service import PayPalService

@pytest.fixture
def fake_paypal(monkeypatch):
    """Provider lives outside the database so rollbacks cannot undo a remote charge."""
    from app.services.payment_flow_service import PaymentFlowService
    state={'orders':{},'captures':0,'refunds':0}
    monkeypatch.setattr(PaymentFlowService,'credentials',staticmethod(lambda *a:dict(
        client_id='test-client',client_secret='test-placeholder',mode='sandbox',brand_name='Test')))
    def create(**data):
        oid='ORDER-'+str(data['custom_id'])
        raw=dict(id=oid,status='APPROVED',purchase_units=[dict(custom_id=data['custom_id'],
            amount=dict(currency_code='USD',value=str(data['amount_usd'])))])
        state['orders'][oid]=raw
        return dict(order_id=oid,status='CREATED',monto_usd=float(data['amount_usd']),approve_url='https://example.test/approve')
    def capture(oid,**kwargs):
        state['captures']+=1
        raw=state['orders'][oid];raw['status']='COMPLETED'
        raw['purchase_units'][0]['payments']={'captures':[dict(id='CAP-'+oid,status='COMPLETED',
            amount=dict(raw['purchase_units'][0]['amount']))]}
        return dict(status='COMPLETED',raw_response=raw)
    def refund(cid,**kwargs):
        state['refunds']+=1
        raw=state['orders'][cid.removeprefix('CAP-')]
        return dict(id='REF-'+cid,status='COMPLETED',amount=dict(raw['purchase_units'][0]['amount']))
    monkeypatch.setattr(PayPalService,'create_order',create)
    monkeypatch.setattr(PayPalService,'capture_order',capture)
    monkeypatch.setattr(PayPalService,'get_order_details',lambda oid,**kw:state['orders'][oid])
    monkeypatch.setattr(PayPalService,'refund_capture',refund)
    return state


def start_payment(db,user,variant,branch,*,purpose='SALE',amount=10,key=None):
    from app.services.payment_flow_service import PaymentFlowService
    from app.schemas.paypal import PayPalCreateOrderRequest
    from app.schemas.wallet import PayPalTopupRequest
    data=(PayPalCreateOrderRequest(sucursal_id=branch.id,monto_bob=amount,request_id=key,
        detalles=[dict(variante_id=variant.id,cantidad=1)]) if purpose=='SALE'
        else PayPalTopupRequest(monto_bob=amount,request_id=key))
    return PaymentFlowService.create(db,user,data,purpose=purpose)




def confirmed_intent(db, user_id, branch_id, amount):
    """Only for stock unit tests: stand in for the separately verified capture flow."""
    import uuid
    from app.models.payment_intent import PaymentIntent
    from app.models.user import User
    user = db.get(User, user_id)
    record = PaymentIntent(tenant_id=user.tenant_id, user_id=user_id, cliente_id=user_id,
        sucursal_id=branch_id, purpose='SALE', state='CAPTURED', amount_bob=amount,
        amount_usd=amount, exchange_rate=1, merchant_key='test-only', provider_mode='sandbox',
        provider_order_id='TEST-'+uuid.uuid4().hex, capture_id='TESTCAP-'+uuid.uuid4().hex,
        payload={})
    db.add(record); db.commit()
    return record
