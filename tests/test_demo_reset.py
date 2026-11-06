"""Explicit destructive demo reset; never uses the project's actual DB."""
import pytest
from sqlalchemy import text
from tests.conftest import test_engine


@pytest.fixture(autouse=True)
def isolate_committed_demo_reset():
    """The reset intentionally commits; never leave its seeded data for other tests."""
    from app.database import Base
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(test_engine)
        Base.metadata.create_all(test_engine)


def test_fresh_rebuilds_legacy_schema_seeds_twice_and_authenticates():
    from scripts.reset_demo_database import reset_demo_database
    from app.database import Base
    from app.models.user import User
    from app.schemas.auth import LoginRequest
    from app.services.auth_service import AuthService
    from sqlalchemy.orm import Session
    with test_engine.begin() as connection:
        connection.execute(text('CREATE TABLE runtime_demo_probe(id integer)'))
        connection.execute(text('INSERT INTO runtime_demo_probe VALUES (1)'))
        connection.execute(text('ALTER TABLE roles DROP COLUMN tenant_id CASCADE'))
    result=reset_demo_database(test_engine)
    assert result['tables_changed_on_repeat']==[]
    assert len(result['checks'])==26  # 18 commercial + 8 SaaS invariants
    assert not any(result['checks'].values())
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('runtime_demo_probe')")).scalar() is None
        assert connection.execute(text('SELECT count(*) FROM ventas')).scalar()==1135
        connection.execute(text('SELECT tenant_id FROM roles LIMIT 1'))
    with Session(test_engine) as db:
        for email in ('admin@urbanstyle.com','admin@modaexpress.com'):
            user=db.query(User).filter_by(email=email).one()
            authenticated,_,_=AuthService.login(db,LoginRequest(email=email,password='Password123!',tenant_id=user.tenant_id))
            assert authenticated.id==user.id


def test_seed_failure_restores_previous_schema_and_data(monkeypatch):
    from scripts.reset_demo_database import reset_demo_database
    from app import seed_database
    with test_engine.begin() as connection:
        connection.execute(text('CREATE TABLE rollback_demo_probe(id integer)'))
        connection.execute(text('INSERT INTO rollback_demo_probe VALUES (42)'))
        before=connection.execute(text('SELECT count(*) FROM users')).scalar()
    def fail(*args,**kwargs):
        raise RuntimeError('injected seed failure')
    monkeypatch.setattr(seed_database,'seed_all',fail)
    try:
        with pytest.raises(RuntimeError,match='injected seed failure'):
            reset_demo_database(test_engine)
        with test_engine.connect() as connection:
            assert connection.execute(text('SELECT id FROM rollback_demo_probe')).scalar()==42
            assert connection.execute(text('SELECT count(*) FROM users')).scalar()==before
    finally:
        with test_engine.begin() as connection:
            connection.execute(text('DROP TABLE IF EXISTS rollback_demo_probe'))


def test_reset_requires_explicit_confirmation_before_any_connection(monkeypatch):
    from scripts import reset_demo_database as reset
    monkeypatch.setattr(reset,'create_engine',lambda *a,**kw:pytest.fail('Must not connect'))
    assert reset.main([])==2


def test_reset_requires_explicit_url_before_any_connection(monkeypatch):
    from scripts import reset_demo_database as reset
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setattr(reset,'create_engine',lambda *a,**kw:pytest.fail('Must not connect'))
    assert reset.main(['--confirm-disposable-demo'])==2


def test_optional_operator_collision_rolls_back_previous_schema_and_data():
    import uuid
    from scripts.reset_demo_database import reset_demo_database
    commercial_id=uuid.uuid5(uuid.NAMESPACE_URL,'fashionstore:demo:user:admin@urbanstyle.com')
    with test_engine.begin() as connection:
        connection.execute(text('CREATE TABLE operator_rollback_probe(id integer)'))
        connection.execute(text('INSERT INTO operator_rollback_probe VALUES (73)'))
    try:
        with pytest.raises(ValueError):
            reset_demo_database(test_engine,commercial_id,'Synthetic-OnlyS6Password!')
        with test_engine.connect() as connection:
            assert connection.execute(text('SELECT id FROM operator_rollback_probe')).scalar_one()==73
            assert connection.execute(text('SELECT count(*) FROM users')).scalar_one()==0
    finally:
        with test_engine.begin() as connection:
            connection.execute(text('DROP TABLE IF EXISTS operator_rollback_probe'))


def test_optional_operator_requires_password_before_starting_reset():
    import uuid
    from types import SimpleNamespace
    from scripts.reset_demo_database import reset_demo_database
    engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),
                           begin=lambda:pytest.fail('Invalid operator must not start destructive DDL'))
    with pytest.raises(ValueError):reset_demo_database(engine,uuid.uuid4(),None)
