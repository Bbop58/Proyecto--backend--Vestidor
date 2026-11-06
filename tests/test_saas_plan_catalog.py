"""S2: immutable offers never rewrite current commercial conditions."""
import uuid
import copy
from decimal import Decimal
from datetime import date,timedelta
from pathlib import Path
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from app.models.subscription_plan import SubscriptionPlan
from app.models.subscription_plan_version import SubscriptionPlanVersion as Version
from app.models.subscription import Subscription
from app.models.platform_audit_log import PlatformAuditLog
from app.services.platform_audit_service import PlatformAuditService
from app.core.plan_catalog_ddl import ensure_legacy_versions
from tests.support_saas import saas_actors, headers


def terms(**updates):
    result={'precio_mensual_bob':'199.00','max_sucursales':2,
        'features':{'reportes_ia':False,'analitica_avanzada':True,'offline':True},
        'descripcion':'Oferta sintética de prueba','reason':'Synthetic catalog test'}
    result.update(updates);return result


def create_plan(client,actor,code='TEST_PLAN'):
    response=client.post('/api/v1/platform/plans',headers=headers(actor),json={
        'codigo':code,'nombre':'Plan de prueba','reason':'Synthetic plan creation'})
    assert response.status_code==201,response.text
    return response.json()['id']


def draft(client,actor,plan_id,**updates):
    response=client.post(f'/api/v1/platform/plans/{plan_id}/versions',
        headers=headers(actor),json=terms(**updates))
    assert response.status_code==201,response.text
    return response.json()


def transition(client,actor,version,action,expected=None):
    return client.post(f"/api/v1/platform/plan-versions/{version['id']}/{action}",
        headers=headers(actor),json={'expected_version':version['lock_version'] if expected is None else expected,
                                   'reason':'Synthetic transition test'})


def test_company_cannot_write_catalog(client,saas_actors):
    response=client.post('/api/v1/platform/plans',headers=headers(saas_actors['admin']),
        json={'codigo':'FORGED','nombre':'Forged','reason':'Not a platform operator'})
    assert response.status_code==403,response.text


def test_seed_cannot_publish_unreviewed_platform_identity(client,db_session,saas_actors):
    actor=saas_actors['operator'];plan=create_plan(client,actor)
    ensure_legacy_versions(db_session.connection());db_session.commit()
    assert db_session.query(Version).count()==0
    assert client.get('/api/v1/subscriptions/plans').json()==[]


def test_publish_new_price_preserves_previous_version(client,db_session,saas_actors):
    actor=saas_actors['operator'];plan=create_plan(client,actor)
    first=draft(client,actor,plan)
    assert client.get('/api/v1/subscriptions/plans').json()==[]
    published=transition(client,actor,first,'publish')
    assert published.status_code==200,published.text
    second=draft(client,actor,plan,precio_mensual_bob='299.00',max_sucursales=4)
    assert transition(client,actor,second,'publish').status_code==200
    old=client.get(f"/api/v1/platform/plan-versions/{first['id']}",headers=headers(actor)).json()
    assert Decimal(str(old['precio_mensual_bob']))==Decimal('199.00')
    assert old['max_sucursales']==2
    offers=client.get('/api/v1/subscriptions/plans').json()
    assert len(offers)==1 and offers[0]['version_id']==second['id']
    assert offers[0]['id']==plan and offers[0]['codigo']=='TEST_PLAN'
    assert Decimal(str(offers[0]['precio_mensual_bob']))==Decimal('299.00')


@pytest.mark.parametrize('invalid',[
    {'precio_mensual_bob':'-0.01'},{'precio_mensual_bob':'199.001'},
    {'precio_mensual_bob':'NaN'},{'precio_mensual_bob':'Infinity'},
    {'precio_mensual_bob':'100000000.00'},{'precio_mensual_bob':None},
    {'max_sucursales':0},{'max_sucursales':1.5},{'max_sucursales':True},
    {'max_sucursales':2147483648},{'moneda':'USD'},{'periodicidad':'ANUAL'},
    {'features':{'reportes_ia':False,'analitica_avanzada':True,'offline':True,'usuarios_ilimitados':True}},
    {'features':{'reportes_ia':'false','analitica_avanzada':True,'offline':True}},
    {'features':{'reportes_ia':False,'offline':True}},
    {'revision':7},{'estado':'PUBLICADA'},{'origen':'REFERENCIA_LEGADA'},
])
def test_invalid_conditions_never_enter_catalog(client,db_session,saas_actors,invalid):
    actor=saas_actors['operator'];plan=create_plan(client,actor)
    response=client.post(f'/api/v1/platform/plans/{plan}/versions',headers=headers(actor),json=terms(**invalid))
    assert response.status_code==422,response.text
    assert db_session.query(Version).count()==0


def test_draft_edit_conflict_and_published_immutability(client,saas_actors):
    actor=saas_actors['operator'];plan=create_plan(client,actor);first=draft(client,actor,plan)
    path=f"/api/v1/platform/plan-versions/{first['id']}"
    edited=client.put(path,headers=headers(actor),json=terms(precio_mensual_bob='210.50',expected_version=0))
    assert edited.status_code==200,edited.text
    assert edited.json()['lock_version']==1
    stale=client.put(path,headers=headers(actor),json=terms(expected_version=0))
    assert stale.status_code==409
    assert transition(client,actor,first,'publish').status_code==409
    current=edited.json();assert transition(client,actor,current,'publish').status_code==200
    assert client.put(path,headers=headers(actor),json=terms(expected_version=2)).status_code==409


def test_publish_retry_is_one_effect_and_retirement_does_not_resurrect_old_offer(client,db_session,saas_actors):
    actor=saas_actors['operator'];plan=create_plan(client,actor)
    old=draft(client,actor,plan);assert transition(client,actor,old,'publish').status_code==200
    latest=draft(client,actor,plan,precio_mensual_bob='0.00')
    first=transition(client,actor,latest,'publish');again=transition(client,actor,latest,'publish')
    assert first.status_code==again.status_code==200 and first.json()==again.json()
    assert first.json()['es_gratuito'] is True and first.json()['periodicidad']=='MENSUAL'
    assert db_session.query(PlatformAuditLog).filter_by(action='PLAN_PUBLISH',target_id=latest['id']).count()==1
    retired=transition(client,actor,first.json(),'retire')
    assert retired.status_code==200
    assert transition(client,actor,first.json(),'retire').json()==retired.json()
    assert client.get('/api/v1/subscriptions/plans').json()==[]
    assert transition(client,actor,retired.json(),'publish').status_code==409
    saved=client.get(f"/api/v1/platform/plan-versions/{latest['id']}",headers=headers(actor))
    assert saved.status_code==200 and saved.json()['estado']=='RETIRADA'


def test_database_rejects_overwrite_delete_and_duplicate_revision(client,db_session,saas_actors):
    actor=saas_actors['operator'];plan=create_plan(client,actor);version=draft(client,actor,plan)
    assert transition(client,actor,version,'publish').status_code==200
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(Version.__table__.update().where(Version.id==version['id']).values(
            precio_mensual_bob=1,lock_version=2))
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(Version.__table__.delete().where(Version.id==version['id']))
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(Version.__table__.insert().values(plan_id=plan,revision=1,
            precio_mensual_bob=100,max_sucursales=1,permite_reportes_ia=False,
            permite_analitica_avanzada=False,permite_offline=False))


def test_catalog_publication_audit_failure_rolls_back(client,db_session,saas_actors,monkeypatch):
    actor=saas_actors['operator'];plan=create_plan(client,actor);version=draft(client,actor,plan)
    def fail(*args,**kwargs):raise HTTPException(503,'Synthetic audit failure')
    monkeypatch.setattr(PlatformAuditService,'record',fail)
    assert transition(client,actor,version,'publish').status_code==503
    assert db_session.get(Version,version['id']).estado=='BORRADOR'
    assert client.get('/api/v1/subscriptions/plans').json()==[]


def test_legacy_migration_preserves_basic_analytics_current_subscription_and_seed_repeat(client,db_session,saas_actors):
    actor=saas_actors['operator'];company=saas_actors['a_company']
    plan=SubscriptionPlan(codigo='LEGACY_BASIC',nombre='Legacy',precio_mensual_bob=199,
        max_sucursales=2,permite_reportes_ia=False,permite_offline=True)
    db_session.add(plan);db_session.flush()
    subscription=Subscription(tenant_id=company.id,plan_id=plan.id,
        fecha_vencimiento=date.today()+timedelta(days=30))
    db_session.add(subscription);db_session.commit()
    from app.core.subscription_contract_ddl import ensure_legacy_terms
    ensure_legacy_terms(db_session.connection());db_session.commit()
    before=(subscription.plan_id,subscription.fecha_inicio,subscription.fecha_vencimiento)
    ensure_legacy_versions(db_session.connection());db_session.commit()
    initial=db_session.query(Version).filter_by(plan_id=plan.id).one()
    assert initial.origen=='REFERENCIA_LEGADA' and initial.permite_analitica_avanzada is True
    assert initial.permite_reportes_ia is False and initial.permite_offline is True
    ensure_legacy_versions(db_session.connection());db_session.commit()
    assert db_session.query(Version).count()==1
    new=draft(client,actor,str(plan.id),precio_mensual_bob='999.00',max_sucursales=7)
    assert transition(client,actor,new,'publish').status_code==200
    db_session.expire_all()
    assert (subscription.plan_id,subscription.fecha_inicio,subscription.fecha_vencimiento)==before
    assert subscription.plan.max_sucursales==2 and subscription.plan.precio_mensual_bob==199
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(SubscriptionPlan.__table__.update().where(SubscriptionPlan.id==plan.id)
            .values(precio_mensual_bob=999))
    current=client.get('/api/v1/subscriptions/current',headers=headers(saas_actors['admin']))
    assert current.status_code==200,current.text
    assert current.json()['plan']['precio_mensual_bob'] is None
    assert current.json()['origen']=='REFERENCIA_LEGADA'


def test_explicit_upgrade_is_repeatable_without_contract_edits(db_session,saas_actors):
    db=db_session;connection=db.connection()
    # Cache identity before deliberately removing a column the modern ORM
    # expects; legacy migration is exercised entirely through Core below.
    company_id=saas_actors['a_company'].id
    connection.exec_driver_sql('DROP TABLE subscription_plan_versions CASCADE')
    connection.exec_driver_sql('ALTER TABLE subscription_plans DROP COLUMN catalog_managed')
    plan_id=uuid.uuid4();sub_id=uuid.uuid4()
    connection.execute(text("INSERT INTO subscription_plans(id,codigo,nombre,max_sucursales,permite_reportes_ia,permite_offline,precio_mensual_bob,activo,created_at,updated_at) VALUES (:id,'LEGACY','Legacy',2,false,true,199,true,now(),now())"),{'id':plan_id})
    connection.execute(Subscription.__table__.insert().values(id=sub_id,tenant_id=company_id,
        plan_id=plan_id,fecha_vencimiento=date.today()+timedelta(days=30)))
    before=dict(connection.execute(select(Subscription.__table__).where(Subscription.id==sub_id)).mappings().one())
    sql=(Path(__file__).parents[1]/'migrations/saas_02_contract_catalog.sql').read_text(encoding='utf-8')
    connection.exec_driver_sql(sql)
    initial=dict(connection.execute(select(Version.__table__)).mappings().one())
    connection.exec_driver_sql(sql)
    assert dict(connection.execute(select(Version.__table__)).mappings().one())==initial
    assert initial['origen']=='REFERENCIA_LEGADA' and initial['permite_analitica_avanzada'] is True
    assert dict(connection.execute(select(Subscription.__table__).where(Subscription.id==sub_id)).mappings().one())==before


def test_legacy_reference_never_recreates_retired_offer(client,db_session,saas_actors):
    plan=SubscriptionPlan(codigo='LEGACY_RETIRED',nombre='Legacy',precio_mensual_bob=0,
        max_sucursales=2,permite_reportes_ia=False,permite_offline=True)
    db_session.add(plan);db_session.flush();ensure_legacy_versions(db_session.connection());db_session.commit()
    version=db_session.query(Version).one()
    actor=saas_actors['operator']
    saved=client.get(f'/api/v1/platform/plan-versions/{version.id}',headers=headers(actor)).json()
    assert transition(client,actor,saved,'retire').status_code==200
    ensure_legacy_versions(db_session.connection());db_session.commit()
    assert db_session.query(Version).count()==1
    assert client.get('/api/v1/subscriptions/plans').json()==[]


@pytest.mark.parametrize('key,value',[
    ('permite_offline',False),('permite_analitica_avanzada',False),
    ('max_sucursales',10),('descripcion','Overwritten description'),
])
def test_published_database_conditions_are_immutable(client,db_session,saas_actors,key,value):
    actor=saas_actors['operator'];plan=create_plan(client,actor);version=draft(client,actor,plan)
    assert transition(client,actor,version,'publish').status_code==200
    with pytest.raises(IntegrityError),db_session.begin_nested():
        db_session.connection().execute(Version.__table__.update().where(Version.id==version['id'])
            .values(**{key:value,'lock_version':2}))


def test_company_orm_cannot_write_global_version(db_session,saas_actors):
    from app.api.deps import get_authenticated_identity
    actor=saas_actors['admin']
    get_authenticated_identity(db_session,{'sub':str(actor.id),'tenant_id':str(actor.tenant_id)})
    db_session.add(Version(plan_id=uuid.uuid4(),revision=1,precio_mensual_bob=10,max_sucursales=1,
        permite_reportes_ia=False,permite_analitica_avanzada=False,permite_offline=False))
    with pytest.raises(HTTPException) as error:db_session.flush()
    assert error.value.status_code==403


def test_invalid_legacy_upgrade_rolls_back_original_schema_and_data(db_session,saas_actors):
    connection=db_session.connection()
    connection.exec_driver_sql('DROP TABLE subscription_plan_versions CASCADE')
    connection.exec_driver_sql('ALTER TABLE subscription_plans DROP COLUMN catalog_managed')
    plan_id=uuid.uuid4()
    connection.execute(text("INSERT INTO subscription_plans(id,codigo,nombre,max_sucursales,permite_reportes_ia,permite_offline,precio_mensual_bob,activo,created_at,updated_at) VALUES (:id,'INVALID_LEGACY','Invalid',0,false,true,199,true,now(),now())"),{'id':plan_id})
    sql=(Path(__file__).parents[1]/'migrations/saas_02_contract_catalog.sql').read_text(encoding='utf-8')
    with pytest.raises(IntegrityError),connection.begin_nested():connection.exec_driver_sql(sql)
    assert connection.execute(text("SELECT to_regclass('subscription_plan_versions')")).scalar() is None
    assert connection.execute(text('SELECT max_sucursales FROM subscription_plans WHERE id=:id'),{'id':plan_id}).scalar_one()==0


@pytest.mark.parametrize('path,method,payload',[
    ('/plans','get',None),('/plans/{id}/versions','get',None),
    ('/plans/{id}/versions','post',terms()),('/plan-versions/{id}','get',None),
    ('/plan-versions/{id}','put',terms(expected_version=0)),
    ('/plan-versions/{id}/publish','post',{'expected_version':0,'reason':'Forbidden platform access'}),
    ('/plan-versions/{id}/retire','post',{'expected_version':0,'reason':'Forbidden platform access'}),
])
def test_all_catalog_routes_require_platform_before_access(client,saas_actors,path,method,payload):
    path='/api/v1/platform'+path.replace('{id}',str(uuid.UUID(int=0)))
    response=client.request(method.upper(),path,headers=headers(saas_actors['admin']),
        **({'json':payload} if payload is not None else {}))
    assert response.status_code==403,response.text
