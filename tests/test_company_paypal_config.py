import secrets,json,uuid
import pytest
from pydantic import SecretStr
from fastapi import HTTPException
from app.config import settings
from app.models import Tenant,PaymentIntent
from app.models.company_paypal_credential import CompanyPayPalCredential as Credential
from app.services.company_paypal_service import CompanyPayPalService as Service
from app.services.paypal_service import PayPalService
from app.schemas.company_paypal import CompanyPayPalUpdate
from app.core.payment_credentials_crypto import encrypt_secret,decrypt_secret
from tests.test_tenant_sales import shops
from tests.test_commercial_invariants import committed_shop
from tests.test_tenant_contract import headers
from tests.support_saas import saas_actors
from tests.support_payments import fake_paypal

@pytest.fixture(autouse=True)
def payment_key(monkeypatch):
    monkeypatch.setattr(settings,'PAYMENT_CREDENTIALS_KEY',SecretStr(secrets.token_urlsafe(32)))
    monkeypatch.setattr(PayPalService,'get_access_token',lambda *a,**kw:'synthetic-token')

def body(cid='merchant-a',secret='synthetic-secret-never-return',revision=0):
    return CompanyPayPalUpdate(client_id=cid,client_secret=secret,mode='sandbox',expected_revision=revision)

def test_secret_is_encrypted_and_bound_to_company_revision(db_session,shops):
    a=shops['a'][0];state=Service.configure(db_session,a,body())
    row=db_session.query(Credential).one()
    assert row.secret_ciphertext.startswith('PP2.') and 'synthetic-secret' not in row.secret_ciphertext
    assert 'secret' not in json.dumps(state,default=str)
    assert Service.resolve(db_session,a.tenant_id)['client_secret']=='synthetic-secret-never-return'
    with pytest.raises(HTTPException):decrypt_secret(row.secret_ciphertext,shops['b'][0].tenant_id,row.id,row.revision,row.client_id,row.mode)

def test_wrong_key_does_not_fallback_to_plaintext_or_global(db_session,shops,monkeypatch):
    a=shops['a'][0];Service.configure(db_session,a,body())
    monkeypatch.setattr(settings,'PAYMENT_CREDENTIALS_KEY',SecretStr(secrets.token_urlsafe(32)))
    with pytest.raises(HTTPException) as rejected:Service.resolve(db_session,a.tenant_id)
    assert rejected.value.status_code==503
    assert not Service.availability(db_session,a)['enabled']

def test_stale_revision_preserves_previous_config(db_session,shops):
    a=shops['a'][0];Service.configure(db_session,a,body())
    with pytest.raises(HTTPException) as rejected:Service.configure(db_session,a,body('merchant-b'))
    assert rejected.value.status_code==409 and db_session.query(Credential).count()==1

def test_rotation_and_disable_preserve_original_credential(db_session,shops):
    a=shops['a'][0];Service.configure(db_session,a,body())
    old=db_session.query(Credential).one()
    intent=type('Intent',(),{'tenant_id':a.tenant_id,'merchant_key':Service.merchant('sandbox','merchant-a'),'payload':{'paypal_credential_id':str(old.id)}})()
    Service.configure(db_session,a,body('merchant-new','new-synthetic-secret',1))
    assert Service.resolve(db_session,a.tenant_id)['client_id']=='merchant-new'
    assert Service.resolve(db_session,a.tenant_id,intent)['client_id']=='merchant-a'
    Service.disable(db_session,a,2)
    assert not Service.availability(db_session,a)['enabled']
    assert Service.resolve(db_session,a.tenant_id,intent)['client_id']=='merchant-a'

def test_api_hides_secret_and_validation_input(client,db_session,shops):
    a=shops['a'][0]
    response=client.put('/api/v1/company/paypal',headers=headers(a),json=body().model_dump(mode='json')|{'client_secret':'synthetic-secret-never-return'})
    assert response.status_code==200,response.text
    assert 'synthetic-secret' not in response.text and 'ciphertext' not in response.text
    invalid=client.put('/api/v1/company/paypal',headers=headers(a),json={'secret_typo':'private-unknown-input'})
    assert invalid.status_code==422 and 'private-unknown-input' not in invalid.text

def test_branding_cannot_write_payment_secret(client,shops):
    response=client.put('/api/v1/subscriptions/branding',headers=headers(shops['a'][0]),json={'paypal_client_secret':'synthetic-secret-never-return'})
    assert response.status_code==409 and 'synthetic-secret' not in response.text

def test_operator_rejected_even_with_permission(client,saas_actors):
    assert client.get('/api/v1/company/paypal',headers=headers(saas_actors['operator'])).status_code==403


def test_invalid_oauth_does_not_replace_valid_account(db_session,shops,monkeypatch):
    a=shops['a'][0];Service.configure(db_session,a,body())
    def fail(*args,**kwargs):raise HTTPException(400,'Proveedor rechazó las credenciales')
    monkeypatch.setattr(PayPalService,'get_access_token',fail)
    with pytest.raises(HTTPException):Service.configure(db_session,a,body('merchant-new','invalid-synthetic',1))
    assert Service.resolve(db_session,a.tenant_id)['client_id']=='merchant-a'
    assert db_session.query(Credential).count()==1


def test_same_merchant_secret_rotation_recovers_old_intent_without_moving_money(db_session,shops):
    a=shops['a'][0];Service.configure(db_session,a,body());old=db_session.query(Credential).one()
    intent=type('Intent',(),{'tenant_id':a.tenant_id,'merchant_key':Service.merchant('sandbox','merchant-a'),'payload':{'paypal_credential_id':str(old.id)}})()
    Service.configure(db_session,a,body('merchant-a','rotated-synthetic-secret',1))
    assert Service.resolve(db_session,a.tenant_id,intent)['client_secret']=='rotated-synthetic-secret'
    assert intent.payload['paypal_credential_id']==str(old.id)


def test_disabled_old_credential_cannot_be_read_from_other_company(db_session,shops):
    a=shops['a'][0];Service.configure(db_session,a,body());old=db_session.query(Credential).one()
    intent=type('Intent',(),{'tenant_id':a.tenant_id,'merchant_key':old.merchant_key,'payload':{'paypal_credential_id':str(old.id)}})()
    with pytest.raises(HTTPException) as rejected:Service.resolve(db_session,shops['b'][0].tenant_id,intent)
    assert rejected.value.status_code==404


def test_customer_without_permission_cannot_manage_secrets(client,db_session,shops):
    from app.models import Role
    a=shops['a'][0];a.role_id=db_session.query(Role).filter_by(name='cliente').one().id;db_session.commit()
    assert client.get('/api/v1/company/paypal',headers=headers(a)).status_code==403
    availability=client.get('/api/v1/payments/paypal/availability',headers=headers(a))
    assert availability.status_code==200 and availability.json()['enabled'] is False
    assert 'client_id' not in availability.text


def test_missing_protection_key_prevents_provider_call(db_session,shops,monkeypatch):
    monkeypatch.setattr(settings,'PAYMENT_CREDENTIALS_KEY',None)
    monkeypatch.setattr(PayPalService,'get_access_token',lambda *a,**kw:pytest.fail('Key missing before provider'))
    with pytest.raises(HTTPException) as rejected:Service.configure(db_session,shops['a'][0],body())
    assert rejected.value.status_code==503


def test_invalid_intent_payload_is_rejected_without_credential_fallback(db_session,shops):
    actor=shops['a'][0]
    Service.configure(db_session,actor,body())
    intent=type('Intent',(),{'tenant_id':actor.tenant_id,
        'merchant_key':Service.merchant('sandbox','merchant-a'),'payload':[]})()
    with pytest.raises(HTTPException) as rejected:
        Service.resolve(db_session,actor.tenant_id,intent)
    assert rejected.value.status_code==409


def test_same_app_cannot_be_assigned_to_two_companies(db_session,shops):
    Service.configure(db_session,shops['a'][0],body())
    with pytest.raises(HTTPException) as rejected:
        Service.configure(db_session,shops['b'][0],body())
    assert rejected.value.status_code==409
    assert db_session.query(Credential).count()==1


def test_platform_app_cannot_become_commercial_account(db_session,shops,monkeypatch):
    monkeypatch.setattr(settings,'PAYPAL_CLIENT_ID','merchant-a')
    with pytest.raises(HTTPException) as rejected:
        Service.configure(db_session,shops['a'][0],body())
    assert rejected.value.status_code==503
    assert db_session.query(Credential).count()==0


def test_wrong_key_cannot_hide_old_history_after_disable(db_session,shops,monkeypatch):
    actor=shops['a'][0]
    Service.configure(db_session,actor,body())
    Service.disable(db_session,actor,1)
    monkeypatch.setattr(settings,'PAYMENT_CREDENTIALS_KEY',SecretStr(secrets.token_urlsafe(32)))
    with pytest.raises(HTTPException) as rejected:
        Service.configure(db_session,actor,body('merchant-new','synthetic-new-secret',2))
    assert rejected.value.status_code==503
    assert Service.latest(db_session.connection(),actor.tenant_id)['revision']==2


def test_concurrent_admin_edits_only_accept_one_revision(committed_shop,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.models import User,Permission
    ids,factory=committed_shop;barrier=Barrier(2)
    with factory() as db:
        actor=db.get(User,ids[0]);permission=Permission(code='payments.configure',module='Pagos',method='PUT',path='/company/paypal')
        db.add(permission);actor.role.permissions.append(permission);db.commit()
    def oauth(*args,**kwargs):barrier.wait(timeout=15);return 'synthetic-token'
    monkeypatch.setattr(PayPalService,'get_access_token',oauth)
    def save(number):
        with factory() as db:
            try:Service.configure(db,db.get(User,ids[0]),body('merchant-'+str(number)));return 200
            except HTTPException as error:return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(save,[1,2]))
    assert sorted(result)==[200,409]
    with factory() as db:assert db.query(Credential).count()==1


def test_immutable_revision_cannot_be_rewritten(db_session,shops):
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError
    a=shops['a'][0];Service.configure(db_session,a,body());row=db_session.query(Credential).one();original=row.secret_ciphertext
    with pytest.raises(DBAPIError),db_session.begin_nested():
        db_session.execute(update(Credential).where(Credential.id==row.id).values(secret_ciphertext='plain-replacement'))
    db_session.expire_all();assert db_session.get(Credential,row.id).secret_ciphertext==original


def test_legacy_secret_cannot_be_overwritten_before_migration(db_session,shops):
    a=shops['a'][0];tenant=db_session.get(Tenant,a.tenant_id);tenant.paypal_client_id='legacy-app';tenant.paypal_client_secret='legacy-synthetic';db_session.commit()
    with pytest.raises(HTTPException) as rejected:Service.configure(db_session,a,body())
    assert rejected.value.status_code==409
    assert db_session.get(Tenant,a.tenant_id).paypal_client_secret=='legacy-synthetic'


@pytest.fixture
def actual_company_provider(request,monkeypatch):
    original=__import__('app.services.payment_flow_service',fromlist=['PaymentFlowService']).PaymentFlowService.credentials
    state=request.getfixturevalue('fake_paypal')
    from app.services.payment_flow_service import PaymentFlowService
    monkeypatch.setattr(PaymentFlowService,'credentials',staticmethod(original))
    return state


@pytest.mark.parametrize('purpose',['SALE','WALLET'])
def test_capture_after_rotation_and_disabling_uses_original_company(db_session,shops,monkeypatch,actual_company_provider,purpose):
    from app.services.payment_flow_service import PaymentFlowService as Flow
    from tests.support_payments import start_payment
    a,v,branches=shops['a'];Service.configure(db_session,a,body())
    captured=[];original=PayPalService.capture_order
    def capture(*args,**kwargs):captured.append(kwargs['client_id']);return original(*args,**kwargs)
    monkeypatch.setattr(PayPalService,'capture_order',capture)
    created=start_payment(db_session,a,v,branches[0],purpose=purpose)
    intent=db_session.query(PaymentIntent).filter_by(provider_order_id=created['order_id']).one()
    reference=intent.payload['paypal_credential_id']
    Service.configure(db_session,a,body('merchant-new','new-private-synthetic',1));Service.disable(db_session,a,2)
    result=Flow.capture(db_session,a,created['order_id'],purpose=purpose)
    replay=Flow.capture(db_session,a,created['order_id'],purpose=purpose)
    assert actual_company_provider['captures']==1 and captured==['merchant-a']
    assert replay==result and db_session.get(PaymentIntent,intent.id).payload['paypal_credential_id']==reference


def test_revoked_permission_during_oauth_does_not_commit_config(db_session,shops,monkeypatch):
    from app.models import Role
    a=shops['a'][0];role=db_session.query(Role).filter_by(name='cliente').one()
    def oauth(*args,**kwargs):a.role_id=role.id;db_session.commit();return 'synthetic-token'
    monkeypatch.setattr(PayPalService,'get_access_token',oauth)
    with pytest.raises(HTTPException) as rejected:Service.configure(db_session,a,body())
    assert rejected.value.status_code==403 and db_session.query(Credential).count()==0


def test_legacy_validation_errors_do_not_echo_secret_input(client,shops):
    response=client.put('/api/v1/subscriptions/branding',headers=headers(shops['a'][0]),json={'paypal_client_secret':['sensitive-synthetic-value']})
    assert response.status_code==422 and 'sensitive-synthetic-value' not in response.text
