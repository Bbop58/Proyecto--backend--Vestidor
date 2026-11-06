"""P1 uses real company credential selection; only remote PayPal is replaced."""
import uuid,secrets
from pydantic import SecretStr
import pytest
from fastapi import HTTPException
from app.config import settings
from app.models import Tenant,PaymentIntent
from app.api.v1.payments import _resolve_tenant_paypal
from app.services.paypal_service import PayPalService
from app.services.payment_flow_service import PaymentFlowService
from tests.test_tenant_sales import shops
from tests.test_tenant_contract import headers
from tests.support_saas import saas_actors
from tests.support_payments import start_payment


@pytest.fixture(autouse=True)
def global_credentials_are_present(monkeypatch):
    monkeypatch.setattr(settings,'PAYMENT_CREDENTIALS_KEY',SecretStr(secrets.token_urlsafe(32)))
    monkeypatch.setattr(settings,'PAYPAL_CLIENT_ID','PLATFORM-ONLY-SYNTHETIC')
    monkeypatch.setattr(settings,'PAYPAL_CLIENT_SECRET','PLATFORM-SECRET-SYNTHETIC')


def configure(db,user,key):
    from app.services.company_paypal_service import CompanyPayPalService
    CompanyPayPalService.append(db.connection(),user.tenant_id,client_id='merchant-'+key,
        secret='secret-'+key+'-synthetic',mode='sandbox')
    db.commit()



@pytest.mark.parametrize('cid,secret,mode',[(None,None,'sandbox'),('merchant-a',None,'sandbox'),(None,'secret-a','sandbox'),('  ','  ','sandbox'),('merchant-a','secret-a','invalid'),('merchant-a','secret-a',None)])
def test_unconfigured_company_never_falls_back_to_platform(db_session,shops,cid,secret,mode):
    a=shops['a'][0];tenant=db_session.get(Tenant,a.tenant_id)
    tenant.paypal_client_id=cid;tenant.paypal_client_secret=secret;tenant.paypal_mode=mode
    db_session.commit()
    with pytest.raises(HTTPException) as rejected:_resolve_tenant_paypal(db_session,user=a)
    assert rejected.value.status_code==503


def test_both_branches_use_one_company_merchant_and_other_company_is_different(db_session,shops):
    a,_,branches=shops['a'];b=shops['b'][0]
    configure(db_session,a,'a');configure(db_session,b,'b')
    first=_resolve_tenant_paypal(db_session,user=a,sucursal_id=branches[0].id)
    second=_resolve_tenant_paypal(db_session,user=a,sucursal_id=branches[1].id)
    other=_resolve_tenant_paypal(db_session,user=b)
    assert first==second and first['client_id']=='merchant-a' and other['client_id']=='merchant-b'


def test_company_cannot_choose_foreign_tenant_or_branch(db_session,shops):
    a=shops['a'][0];b,_,branches=shops['b'];configure(db_session,a,'a');configure(db_session,b,'b')
    for args in [{'tenant_id':b.tenant_id},{'sucursal_id':branches[0].id}]:
        with pytest.raises(HTTPException) as rejected:_resolve_tenant_paypal(db_session,user=a,**args)
        assert rejected.value.status_code==404


def test_unknown_company_has_no_global_config(db_session):
    with pytest.raises(HTTPException) as rejected:_resolve_tenant_paypal(db_session,tenant_id=uuid.uuid4())
    assert rejected.value.status_code==404


def test_sdk_config_is_authenticated_scoped_and_never_returns_secret(client,db_session,shops):
    a=shops['a'][0];b=shops['b'][0];configure(db_session,a,'a');configure(db_session,b,'b')
    assert client.get('/api/v1/payments/paypal/config').status_code==401
    response=client.get('/api/v1/payments/paypal/config',headers=headers(a))
    assert response.status_code==200,response.text
    assert response.json()['client_id']=='merchant-a'
    assert 'secret' not in response.text and 'PLATFORM-' not in response.text
    forged=client.get('/api/v1/payments/paypal/config',headers=headers(a),params={'tenant_id':str(b.tenant_id)})
    assert forged.status_code==404


def test_operator_cannot_get_commercial_paypal_config(client,saas_actors):
    assert client.get('/api/v1/payments/paypal/config',headers=headers(saas_actors['operator'])).status_code==403


@pytest.mark.parametrize('cid,secret',[(None,None),('merchant-a',None),(None,'secret-a')])
def test_transport_never_substitutes_partial_pair_with_global_credentials(monkeypatch,cid,secret):
    calls=[]
    def request(*args,**kwargs):calls.append(True);return {'access_token':'synthetic-only'}
    monkeypatch.setattr(PayPalService,'_request',request)
    with pytest.raises(HTTPException) as rejected:PayPalService.get_access_token(cid,secret,'sandbox')
    assert rejected.value.status_code==503 and not calls


@pytest.mark.parametrize('purpose',['SALE','WALLET'])
def test_missing_company_account_cannot_create_intent_or_contact_provider(db_session,shops,monkeypatch,purpose):
    a,v,branches=shops['a']
    monkeypatch.setattr(PayPalService,'create_order',lambda **kw:pytest.fail('Unconfigured company reached provider'))
    with pytest.raises(HTTPException) as rejected:start_payment(db_session,a,v,branches[0],purpose=purpose)
    assert rejected.value.status_code==503
    assert db_session.query(PaymentIntent).count()==0


@pytest.mark.parametrize('purpose',['SALE','WALLET'])
def test_create_binds_company_merchant_without_mocking_credentials(db_session,shops,monkeypatch,purpose):
    a,v,branches=shops['a'];configure(db_session,a,'a');calls=[]
    def create(**data):
        calls.append(data)
        return {'order_id':'ORDER-'+str(data['custom_id']),'approve_url':'https://example.test/approve'}
    monkeypatch.setattr(PayPalService,'create_order',create)
    created=start_payment(db_session,a,v,branches[0],purpose=purpose)
    intent=db_session.query(PaymentIntent).filter_by(provider_order_id=created['order_id']).one()
    assert calls[0]['client_id']=='merchant-a' and calls[0]['client_secret']=='secret-a-synthetic'
    assert intent.tenant_id==a.tenant_id and intent.merchant_key==PaymentFlowService.merchant(calls[0])
    before=(intent.id,intent.provider_order_id,intent.merchant_key,intent.state)
    configure(db_session,a,'changed')
    # P2 retains encrypted original credentials: rotation may recover the same seller,
    # without changing the identity or moving an existing payment to the new seller.
    recovered=PaymentFlowService.credentials(db_session,a,intent)
    assert recovered['client_id']=='merchant-a'
    db_session.expire_all();intent=db_session.get(PaymentIntent,before[0])
    assert (intent.id,intent.provider_order_id,intent.merchant_key,intent.state)==before



def test_platform_client_cannot_be_configured_as_company_collector(db_session,shops):
    a=shops['a'][0];tenant=db_session.get(Tenant,a.tenant_id)
    tenant.paypal_client_id=settings.PAYPAL_CLIENT_ID
    tenant.paypal_client_secret=settings.PAYPAL_CLIENT_SECRET
    tenant.paypal_mode='sandbox';db_session.commit()
    with pytest.raises(HTTPException) as rejected:_resolve_tenant_paypal(db_session,user=a)
    assert rejected.value.status_code==503
