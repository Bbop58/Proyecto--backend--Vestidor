"""SM1 real PostgreSQL row-lock races in a dedicated schema, no hosted data."""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Tenant,Role,User
from app.core.security import get_password_hash,decode_token
from app.services.mobile_session_service import MobileSessionService as Sessions
from app.services.auth_service import AuthService
from app.schemas.auth import ChangePasswordRequest
from tests.conftest import test_engine,TEST_DATABASE_URL


@pytest.fixture
def committed_mobile_shop():
    schema='mobile_race_'+uuid.uuid4().hex
    with test_engine.begin() as c:c.exec_driver_sql('CREATE SCHEMA '+schema)
    engine=create_engine(TEST_DATABASE_URL,connect_args={'options':'-csearch_path='+schema})
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            company=Tenant(nombre='QA',razon_social='QA',nit_rut='QA',slug='qa',activo=True)
            role=Role(name='admin');db.add_all([company,role]);db.flush()
            user=User(tenant_id=company.id,role_id=role.id,email='race@example.com',full_name='QA',hashed_password=get_password_hash('Previous123!'),is_active=True)
            db.add(user);db.commit();identifier=user.id
        yield engine,identifier
    finally:
        engine.dispose()
        with test_engine.begin() as c:c.exec_driver_sql('DROP SCHEMA '+schema+' CASCADE')


def wait_for_lock(engine,pid):
    for _ in range(100):
        with engine.connect() as c:
            waiting=c.scalar(text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),{'pid':pid})
        if waiting=='Lock':return
        time.sleep(.02)
    pytest.fail('The second transaction never waited for the real PostgreSQL lock')


@pytest.mark.parametrize('first',['refresh','logout'])
def test_refresh_and_logout_are_serialized_and_never_leave_valid_access(committed_mobile_shop,monkeypatch,first):
    import app.services.mobile_session_service as module
    engine,user_id=committed_mobile_shop
    with Session(engine) as db:issued=Sessions.issue(db,db.get(User,user_id))
    locked=Event();release=Event();started=Event();pids={};original=module._lookup
    def pause(db,credential):
        row=original(db,credential)
        if current_thread().name.startswith('mobile-first'):
            locked.set();assert release.wait(10)
        return row
    monkeypatch.setattr(module,'_lookup',pause)
    def operation(kind,second=False):
        with Session(engine) as db:
            if second:pids['second']=db.scalar(text('SELECT pg_backend_pid()'));started.set()
            try:
                if kind=='refresh':return Sessions.refresh(db,issued.session_token)
                Sessions.revoke(db,issued.session_token);return None
            except HTTPException as e:db.rollback();return e.status_code
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='mobile-first') as one,ThreadPoolExecutor(max_workers=1,thread_name_prefix='mobile-second') as two:
        first_future=one.submit(operation,first)
        try:
            assert locked.wait(10)
            second_future=two.submit(operation,'logout' if first=='refresh' else 'refresh',True)
            assert started.wait(10);wait_for_lock(engine,pids['second'])
        finally:release.set()
        first_result=first_future.result(15);second_result=second_future.result(15)
    if first=='logout':assert second_result==401
    else:assert first_result.session_id==issued.session_id
    with Session(engine) as db:
        with pytest.raises(HTTPException):Sessions.validate_access(db,decode_token(issued.access_token))
        if first=='refresh':
            with pytest.raises(HTTPException):Sessions.validate_access(db,decode_token(first_result.access_token))


def test_password_change_waits_for_creation_and_revokes_that_new_session(committed_mobile_shop,monkeypatch):
    import app.services.mobile_session_service as module
    engine,user_id=committed_mobile_shop
    locked=Event();release=Event();started=Event();pids={};original=module._response
    def pause(db,row,user):locked.set();assert release.wait(10);return original(db,row,user)
    monkeypatch.setattr(module,'_response',pause)
    def create():
        with Session(engine) as db:return Sessions.issue(db,db.get(User,user_id))
    def password():
        with Session(engine) as db:
            pids['password']=db.scalar(text('SELECT pg_backend_pid()'));started.set()
            AuthService.change_password(db,db.get(User,user_id),ChangePasswordRequest(current_password='Previous123!',new_password='Updated123!'))
    with ThreadPoolExecutor(max_workers=2) as workers:
        created=workers.submit(create)
        try:
            assert locked.wait(10);changed=workers.submit(password)
            assert started.wait(10);wait_for_lock(engine,pids['password'])
        finally:release.set()
        issued=created.result(15);changed.result(15)
    with Session(engine) as db:
        with pytest.raises(HTTPException):Sessions.validate_access(db,decode_token(issued.access_token))
