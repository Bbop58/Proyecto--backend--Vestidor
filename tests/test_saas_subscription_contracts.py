import uuid
import pytest
from datetime import date,timedelta
from datetime import datetime,timezone
from decimal import Decimal
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from app.models.subscription_plan import SubscriptionPlan
from app.models.subscription import Subscription
from app.core.plan_catalog_ddl import ensure_legacy_versions
from app.core.subscription_contract_ddl import ensure_legacy_terms
from app.models.subscription_term import SubscriptionTerm as Term
from app.models.subscription_change_request import SubscriptionChangeRequest as Request
from app.models.branch import Branch
from app.models.subscription_plan_version import SubscriptionPlanVersion as Version
from app.services.subscription_contract_service import SubscriptionContractService as Service
from app.services.platform_audit_service import PlatformAuditService
from app.core.billing_calendar import next_billing_date,business_today
from tests.test_saas_plan_catalog import draft as catalog_draft,transition
from tests.support_saas import saas_actors,headers


@pytest.fixture
def contract_setup(db_session,saas_actors,request):
    plan=SubscriptionPlan(codigo='CONTRACT',nombre='Contract',precio_mensual_bob=100,
        max_sucursales=4,permite_offline=True,permite_reportes_ia=True)
    db_session.add(plan);db_session.flush();ensure_legacy_versions(db_session.connection())
    sub=Subscription(tenant_id=saas_actors['admin'].tenant_id,plan_id=plan.id,
        fecha_inicio=business_today()-timedelta(days=3),fecha_vencimiento=business_today()+timedelta(days=-1 if getattr(request,'param',None)=='expired' else 15))
    db_session.add(sub);db_session.commit()
    ensure_legacy_terms(db_session.connection());db_session.commit()
    # S3/S4 regression fixtures represent accepted pre-P3 manual quotations.
    # New billing suites explicitly turn this marker off and use the actual API token.
    return {**saas_actors,'sub':sub,'plan':plan,'legacy_manual_quotes':True}


def test_old_client_cannot_self_activate(client,contract_setup):
    actor=contract_setup['admin'];sub=contract_setup['sub'];before=sub.fecha_vencimiento
    result=client.post('/api/v1/subscriptions/change-plan',headers=headers(actor),json={'plan_codigo':'CONTRACT'})
    assert result.status_code==409,result.text
    assert sub.fecha_vencimiento==before


def test_onboarding_disabled_without_verified_channel(client,db_session):
    from app.models.tenant import Tenant
    before=db_session.query(Tenant).count()
    result=client.post('/api/v1/subscriptions/onboarding',json={'nombre':'New','razon_social':'New',
        'nit_rut':'1234','slug':'new','admin_email':'new@example.com','admin_password':'OnlySynthetic123!'})
    assert result.status_code==503,result.text
    assert db_session.query(Tenant).count()==before


def quote(client,setup,actor=None,kind='RENOVACION'):
    version=next(row for row in client.get('/api/v1/subscriptions/plans').json() if row['codigo']=='CONTRACT')['version_id']
    result=client.post('/api/v1/subscriptions/quote',headers=headers(actor or setup['admin']),json={'version_id':version,'kind':kind})
    assert result.status_code==200,result.text
    quoted=result.json()
    if setup.get('legacy_manual_quotes'):
        quoted=legacy_quote(quoted)
    return quoted


def legacy_quote(quoted):
    """Reconstruct a signed pre-P3 offer; never used by application code."""
    import jwt
    payload=jwt.decode(quoted['quote_token'],options={'verify_signature':False})
    payload.pop('billing_mode',None)
    return {**quoted,'quote_token':jwt.encode(payload,Service._key(),algorithm='HS256')}


def accept(client,setup,quoted=None,key=None):
    quoted=quoted or quote(client,setup)
    if setup.get('legacy_manual_quotes'):quoted=legacy_quote(quoted)
    body={'quote_token':quoted['quote_token'],'acceptance':True,'idempotency_key':str(key or uuid.uuid4())}
    result=client.post('/api/v1/subscriptions/requests',headers=headers(setup['admin']),json=body)
    assert result.status_code==201,result.text
    return result.json(),body


def decide(client,setup,request,decision='APROBADA',key=None,expected=0):
    return client.post(f"/api/v1/platform/subscription-requests/{request['id']}/decision",headers=headers(setup['operator']),
        json={'decision':decision,'expected_version':expected,'idempotency_key':str(key or uuid.uuid4()),'reason':'Synthetic manual decision'})


@pytest.mark.parametrize('start,anchor,expected',[
    (date(2027,1,31),31,date(2027,2,28)),(date(2027,2,28),31,date(2027,3,31)),
    (date(2028,1,31),31,date(2028,2,29)),(date(2027,12,31),31,date(2028,1,31)),
])
def test_month_end_anchor(start,anchor,expected):assert next_billing_date(start,anchor)==expected


def test_acceptance_is_pending_and_approval_retry_creates_one_term(client,db_session,contract_setup):
    setup=contract_setup;sub=setup['sub'];before=(sub.plan_id,sub.fecha_inicio,sub.fecha_vencimiento)
    request,body=accept(client,setup)
    assert request['estado']=='PENDIENTE' and db_session.query(Term).count()==1
    replay=client.post('/api/v1/subscriptions/requests',headers=headers(setup['admin']),json=body)
    assert replay.status_code==201 and replay.json()==request
    key=uuid.uuid4();approved=decide(client,setup,request,key=key)
    assert approved.status_code==200,approved.text
    repeated=decide(client,setup,request,key=key)
    assert repeated.status_code==200 and repeated.json()==approved.json()
    assert db_session.query(Term).count()==2
    db_session.expire_all();assert (sub.plan_id,sub.fecha_inicio,sub.fecha_vencimiento)==before
    assert approved.json()['payment_status']=='NO_VERIFICADO'
    own=client.get('/api/v1/subscriptions/contract',headers=headers(setup['admin']))
    assert own.status_code==200 and own.json()['current_term']['origen']=='REFERENCIA_LEGADA'
    assert own.json()['current_term']['precio_mensual_bob'] is None


@pytest.mark.parametrize('contract_setup',['expired'],indirect=True)
def test_price_100_to_150_keeps_current_term_and_requires_new_acceptance(client,db_session,contract_setup):
    setup=contract_setup;first,_=accept(client,setup)
    assert decide(client,setup,first).status_code==200
    new=catalog_draft(client,setup['operator'],str(setup['plan'].id),precio_mensual_bob='150.00',max_sucursales=4)
    assert transition(client,setup['operator'],new,'publish').status_code==200
    request,_=accept(client,setup)
    assert request['conditions']['precio_mensual_bob']=='150.00'
    assert decide(client,setup,request).status_code==200
    current=client.get('/api/v1/subscriptions/contract',headers=headers(setup['admin'])).json()
    assert current['current_term']['precio_mensual_bob']==100
    future=[r for r in current['terms'] if r['origen']=='MANUAL' and r['id']!=current['current_term']['id']]
    assert len(future)==1 and future[0]['precio_mensual_bob']==150


def test_issued_quote_keeps_old_price_when_another_revision_is_published(client,contract_setup):
    setup=contract_setup;quoted=quote(client,setup)
    new=catalog_draft(client,setup['operator'],str(setup['plan'].id),precio_mensual_bob='150.00',max_sucursales=4)
    assert transition(client,setup['operator'],new,'publish').status_code==200
    request,_=accept(client,setup,quoted)
    assert decide(client,setup,request).status_code==200
    assert request['conditions']['precio_mensual_bob']=='100.00'


@pytest.mark.parametrize('decision',['RECHAZADA','CANCELADA'])
def test_rejection_or_cancel_never_extends_terms(client,db_session,contract_setup,decision):
    setup=contract_setup;request,_=accept(client,setup)
    if decision=='CANCELADA':
        result=client.post(f"/api/v1/subscriptions/requests/{request['id']}/cancel",headers=headers(setup['admin']),
            json={'expected_version':0,'reason':'Synthetic cancellation'})
    else:result=decide(client,setup,request,decision)
    assert result.status_code==200,result.text
    assert result.json()['estado']==decision and db_session.query(Term).count()==1
    assert decide(client,setup,request).status_code==409


def test_wrong_identity_signature_or_no_acceptance_is_rejected(client,contract_setup):
    setup=contract_setup;quoted=quote(client,setup)
    body={'quote_token':quoted['quote_token'],'acceptance':True,'idempotency_key':str(uuid.uuid4())}
    assert client.post('/api/v1/subscriptions/requests',headers=headers(setup['other']),json=body).status_code==403
    parts=body['quote_token'].split('.');parts[-1]=('a' if parts[-1][0]!='a' else 'b')+parts[-1][1:]
    assert client.post('/api/v1/subscriptions/requests',headers=headers(setup['admin']),json={**body,'quote_token':'.'.join(parts)}).status_code==400
    assert client.post('/api/v1/subscriptions/requests',headers=headers(setup['admin']),json={**body,'acceptance':False}).status_code==422
    assert client.post('/api/v1/subscriptions/requests',headers=headers(setup['admin']),json={**body,'tenant_id':str(setup['b_company'].id)}).status_code==422


def test_expired_or_retired_offer_requires_new_acceptance(client,contract_setup,monkeypatch):
    setup=contract_setup;request,_=accept(client,setup)
    now=Service.now();monkeypatch.setattr(Service,'now',staticmethod(lambda:now+timedelta(minutes=16)))
    assert decide(client,setup,request).status_code==409
    monkeypatch.setattr(Service,'now',staticmethod(lambda:now))
    version=client.get(f"/api/v1/platform/plan-versions/{request['version_id']}",headers=headers(setup['operator'])).json()
    assert transition(client,setup['operator'],version,'retire').status_code==200
    assert decide(client,setup,request).status_code==409


def test_downgrade_preserves_branches_and_rejects_excess(client,db_session,contract_setup):
    setup=contract_setup
    for i in range(3):db_session.add(Branch(tenant_id=setup['admin'].tenant_id,nombre=f'Synthetic {i}',ciudad='SC',direccion='Test',activa=True))
    db_session.commit()
    version=catalog_draft(client,setup['operator'],str(setup['plan'].id),max_sucursales=2)
    assert transition(client,setup['operator'],version,'publish').status_code==200
    request,_=accept(client,setup)
    assert decide(client,setup,request).status_code==409
    assert db_session.query(Branch).count()==3 and db_session.query(Term).count()==1


def test_audit_failure_rolls_back_approval(client,db_session,contract_setup,monkeypatch):
    setup=contract_setup;request,_=accept(client,setup)
    def fail(*args,**kwargs):raise HTTPException(503,'Synthetic audit failure')
    monkeypatch.setattr(PlatformAuditService,'record',fail)
    assert decide(client,setup,request).status_code==503
    assert db_session.query(Term).count()==1
    assert db_session.get(Request,request['id']).estado=='PENDIENTE'


@pytest.mark.parametrize('who',['customer','cashier','operator'])
def test_role_cannot_manage_company_contract(client,contract_setup,who):
    setup=contract_setup
    assert client.get('/api/v1/subscriptions/contract',headers=headers(setup[who])).status_code==403


def test_direct_sql_cannot_extend_or_overwrite_legacy_history(db_session,contract_setup):
    sub=contract_setup['sub']
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(Subscription.__table__.update().where(Subscription.id==sub.id).values(fecha_vencimiento=date.today()+timedelta(days=365)))


def test_request_and_decision_keys_reject_different_payload(client,contract_setup):
    setup=contract_setup;requested,body=accept(client,setup)
    another=quote(client,setup)
    assert client.post('/api/v1/subscriptions/requests',headers=headers(setup['admin']),json={**body,'quote_token':another['quote_token']}).status_code==409
    key=uuid.uuid4();assert decide(client,setup,requested,key=key).status_code==200
    assert decide(client,setup,requested,decision='RECHAZADA',key=key).status_code==409


def test_manager_can_only_see_and_cancel_own_company(client,contract_setup):
    setup=contract_setup;requested,_=accept(client,setup)
    result=client.get('/api/v1/subscriptions/requests',headers=headers(setup['other']))
    assert result.status_code==200 and result.json()==[]
    assert client.post(f"/api/v1/subscriptions/requests/{requested['id']}/cancel",headers=headers(setup['other']),
        json={'expected_version':0,'reason':'Attempt cross-company cancel'}).status_code==404
    assert client.post(f"/api/v1/platform/subscription-requests/{requested['id']}/decision",headers=headers(setup['admin']),
        json={'decision':'APROBADA','expected_version':0,'idempotency_key':str(uuid.uuid4()),'reason':'Attempt self approval'}).status_code==403


def test_expired_quote_and_session_token_cannot_request_contract(client,contract_setup):
    import jwt
    setup=contract_setup;quoted=quote(client,setup)
    payload=jwt.decode(quoted['quote_token'],options={'verify_signature':False})
    payload['exp']=int((Service.now()-timedelta(seconds=1)).timestamp())
    payload['iat']=payload['exp']-900
    expired=jwt.encode(payload,Service._key(),algorithm='HS256')
    body={'quote_token':expired,'acceptance':True,'idempotency_key':str(uuid.uuid4())}
    assert client.post('/api/v1/subscriptions/requests',headers=headers(setup['admin']),json=body).status_code==409
    session=headers(setup['admin'])['Authorization'].split(' ',1)[1]
    assert client.post('/api/v1/subscriptions/requests',headers=headers(setup['admin']),json={**body,'quote_token':session}).status_code==400


def test_future_downgrade_preserves_current_branch_entitlement(client,db_session,contract_setup):
    setup=contract_setup
    new=catalog_draft(client,setup['operator'],str(setup['plan'].id),max_sucursales=2)
    assert transition(client,setup['operator'],new,'publish').status_code==200
    requested,_=accept(client,setup);assert decide(client,setup,requested).status_code==200
    for i in range(3):
        result=client.post('/api/v1/sucursales',headers=headers(setup['admin']),json={
            'nombre':f'Current contract branch {i}','ciudad':'SC','direccion':'Test','activa':True})
        assert result.status_code==201,result.text
    assert db_session.query(Branch).count()==3


def test_migration_and_seed_repeat_preserve_unknown_legacy_price(db_session,contract_setup):
    from pathlib import Path
    setup=contract_setup;connection=db_session.connection()
    before=[dict(r) for r in connection.execute(select(Term.__table__)).mappings()]
    sql=(Path(__file__).parents[1]/'migrations/saas_03_subscription_terms.sql').read_text(encoding='utf-8')
    connection.exec_driver_sql(sql);connection.exec_driver_sql(sql)
    ensure_legacy_terms(connection)
    assert [dict(r) for r in connection.execute(select(Term.__table__)).mappings()]==before
    assert before[0]['precio_mensual_bob'] is None and before[0]['accepted_at'] is None
    assert before[0]['fecha_fin']==setup['sub'].fecha_vencimiento+timedelta(days=1)


def test_contract_snapshots_are_references_not_restore_targets(client,db_session,contract_setup):
    import json
    from app.core.tenant_scope import bind_tenant
    from app.services.backup_service import BackupService,json_serializer
    setup=contract_setup;requested,_=accept(client,setup)
    bind_tenant(db_session,setup['admin'].tenant_id,setup['admin'].id)
    backup=json.loads(json.dumps(BackupService.create_backup(db_session,setup['admin']),default=json_serializer))
    assert 'subscription_terms' not in backup['data'] and 'reference_subscription_terms' in backup['data']
    BackupService.restore_backup(db_session,backup,setup['admin'])
    assert db_session.get(Request,requested['id']).estado=='PENDIENTE'
    db_session.info.pop('tenant_scope',None);db_session.info.pop('actor_id',None)
    assert decide(client,setup,requested).status_code==200
    bind_tenant(db_session,setup['admin'].tenant_id,setup['admin'].id)
    with pytest.raises(HTTPException) as error:BackupService.restore_backup(db_session,backup,setup['admin'])
    assert error.value.status_code==409


def test_legacy_term_and_accepted_request_are_immutable_in_sql(db_session,contract_setup,client):
    setup=contract_setup;requested,_=accept(client,setup)
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(Term.__table__.update().where(Term.tenant_id==setup['admin'].tenant_id).values(max_sucursales=100))
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(Request.__table__.update().where(Request.id==requested['id']).values(accepted_by=setup['other'].id))


def test_existing_suspended_manager_can_renew_without_reactivating_business(client,db_session,contract_setup):
    from app.models.tenant import Tenant
    setup=contract_setup;actor=setup['admin'];tenant_id=actor.tenant_id
    db_session.connection().execute(Tenant.__table__.update().where(Tenant.id==tenant_id).values(activo=False))
    db_session.commit();db_session.expire_all()
    requested,_=accept(client,setup)
    assert decide(client,setup,requested).status_code==200
    assert db_session.get(Tenant,tenant_id).activo is False
    assert client.get('/api/v1/ventas',headers=headers(actor)).status_code==403


def test_no_subscription_manager_can_request_alta_without_self_activation(client,db_session,saas_actors):
    setup=saas_actors
    plan=SubscriptionPlan(codigo='CONTRACT',nombre='New contract',precio_mensual_bob=100,max_sucursales=3)
    db_session.add(plan);db_session.flush();ensure_legacy_versions(db_session.connection());db_session.commit()
    setup={**setup,'plan':plan,'legacy_manual_quotes':True}
    quoted=quote(client,setup,kind='ALTA');requested,_=accept(client,setup,quoted)
    assert db_session.query(Subscription).one().estado=='PENDIENTE'
    assert db_session.query(Term).count()==0
    assert decide(client,setup,requested).status_code==200
    assert db_session.query(Term).one().precio_mensual_bob==100


def test_database_rejects_overlapping_legacy_terms(db_session,contract_setup):
    first=db_session.query(Term).one()
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(Term.__table__.insert().values(tenant_id=first.tenant_id,
            subscription_id=first.subscription_id,fecha_inicio=first.fecha_inicio+timedelta(days=1),
            fecha_fin=first.fecha_fin,anchor_day=first.anchor_day,moneda='BOB',periodicidad='LEGADA',
            max_sucursales=first.max_sucursales,features=first.features,origen='REFERENCIA_LEGADA'))


def test_upgrade_from_existing_subscription_without_contract_tables(db_session,contract_setup):
    from pathlib import Path
    connection=db_session.connection();sub_id=contract_setup['sub'].id
    before=dict(connection.execute(select(Subscription.__table__).where(Subscription.id==sub_id)).mappings().one())
    # Simulate an actual pre-P3 schema, rather than leaving newer FK children.
    connection.exec_driver_sql('DROP TABLE subscription_payment_events')
    connection.exec_driver_sql('DROP TABLE subscription_payments')
    connection.exec_driver_sql('DROP TABLE subscription_terms')
    connection.exec_driver_sql('DROP TABLE subscription_change_requests')
    sql=(Path(__file__).parents[1]/'migrations/saas_03_subscription_terms.sql').read_text(encoding='utf-8')
    connection.exec_driver_sql(sql)
    first=dict(connection.execute(select(Term.__table__)).mappings().one())
    connection.exec_driver_sql(sql)
    assert dict(connection.execute(select(Term.__table__)).mappings().one())==first
    assert dict(connection.execute(select(Subscription.__table__).where(Subscription.id==sub_id)).mappings().one())==before
    assert first['precio_mensual_bob'] is None and first['origen']=='REFERENCIA_LEGADA'


def test_future_upgrade_does_not_grant_current_quota_and_reactivation_checks_current_limit(client,db_session,contract_setup):
    setup=contract_setup
    version=catalog_draft(client,setup['operator'],str(setup['plan'].id),max_sucursales=10)
    assert transition(client,setup['operator'],version,'publish').status_code==200
    requested,_=accept(client,setup);assert decide(client,setup,requested).status_code==200
    for i in range(4):db_session.add(Branch(tenant_id=setup['admin'].tenant_id,nombre=f'Active {i}',ciudad='SC',direccion='Test',activa=True))
    inactive=Branch(tenant_id=setup['admin'].tenant_id,nombre='Inactive',ciudad='SC',direccion='Test',activa=False)
    db_session.add(inactive);db_session.commit();branch_id=inactive.id
    assert client.put(f'/api/v1/sucursales/{branch_id}',headers=headers(setup['admin']),json={'activa':True}).status_code==400
    assert db_session.get(Branch,branch_id).activa is False


def test_database_term_must_match_accepted_price(client,db_session,contract_setup):
    setup=contract_setup;requested,_=accept(client,setup)
    row=db_session.get(Request,requested['id']);schedule=row.quote_payload['schedule'];conditions=row.quote_payload['conditions']
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(Term.__table__.insert().values(tenant_id=row.tenant_id,subscription_id=row.subscription_id,
            version_id=row.version_id,request_id=row.id,fecha_inicio=date.fromisoformat(schedule['fecha_inicio']),
            fecha_fin=date.fromisoformat(schedule['fecha_fin']),anchor_day=schedule['anchor_day'],precio_mensual_bob=1,
            moneda='BOB',periodicidad='MENSUAL',max_sucursales=conditions['max_sucursales'],features=conditions['features'],
            origen='MANUAL',accepted_by=row.accepted_by,accepted_at=row.accepted_at,
            approved_by=setup['operator'].id,approved_at=Service.now()))
