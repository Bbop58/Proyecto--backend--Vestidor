import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from app.models import Tenant
from app.models.analytics_model import AnalyticsModel
from app.ml.analytics_store import (analysis_update, save_analysis, load_analysis,
    invalidate_analytics, lock_analytics_restore, make_metadata)


@pytest.fixture
def store_companies(db_session):
    engine = db_session.get_bind().engine
    with Session(engine) as db:
        rows = [Tenant(nombre=k, razon_social=k, nit_rut=k, slug='ml-'+uuid.uuid4().hex) for k in ['a', 'b']]
        db.add_all(rows); db.commit(); ids = [row.id for row in rows]
    yield engine, ids
    with engine.begin() as conn: conn.execute(delete(Tenant).where(Tenant.id.in_(ids)))


def metadata():
    from datetime import datetime, timezone
    return make_metadata('0'*64, datetime(2026, 1, 14, tzinfo=timezone.utc))


def write(engine, company, kind='FORECAST', artifact=b'qa-generated-model', result=None):
    with analysis_update(engine, company) as db:
        return save_analysis(db, company, kind, artifact, result or {'total': 90}, metadata())


def test_last_model_per_kind_and_company_survives_reload(store_companies):
    engine, (a,b) = store_companies
    write(engine,a); write(engine,a,'RFM'); write(engine,b)
    write(engine,a,artifact=b'updated',result={'total':110})
    with Session(engine) as db:
        own = load_analysis(db,a,'FORECAST')
        assert own.state == 'LISTO' and own.result == {'total':110} and own.artifact == b'updated'
        assert load_analysis(db,b,'FORECAST').result == {'total':90}
        assert load_analysis(db,b,'RFM').state == 'SIN_ANALISIS'
        assert len(db.scalars(select(AnalyticsModel).where(AnalyticsModel.tenant_id==a)).all()) == 2


def test_exception_and_invalid_payload_preserve_previous_model(store_companies):
    engine,(a,_) = store_companies; write(engine,a)
    with pytest.raises(RuntimeError):
        with analysis_update(engine,a) as db:
            save_analysis(db,a,'FORECAST',b'new',{'total':999},metadata())
            raise RuntimeError('synthetic failure')
    for blob,result in [(b'x'*(4*1024*1024+1),{}),(b'new',{'bad':float('nan')})]:
        with pytest.raises(HTTPException):
            with analysis_update(engine,a) as db:save_analysis(db,a,'FORECAST',blob,result,metadata())
    with Session(engine) as db: assert load_analysis(db,a,'FORECAST').result == {'total':90}


def test_update_lock_blocks_same_company_but_not_other_company(store_companies):
    engine,(a,b) = store_companies
    with analysis_update(engine,a):
        with pytest.raises(HTTPException) as error:
            with analysis_update(engine,a):pass
        assert error.value.status_code==409
        with analysis_update(engine,b) as db:save_analysis(db,b,'RFM',b'own',{},metadata())
        with engine.begin() as conn:
            with pytest.raises(HTTPException) as error:lock_analytics_restore(conn,a)
            assert error.value.status_code==409


def test_scope_and_unlocked_writes_are_rejected(store_companies):
    engine,(a,b) = store_companies
    with Session(engine) as db:
        with pytest.raises(HTTPException):save_analysis(db,a,'FORECAST',b'x',{},metadata())
        db.info['tenant_scope']=a
        with pytest.raises(HTTPException):load_analysis(db,b,'FORECAST')
    with analysis_update(engine,a) as db:
        with pytest.raises(HTTPException):save_analysis(db,b,'FORECAST',b'x',{},metadata())
        with pytest.raises(HTTPException):save_analysis(db,a,'UNSAFE',b'x',{},metadata())


def test_corruption_and_library_mismatch_require_update(store_companies):
    engine,(a,_) = store_companies; write(engine,a)
    with Session(engine) as db:
        row=db.scalar(select(AnalyticsModel).where(AnalyticsModel.tenant_id==a))
        row.artifact=b'corrupt';db.commit()
        assert load_analysis(db,a,'FORECAST').state=='REQUIERE_ACTUALIZAR'
    write(engine,a)
    with Session(engine) as db:
        row=db.scalar(select(AnalyticsModel).where(AnalyticsModel.tenant_id==a))
        row.model_metadata={**row.model_metadata,'sklearn_version':'incompatible'};db.commit()
        assert load_analysis(db,a,'FORECAST').state=='REQUIERE_ACTUALIZAR'


def test_invalidation_is_transactional_and_scoped(store_companies):
    engine,(a,b)=store_companies;write(engine,a);write(engine,b)
    with pytest.raises(RuntimeError):
        with engine.begin() as conn:
            invalidate_analytics(conn,a);raise RuntimeError('rollback')
    with Session(engine) as db:assert load_analysis(db,a,'FORECAST').state=='LISTO'
    with engine.begin() as conn:invalidate_analytics(conn,a)
    with Session(engine) as db:
        assert load_analysis(db,a,'FORECAST').state=='SIN_ANALISIS'
        assert load_analysis(db,b,'FORECAST').state=='LISTO'


def test_restore_lock_also_prevents_training_and_releases_after_rollback(store_companies):
    engine,(a,_) = store_companies
    with pytest.raises(RuntimeError):
        with engine.begin() as conn:
            lock_analytics_restore(conn,a)
            with pytest.raises(HTTPException) as error:
                with analysis_update(engine,a):pass
            assert error.value.status_code==409
            raise RuntimeError('rollback restore')
    write(engine,a)


def test_update_reads_a_repeatable_source_snapshot(store_companies):
    engine,(a,_) = store_companies
    from sqlalchemy import text
    with analysis_update(engine,a) as db:
        assert db.scalar(text('SHOW transaction_isolation'))=='repeatable read'


def test_operator_and_platform_context_cannot_load_models(store_companies):
    engine,(a,_) = store_companies
    with Session(engine) as db:
        technical=Tenant(nombre='Operator',razon_social='Operator',nit_rut='ML-op',
            slug='ml-op-'+uuid.uuid4().hex,es_operadora=True)
        db.add(technical);db.commit();identifier=technical.id
        try:
            with pytest.raises(HTTPException):load_analysis(db,identifier,'FORECAST')
            db.info['platform_actor_id']=uuid.uuid4()
            with pytest.raises(HTTPException):load_analysis(db,a,'FORECAST')
        finally:
            db.info.clear();db.delete(technical);db.commit()
