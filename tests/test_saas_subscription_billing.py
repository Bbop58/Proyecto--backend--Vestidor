"""P3: central SaaS suite, without the historical commercial-license fixture."""
import uuid,copy
from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import select,text,update
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.models.subscription_payment import SubscriptionPayment as Payment,SubscriptionPaymentEvent as Event
from app.models.subscription_term import SubscriptionTerm as Term
from app.models.subscription_change_request import SubscriptionChangeRequest as ChangeRequest
from app.models.payment_intent import PaymentIntent
from app.models.tenant import Tenant
from app.services.subscription_billing_service import SubscriptionBillingService as Service
from app.services.subscription_contract_service import SubscriptionContractService as Contract
from app.services.paypal_service import PayPalService,ProviderError
from tests.test_saas_subscription_contracts import contract_setup,accept,decide,quote
from tests.support_saas import saas_actors,headers

@pytest.fixture(autouse=True)
def new_billing_quotes(contract_setup):
    contract_setup['legacy_manual_quotes']=False


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings,'PAYPAL_CLIENT_ID','synthetic-platform-p3')
    monkeypatch.setattr(settings,'PAYPAL_CLIENT_SECRET','private-synthetic-platform')
    monkeypatch.setattr(settings,'PAYPAL_MODE','sandbox')
    monkeypatch.setattr(settings,'PAYPAL_EXCHANGE_RATE',6.96)
    monkeypatch.setattr(settings,'PAYPAL_WEBHOOK_ID','synthetic-webhook')
    orders={};calls={'create':[],'capture':[],'refund':[],'verify':[]}
    def credentials(kwargs):
        assert kwargs['client_id']=='synthetic-platform-p3'
        assert kwargs['client_secret']=='private-synthetic-platform'
    def create(**kwargs):
        credentials(kwargs);calls['create'].append(kwargs['request_id'])
        oid='P3-'+kwargs['custom_id'];orders[oid]={'id':oid,'status':'APPROVED','purchase_units':[{
            'custom_id':kwargs['custom_id'],'payments':{'captures':[{'id':'CAP-'+kwargs['custom_id'],
            'status':'COMPLETED','create_time':Contract.now().isoformat(),
            'amount':{'currency_code':'USD','value':str(kwargs['amount_usd'])}}]}}]}
        return {'order_id':oid,'approve_url':'https://www.sandbox.paypal.com/checkoutnow?token='+oid}
    def details(oid,**kwargs):credentials(kwargs);return copy.deepcopy(orders[oid])
    def capture(oid,**kwargs):
        credentials(kwargs);calls['capture'].append(kwargs['request_id']);orders[oid]['status']='COMPLETED'
        return {'raw_response':copy.deepcopy(orders[oid])}
    def refund(cid,**kwargs):
        credentials(kwargs);calls['refund'].append(kwargs['request_id'])
        item=next(o for o in orders.values() if o['purchase_units'][0]['payments']['captures'][0]['id']==cid)
        return {'id':'REF-'+cid,'status':'COMPLETED','amount':item['purchase_units'][0]['payments']['captures'][0]['amount']}
    def verify(h,e,w,**kwargs):credentials(kwargs);calls['verify'].append(e['id']);return True
    monkeypatch.setattr(PayPalService,'create_order',create)
    monkeypatch.setattr(PayPalService,'get_order_details',details)
    monkeypatch.setattr(PayPalService,'capture_order',capture)
    monkeypatch.setattr(PayPalService,'refund_capture',refund)
    monkeypatch.setattr(PayPalService,'verify_webhook_signature',verify)
    return orders,calls


def approved(client,setup):
    row,_=accept(client,setup);reply=decide(client,setup,row)
    assert reply.status_code==200,reply.text
    assert reply.json()['payment_status']=='READY'
    return reply.json()['billing_payment_id'],row


def start(client,setup,payment_id):
    result=client.post(f'/api/v1/subscriptions/billing/{payment_id}/create',headers=headers(setup['admin']),json={})
    assert result.status_code==200,result.text
    return result.json()


def capture(client,setup,payment_id):
    return client.post(f'/api/v1/subscriptions/billing/{payment_id}/capture',headers=headers(setup['admin']),json={})

def test_billing_service_is_separate_from_commercial_payment_flow():
    assert Service is not None


def test_approval_has_no_term_and_payment_retries_apply_exactly_once(client,db_session,contract_setup,provider):
    setup=contract_setup;payment_id,_=approved(client,setup)
    assert db_session.query(Term).count()==1,[(str(t.tenant_id),t.origen,str(t.request_id)) for t in db_session.query(Term).all()]
    assert db_session.query(PaymentIntent).count()==0
    first=start(client,setup,payment_id);again=start(client,setup,payment_id)
    assert first==again and first['amount_bob']==100
    result=capture(client,setup,payment_id)
    assert result.status_code==200,result.text
    assert result.json()['state']=='APPLIED'
    assert capture(client,setup,payment_id).json()==result.json()
    assert len(provider[1]['create'])==1 and len(provider[1]['capture'])==1
    assert db_session.query(Term).count()==2 and db_session.query(PaymentIntent).count()==0
    paid=db_session.query(Term).filter_by(origen='PAYPAL').one()
    assert paid.fecha_inicio>Contract.now().date()  # renewal does not grant future benefits today
    rows=client.get('/api/v1/subscriptions/requests',headers=headers(setup['admin'])).json()
    assert rows[0]['payment_status']=='VERIFICADO'


@pytest.mark.parametrize('who',['other','customer','cashier','operator'])
def test_nonowner_or_nonmanager_cannot_initiate_or_capture(client,contract_setup,provider,who):
    pid,_=approved(client,contract_setup)
    for action in ('create','capture'):
        result=client.post(f'/api/v1/subscriptions/billing/{pid}/{action}',headers=headers(contract_setup[who]),json={})
        assert result.status_code in (403,404),result.text
    assert not provider[1]['create'] and not provider[1]['capture']


def test_rejected_or_pending_request_has_no_payment(client,contract_setup,provider):
    request,_=accept(client,contract_setup)
    for _ in range(2):
        assert client.get(f"/api/v1/subscriptions/requests/{request['id']}/payment",headers=headers(contract_setup['admin'])).status_code==409
        assert decide(client,contract_setup,request,decision='RECHAZADA').status_code in (200,409)
    assert not provider[1]['create']


def test_client_cannot_override_price_or_tenant(client,contract_setup,provider):
    pid,_=approved(client,contract_setup)
    result=client.post(f'/api/v1/subscriptions/billing/{pid}/create',headers=headers(contract_setup['admin']),
        json={'amount_bob':1,'tenant_id':str(contract_setup['other'].tenant_id)})
    assert result.status_code==422 and not provider[1]['create']


def test_capture_timeout_is_recovered_without_new_charge(client,db_session,contract_setup,provider,monkeypatch):
    pid,_=approved(client,contract_setup);started=start(client,contract_setup,pid)
    original=PayPalService.capture_order
    def ambiguous(*args,**kwargs):original(*args,**kwargs);raise ProviderError(504,'synthetic timeout')
    monkeypatch.setattr(PayPalService,'capture_order',ambiguous)
    assert capture(client,contract_setup,pid).status_code==504
    assert db_session.get(Payment,pid).state=='CAPTURING'
    assert capture(client,contract_setup,pid).json()['state']=='APPLIED'
    assert len(provider[1]['capture'])==1 and db_session.query(Term).count()==2


def test_expired_authorization_cannot_initiate_charge(client,contract_setup,provider,monkeypatch):
    pid,_=approved(client,contract_setup);start(client,contract_setup,pid)
    now=Contract.now();monkeypatch.setattr(Contract,'now',staticmethod(lambda:now+timedelta(minutes=31)))
    assert capture(client,contract_setup,pid).status_code==409
    assert not provider[1]['capture']


def test_completed_payment_reconciles_after_expiry_using_provider_time(client,db_session,contract_setup,provider,monkeypatch):
    pid,_=approved(client,contract_setup);row=start(client,contract_setup,pid)
    provider[0][row['provider_order_id']]['status']='COMPLETED'
    now=Contract.now();monkeypatch.setattr(Contract,'now',staticmethod(lambda:now+timedelta(minutes=31)))
    assert capture(client,contract_setup,pid).json()['state']=='APPLIED'
    assert not provider[1]['capture'] and db_session.query(Term).count()==2


def test_late_capture_is_refunded_not_converted_to_license(client,db_session,contract_setup,provider):
    pid,_=approved(client,contract_setup);row=start(client,contract_setup,pid)
    order=provider[0][row['provider_order_id']];order['status']='COMPLETED'
    order['purchase_units'][0]['payments']['captures'][0]['create_time']=(Contract.now()+timedelta(minutes=32)).isoformat()
    result=capture(client,contract_setup,pid)
    assert result.status_code==200,result.text
    assert result.json()['state']=='REFUNDED' and db_session.query(Term).count()==1
    assert len(provider[1]['refund'])==1
    assert capture(client,contract_setup,pid).json()['state']=='REFUNDED'
    assert len(provider[1]['refund'])==1


def test_pending_refund_survives_ambiguous_timeout(client,db_session,contract_setup,provider,monkeypatch):
    pid,_=approved(client,contract_setup);row=start(client,contract_setup,pid)
    provider[0][row['provider_order_id']]['status']='COMPLETED'
    db_session.execute(update(Tenant).where(Tenant.id==contract_setup['admin'].tenant_id).values(activo=False));db_session.commit()
    original=PayPalService.refund_capture
    monkeypatch.setattr(PayPalService,'refund_capture',lambda *a,**kw:(_ for _ in ()).throw(ProviderError(504,'Synthetic refund timeout')))
    assert capture(client,contract_setup,pid).status_code==504
    assert db_session.get(Payment,pid).state=='REFUND_PENDING' and db_session.query(Term).count()==1
    monkeypatch.setattr(PayPalService,'refund_capture',original)
    assert capture(client,contract_setup,pid).json()['state']=='REFUNDED'


@pytest.mark.parametrize('damage',['amount','identity','currency'])
def test_discordant_capture_is_quarantined(client,db_session,contract_setup,provider,damage):
    pid,_=approved(client,contract_setup);row=start(client,contract_setup,pid);order=provider[0][row['provider_order_id']]
    order['status']='COMPLETED';unit=order['purchase_units'][0]
    if damage=='identity':unit['custom_id']=str(uuid.uuid4())
    else:unit['payments']['captures'][0]['amount']['value' if damage=='amount' else 'currency_code']='0.01' if damage=='amount' else 'BOB'
    assert capture(client,contract_setup,pid).status_code==409
    assert db_session.get(Payment,pid).state=='REVIEW' and db_session.query(Term).count()==1


def test_platform_rotation_does_not_redirect_old_order(client,contract_setup,provider,monkeypatch):
    pid,_=approved(client,contract_setup);start(client,contract_setup,pid)
    monkeypatch.setattr(settings,'PAYPAL_CLIENT_ID','new-platform-app')
    assert capture(client,contract_setup,pid).status_code==409
    assert not provider[1]['capture']


def event_for(row):
    return {'id':'EVENT-'+str(uuid.uuid4()),'event_type':'PAYMENT.CAPTURE.COMPLETED',
        'resource':{'supplementary_data':{'related_ids':{'order_id':row['provider_order_id']}}}}


def test_verified_webhook_reconciles_and_deduplicates_without_new_charge(client,db_session,contract_setup,provider):
    pid,_=approved(client,contract_setup);row=start(client,contract_setup,pid)
    provider[0][row['provider_order_id']]['status']='COMPLETED';event=event_for(row)
    for _ in range(2):
        result=client.post('/api/v1/subscriptions/paypal/webhook',json=event)
        assert result.status_code==200,result.text
    assert db_session.get(Payment,pid).state=='APPLIED' and db_session.query(Term).count()==2
    assert db_session.query(Event).count()==1 and not provider[1]['capture']
    altered={**event,'summary':'different'}
    assert client.post('/api/v1/subscriptions/paypal/webhook',json=altered).status_code==409


def test_unverified_event_never_changes_billing(client,db_session,contract_setup,provider,monkeypatch):
    pid,_=approved(client,contract_setup);row=start(client,contract_setup,pid)
    def reject(*a,**kw):raise HTTPException(400,'Signature rejected')
    monkeypatch.setattr(PayPalService,'verify_webhook_signature',reject)
    assert client.post('/api/v1/subscriptions/paypal/webhook',json=event_for(row)).status_code==400
    assert db_session.query(Event).count()==0 and db_session.get(Payment,pid).state=='CREATED'


def test_migration_repeated_does_not_attribute_legacy_payment(db_session,contract_setup):
    from scripts.migrate_subscription_billing import migrate_connection
    for _ in range(2):assert migrate_connection(db_session.connection())['historical_payments_attributed']==0
    assert db_session.query(Payment).count()==0 and db_session.query(Term).one().origen=='REFERENCIA_LEGADA'


def test_backup_cannot_revert_financial_state(client,db_session,contract_setup,provider):
    from tests.test_saas_backup_contracts import snapshot
    from app.services.backup_service import BackupService
    pid,_=approved(client,contract_setup);saved=snapshot(db_session,contract_setup['admin'])
    assert saved['version']=='4.6.0' and len(saved['data']['reference_subscription_payments'])==1
    start(client,contract_setup,pid)
    from app.core.tenant_scope import bind_tenant
    bind_tenant(db_session,contract_setup['admin'].tenant_id,contract_setup['admin'].id)
    with pytest.raises(HTTPException) as rejected:BackupService.restore_backup(db_session,saved,contract_setup['admin'])
    assert rejected.value.status_code==409
    assert db_session.get(Payment,pid).state=='CREATED'


def test_billing_amount_is_immutable_and_term_cannot_bypass_capture(client,db_session,contract_setup,provider):
    pid,requested=approved(client,contract_setup)
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(update(Payment).where(Payment.id==uuid.UUID(pid)).values(amount_bob=1))
    request=db_session.get(ChangeRequest,requested['id']);payload=request.quote_payload;now=Contract.now()
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(Term.__table__.insert().values(tenant_id=request.tenant_id,subscription_id=request.subscription_id,
            version_id=request.version_id,request_id=request.id,fecha_inicio=payload['schedule']['fecha_inicio'],
            fecha_fin=payload['schedule']['fecha_fin'],anchor_day=payload['schedule']['anchor_day'],precio_mensual_bob=100,
            moneda='BOB',periodicidad='MENSUAL',max_sucursales=payload['conditions']['max_sucursales'],features=payload['conditions']['features'],
            origen='PAYPAL',accepted_by=request.accepted_by,accepted_at=request.accepted_at,approved_by=request.decided_by,approved_at=now))


def test_refund_webhook_restricts_paid_term_without_erasing_history(client,db_session,contract_setup,provider):
    from app.services.entitlement_service import EntitlementService
    pid,_=approved(client,contract_setup);row=start(client,contract_setup,pid)
    assert capture(client,contract_setup,pid).json()['state']=='APPLIED'
    term=db_session.query(Term).filter_by(origen='PAYPAL').one();actor=contract_setup['admin']
    assert EntitlementService.resolve(db_session,actor,at=term.fecha_inicio)['active']
    order=provider[0][row['provider_order_id']];order['purchase_units'][0]['payments']['captures'][0]['status']='REFUNDED'
    event=event_for(row);event['event_type']='PAYMENT.CAPTURE.REFUNDED'
    result=client.post('/api/v1/subscriptions/paypal/webhook',json=event)
    assert result.status_code==200,result.text
    db_session.expire_all()
    assert db_session.get(Payment,pid).state=='REVIEW'
    assert not EntitlementService.resolve(db_session,actor,at=term.fecha_inicio)['active']
    assert db_session.query(Term).filter_by(origen='PAYPAL').count()==1


def test_permission_revoked_during_provider_read_prevents_new_capture(client,db_session,contract_setup,provider,monkeypatch):
    from app.models import User,Role
    pid,_=approved(client,contract_setup);start(client,contract_setup,pid)
    original=PayPalService.get_order_details
    customer=db_session.query(Role).filter_by(name='cliente').one().id
    def revoke(*a,**kw):
        result=original(*a,**kw)
        db_session.connection().execute(update(User).where(User.id==contract_setup['admin'].id).values(role_id=customer))
        return result
    monkeypatch.setattr(PayPalService,'get_order_details',revoke)
    assert capture(client,contract_setup,pid).status_code==403
    assert not provider[1]['capture'] and db_session.query(Term).filter_by(origen='PAYPAL').count()==0


def test_real_transport_verifies_signature_instead_of_trusting_event(monkeypatch):
    # Call the real verification method, not the provider fixture's double.
    observed=[]
    monkeypatch.setattr(PayPalService,'_headers',lambda *a,**kw:{})
    monkeypatch.setattr(PayPalService,'_request',lambda *a,**kw:observed.append((a,kw)) or {'verification_status':'FAILURE'})
    headers_={key:'synthetic' for key in ['paypal-auth-algo','paypal-cert-url','paypal-transmission-id','paypal-transmission-sig','paypal-transmission-time']}
    with pytest.raises(HTTPException) as rejected:PayPalService.verify_webhook_signature(headers_,{'id':'EVENT'},'WEBHOOK','cid','secret','sandbox')
    assert rejected.value.status_code==400
    assert observed[0][0][0].endswith('/v1/notifications/verify-webhook-signature')


def test_zero_price_is_exempt_without_a_provider_charge(client,db_session,contract_setup,provider):
    from tests.test_saas_plan_catalog import draft,transition
    setup=contract_setup
    version=draft(client,setup['operator'],str(setup['plan'].id),precio_mensual_bob='0.00')
    assert transition(client,setup['operator'],version,'publish').status_code==200
    requested,_=accept(client,setup);result=decide(client,setup,requested)
    assert result.status_code==200,result.text
    assert result.json()['payment_status']=='SIN_CARGO'
    assert db_session.query(Payment).count()==0 and not provider[1]['create']
    assert db_session.query(Term).filter_by(origen='MANUAL').one().precio_mensual_bob==0


def test_missing_platform_credentials_roll_back_approval(client,db_session,contract_setup,provider,monkeypatch):
    requested,_=accept(client,contract_setup)
    monkeypatch.setattr(settings,'PAYPAL_CLIENT_SECRET','')
    result=decide(client,contract_setup,requested)
    assert result.status_code==503
    db_session.expire_all()
    assert db_session.get(ChangeRequest,requested['id']).estado=='PENDIENTE' and db_session.query(Payment).count()==0


def test_fx_change_does_not_change_accepted_payment_amount(client,contract_setup,provider,monkeypatch):
    pid,_=approved(client,contract_setup)
    monkeypatch.setattr(settings,'PAYPAL_EXCHANGE_RATE',20)
    row=start(client,contract_setup,pid)
    assert row['exchange_rate']==6.96 and row['amount_usd']==14.37


def test_platform_cannot_reuse_an_enterprise_application(client,db_session,contract_setup,provider,monkeypatch):
    import secrets
    from pydantic import SecretStr
    from app.services.company_paypal_service import CompanyPayPalService
    monkeypatch.setattr(settings,'PAYMENT_CREDENTIALS_KEY',SecretStr(secrets.token_urlsafe(32)))
    monkeypatch.setattr(settings,'PAYPAL_CLIENT_ID','different-platform-during-company-provision')
    CompanyPayPalService.append(db_session.connection(),contract_setup['admin'].tenant_id,client_id='synthetic-platform-p3',secret='synthetic-company',mode='sandbox')
    db_session.commit()
    monkeypatch.setattr(settings,'PAYPAL_CLIENT_ID','synthetic-platform-p3')
    requested,_=accept(client,contract_setup)
    assert decide(client,contract_setup,requested).status_code==503 and db_session.query(Payment).count()==0


def test_pending_provider_capture_is_recoverable_without_another_charge(client,db_session,contract_setup,provider):
    pid,_=approved(client,contract_setup);row=start(client,contract_setup,pid)
    order=provider[0][row['provider_order_id']];order['status']='COMPLETED'
    order['purchase_units'][0]['payments']['captures'][0]['status']='PENDING'
    assert capture(client,contract_setup,pid).status_code==409
    assert db_session.get(Payment,pid).state=='CAPTURING' and not provider[1]['capture']
    order['purchase_units'][0]['payments']['captures'][0]['status']='COMPLETED'
    assert capture(client,contract_setup,pid).json()['state']=='APPLIED'
    assert not provider[1]['capture']


def test_pending_refund_uses_its_existing_identity(client,db_session,contract_setup,provider,monkeypatch):
    pid,_=approved(client,contract_setup);row=start(client,contract_setup,pid)
    provider[0][row['provider_order_id']]['status']='COMPLETED'
    db_session.connection().execute(update(Tenant).where(Tenant.id==contract_setup['admin'].tenant_id).values(activo=False));db_session.commit()
    original=PayPalService.refund_capture
    final={}
    def pending(*a,**kw):
        data=original(*a,**kw);final.update(data);return {**data,'status':'PENDING'}
    monkeypatch.setattr(PayPalService,'refund_capture',pending)
    assert capture(client,contract_setup,pid).json()['state']=='REFUND_PENDING'
    monkeypatch.setattr(PayPalService,'get_refund_details',lambda *a,**kw:dict(final))
    assert capture(client,contract_setup,pid).json()['state']=='REFUNDED'
    assert len(provider[1]['refund'])==1


def test_crash_after_durable_capture_retries_only_local_application(client,db_session,contract_setup,provider,monkeypatch):
    from sqlalchemy.engine import Connection
    from sqlalchemy.sql.dml import Insert
    pid,_=approved(client,contract_setup);start(client,contract_setup,pid)
    original=Connection.execute
    def fail_term(connection,statement,*args,**kwargs):
        if isinstance(statement,Insert) and statement.table.name=='subscription_terms':
            raise RuntimeError('synthetic failure applying captured subscription')
        return original(connection,statement,*args,**kwargs)
    monkeypatch.setattr(Connection,'execute',fail_term)
    with pytest.raises(RuntimeError,match='synthetic failure'):capture(client,contract_setup,pid)
    assert db_session.get(Payment,pid).state=='CAPTURED'
    assert db_session.query(Term).filter_by(origen='PAYPAL').count()==0
    monkeypatch.setattr(Connection,'execute',original)
    assert capture(client,contract_setup,pid).json()['state']=='APPLIED'
    assert len(provider[1]['capture'])==1


def test_private_schema_creation_never_alters_public_contract_table():
    from tests.conftest import test_engine
    from app.database import Base
    schema='billing_scope_'+uuid.uuid4().hex
    try:
        with test_engine.begin() as lock:
            # Compatible with fixture inserts, incompatible with accidental ALTER.
            lock.execute(text('LOCK TABLE public.subscription_terms IN SHARE UPDATE EXCLUSIVE MODE'))
            with test_engine.begin() as connection:
                connection.execute(text('CREATE SCHEMA '+schema))
                connection.execute(text("SET LOCAL lock_timeout='2s'"))
                Base.metadata.create_all(connection.execution_options(schema_translate_map={None:schema}))
    finally:
        with test_engine.begin() as connection:connection.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))


def test_one_capture_cannot_also_fund_a_commercial_operation(client,db_session,contract_setup,provider):
    from app.core.payment_evidence import capture_claimed_elsewhere
    pid,_=approved(client,contract_setup);row=start(client,contract_setup,pid)
    order=provider[0][row['provider_order_id']];order['status']='COMPLETED'
    cid=order['purchase_units'][0]['payments']['captures'][0]['id']
    other=PaymentIntent(tenant_id=contract_setup['admin'].tenant_id,user_id=contract_setup['admin'].id,purpose='WALLET',state='CAPTURED',
        capture_id=cid,amount_bob=100,amount_usd=row['amount_usd'],exchange_rate=row['exchange_rate'],merchant_key='a'*64,provider_mode='sandbox')
    db_session.add(other);db_session.commit()
    assert capture(client,contract_setup,pid).status_code==409
    assert db_session.get(Payment,pid).state=='REVIEW' and db_session.query(Term).filter_by(origen='PAYPAL').count()==0
    assert capture_claimed_elsewhere(db_session,cid,billing_id=uuid.UUID(pid))


def test_billing_capture_is_unavailable_to_another_commercial_intent(client,db_session,contract_setup,provider):
    from app.core.payment_evidence import capture_claimed_elsewhere
    pid,_=approved(client,contract_setup);start(client,contract_setup,pid)
    completed=capture(client,contract_setup,pid).json()
    assert completed['state']=='APPLIED'
    assert capture_claimed_elsewhere(db_session,completed['capture_id'],commercial_id=uuid.uuid4())


def test_two_accepted_requests_cannot_authorize_duplicate_pending_periods(client,db_session,contract_setup,provider):
    approved(client,contract_setup)
    another,_=accept(client,contract_setup)
    result=decide(client,contract_setup,another)
    assert result.status_code==409
    db_session.expire_all()
    assert db_session.get(ChangeRequest,another['id']).estado=='PENDIENTE'
    assert db_session.query(Payment).count()==1 and not provider[1]['create']


@pytest.mark.parametrize('contract_setup',['expired'],indirect=True)
@pytest.mark.parametrize('expired_status',['ACTIVA','VENCIDA'])
def test_paid_current_period_refreshes_profile_access(client,db_session,contract_setup,provider,expired_status):
    from app.models import Subscription
    setup=contract_setup
    db_session.connection().execute(update(Subscription).where(Subscription.id==setup['sub'].id).values(estado=expired_status));db_session.commit()
    before=client.get('/api/v1/users/me',headers=headers(setup['admin']))
    assert before.status_code==200 and before.json()['service_restricted'] is True
    pid,_=approved(client,setup);start(client,setup,pid)
    assert capture(client,setup,pid).json()['state']=='APPLIED'
    after=client.get('/api/v1/users/me',headers=headers(setup['admin']))
    assert after.status_code==200 and after.json()['service_restricted'] is False
    assert after.json()['tenant_id']==before.json()['tenant_id']
