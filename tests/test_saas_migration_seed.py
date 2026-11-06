"""Populated pre-SaaS schema, only in the PostgreSQL database validated by conftest."""
import uuid
from datetime import timedelta
from pathlib import Path
import pytest
from sqlalchemy import MetaData,select,text
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Tenant,User,Role,SubscriptionPlan,Subscription,SubscriptionTerm,SubscriptionPlanVersion
from app.core.billing_calendar import business_today
from tests.conftest import test_engine
from tests.test_seed_phase0 import seed_database,snapshot
from tests.test_tenant_sales import shops


@pytest.fixture
def legacy_saas():
    schema='saas_upgrade_'+uuid.uuid4().hex
    with test_engine.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=test_engine.execution_options(schema_translate_map={None:schema})
    metadata=MetaData()
    excluded={'platform_audit_logs','subscription_plan_versions','subscription_change_requests','subscription_terms','subscription_payments','subscription_payment_events'}
    for table in Base.metadata.sorted_tables:
        if table.name not in excluded:table.to_metadata(metadata)
    for name,columns in {'tenants':['es_operadora','platform_revision'],'subscription_plans':['catalog_managed']}.items():
        table=metadata.tables[name]
        for index in list(table.indexes):
            if any(getattr(col,'name',None) in columns for col in index.expressions):table.indexes.remove(index)
        for column in columns:table._columns.remove(table.c[column])
    ids={key:uuid.uuid4() for key in ['company','user','role','plan','subscription']}
    try:
        with engine.begin() as conn:
            conn.execute(text('SET LOCAL search_path TO '+schema+',public'))
            metadata.create_all(conn)
            conn.execute(metadata.tables['tenants'].insert().values(id=ids['company'],nombre='Legacy QA',razon_social='Legacy QA',nit_rut='LEGACY',slug='legacy-qa',activo=True))
            conn.execute(metadata.tables['roles'].insert().values(id=ids['role'],name='admin'))
            conn.execute(metadata.tables['users'].insert().values(id=ids['user'],tenant_id=ids['company'],role_id=ids['role'],email='legacy@synthetic.example.com',full_name='Legacy QA',hashed_password='unchanged-synthetic-hash',is_active=True,is_verified=True))
            conn.execute(metadata.tables['subscription_plans'].insert().values(id=ids['plan'],codigo='LEGACY',nombre='Legacy',max_sucursales=3,precio_mensual_bob=199,permite_reportes_ia=False,permite_offline=True))
            conn.execute(metadata.tables['subscriptions'].insert().values(id=ids['subscription'],tenant_id=ids['company'],plan_id=ids['plan'],estado='ACTIVA',fecha_inicio=business_today()-timedelta(days=5),fecha_vencimiento=business_today()+timedelta(days=20)))
        yield engine,schema,ids
    finally:
        with test_engine.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))


def test_migration_preserves_effective_legacy_analytics_access(legacy_saas):
    from scripts.migrate_saas import migrate_connection
    from app.services.entitlement_service import EntitlementService
    engine,schema,ids=legacy_saas
    with engine.begin() as conn:
        conn.execute(text('SET LOCAL search_path TO '+schema+',public'))
        original=dict(conn.execute(text('SELECT * FROM subscriptions')).mappings().one())
        migrate_connection(conn)
        before=[dict(r) for r in conn.execute(select(SubscriptionTerm.__table__)).mappings()]
        migrate_connection(conn)
        assert [dict(r) for r in conn.execute(select(SubscriptionTerm.__table__)).mappings()]==before
        assert dict(conn.execute(select(Subscription.__table__)).mappings().one())==original
        assert before[0]['precio_mensual_bob'] is None and before[0]['accepted_at'] is None
        assert before[0]['fecha_fin']==original['fecha_vencimiento']+timedelta(days=1)
    with Session(engine) as db:
        actor=db.get(User,ids['user']);effective=EntitlementService.resolve(db,actor)
        assert effective['features']=={'reportes_ia':False,'analitica_avanzada':True,'offline':True}
        assert actor.hashed_password=='unchanged-synthetic-hash'


def test_migration_failure_rolls_back_ddl_and_keeps_populated_data(legacy_saas,monkeypatch,tmp_path):
    import scripts.migrate_saas as migration
    engine,schema,ids=legacy_saas
    failure=tmp_path/'failure.sql';failure.write_text('SELECT 1/0;',encoding='utf-8')
    monkeypatch.setattr(migration,'MIGRATIONS',(*migration.MIGRATIONS,failure))
    with pytest.raises(Exception),engine.begin() as conn:
        conn.execute(text('SET LOCAL search_path TO '+schema+',public'));migration.migrate_connection(conn)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM '+schema+'.users')).scalar_one()==1
        assert conn.execute(text("SELECT to_regclass(:name)"),{'name':schema+'.subscription_terms'}).scalar_one() is None


def test_readiness_rejects_missing_saas_guard_trigger(db_session):
    from app.api.health import schema_compatible
    conn=db_session.connection();assert schema_compatible(conn)
    conn.exec_driver_sql('DROP TRIGGER tr_subscription_term ON subscription_terms')
    assert not schema_compatible(conn)


def test_optional_seed_operator_is_stable_and_never_granted_by_name(seed_database,monkeypatch):
    import contextlib,io
    from app import seed_database as seed
    from app.config import settings
    from app.saas_integrity import verify_counts
    monkeypatch.setattr(settings,'PLATFORM_ADMIN_USER_IDS',[])
    operator_id=uuid.uuid5(uuid.NAMESPACE_URL,'fashionstore:test:operator:s6')
    with contextlib.redirect_stdout(io.StringIO()):seed.seed_all()
    with seed_database() as db:
        assert not db.query(Tenant).filter_by(es_operadora=True).count()
        operator=seed.seed_demo_operator(db,operator_id,'S6-SyntheticOnly123!');db.commit()
        assert not operator.is_platform_admin
        assert operator.role.name=='superadmin_plataforma' and operator.role.permissions==[]
        commercial=db.query(User).filter_by(email='admin@urbanstyle.com').one()
        assert commercial.tenant_id!=operator.tenant_id and not commercial.tenant.es_operadora
        monkeypatch.setattr(settings,'PLATFORM_ADMIN_USER_IDS',[str(operator_id)])
        assert operator.is_platform_admin and not commercial.is_platform_admin
        assert not any(verify_counts(db.connection(),[operator_id]).values())
    before=snapshot(seed_database)
    with contextlib.redirect_stdout(io.StringIO()):seed.seed_all()
    with seed_database() as db:seed.seed_demo_operator(db,operator_id,'Another-Synthetic123!');db.commit()
    assert snapshot(seed_database)==before


def test_seed_operator_refuses_existing_commercial_uuid_without_reassignment(db_session,shops):
    from app.seed_database import seed_demo_operator
    user,_,_=shops['a'];original=(user.id,user.email,user.tenant_id,user.role_id)
    with pytest.raises(ValueError):seed_demo_operator(db_session,user.id,'S6-SyntheticOnly123!')
    assert (user.id,user.email,user.tenant_id,user.role_id)==original
