"""SM1 transactional recovery and migration; only the isolated QA database."""
import json
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import select,text,inspect
from app.models.mobile_session import MobileSession
from app.services.mobile_session_service import MobileSessionService as Sessions
from app.services.backup_service import BackupService,json_serializer
from app.core.tenant_scope import bind_tenant
from tests.test_tenant_sales import shops


def save(db,user):
    bind_tenant(db,user.tenant_id,user.id)
    return json.loads(json.dumps(BackupService.create_backup(db,user),default=json_serializer))


def test_company_backup_excludes_sessions_restore_invalidates_only_owner(db_session,shops):
    db=db_session;a=shops['a'][0];b=shops['b'][0]
    own=Sessions.issue(db,a);other=Sessions.issue(db,b)
    saved=save(db,a)
    assert saved['version']=='4.6.0' and 'mobile_sessions' not in saved['data']
    assert own.session_token not in json.dumps(saved)
    assert db.get(MobileSession,own.session_id).credential_hash not in json.dumps(saved)
    assert BackupService.restore_backup(db,saved,a)['success']
    # The snapshot recreates users, so CASCADE may remove the revoked own row.
    assert db.connection().execute(select(MobileSession.__table__.c.id).where(MobileSession.id==own.session_id)).first() is None
    assert db.connection().execute(select(MobileSession.__table__.c.revoked_at).where(MobileSession.id==other.session_id)).scalar() is None
    with pytest.raises(HTTPException):Sessions.validate_access(db,{'mobile_session_id':str(own.session_id),'sub':str(a.id),'tenant_id':str(a.tenant_id)})


def test_restore_failure_rolls_revocation_and_own_data_back(db_session,shops,monkeypatch):
    from app.services.audit_service import AuditService
    db=db_session;a=shops['a'][0];own=Sessions.issue(db,a);saved=save(db,a)
    def fail(*args,**kwargs):raise RuntimeError('QA final restore failure')
    monkeypatch.setattr(AuditService,'record',fail)
    with pytest.raises(HTTPException):BackupService.restore_backup(db,saved,a)
    row=db.connection().execute(select(MobileSession.__table__).where(MobileSession.id==own.session_id)).mappings().one()
    assert row['revoked_at'] is None
    assert Sessions.refresh(db,own.session_token).session_id==own.session_id


def test_admin_reset_or_deactivation_revokes_only_target(db_session,shops):
    from app.services.user_service import UserService
    from app.schemas.user import UserAdminUpdate
    db=db_session;a=shops['a'][0];b=shops['b'][0]
    one=Sessions.issue(db,a);two=Sessions.issue(db,b)
    UserService.update_by_admin(db,a,UserAdminUpdate(password='ResetOnly123!'),a)
    assert db.get(MobileSession,one.session_id).revoked_at is not None
    assert db.get(MobileSession,two.session_id).revoked_at is None
    # Admins cannot deactivate themselves; use a separate own-company operator.
    from app.models.user import User
    actor=User(tenant_id=a.tenant_id,role_id=a.role_id,email='session-admin@example.com',full_name='QA',hashed_password='unused',is_active=True)
    db.add(actor);db.commit()
    three=Sessions.issue(db,a)
    UserService.toggle_active(db,a,actor)
    assert db.get(MobileSession,three.session_id).revoked_at is not None


def test_mobile_migration_repeat_and_rollback_never_seed(db_session):
    from scripts.migrate_mobile_sessions import migrate_connection
    engine=db_session.get_bind().engine;schema='mobile_qa_'+uuid.uuid4().hex
    with engine.begin() as c:
        c.exec_driver_sql('CREATE SCHEMA '+schema)
        c.exec_driver_sql('SET LOCAL search_path TO '+schema)
        c.exec_driver_sql('CREATE TABLE tenants(id uuid PRIMARY KEY)')
        c.exec_driver_sql('CREATE TABLE users(id uuid PRIMARY KEY,tenant_id uuid REFERENCES tenants(id))')
    try:
        with pytest.raises(RuntimeError):
            with engine.begin() as c:
                c.exec_driver_sql('SET LOCAL search_path TO '+schema)
                migrate_connection(c);raise RuntimeError('QA rollback')
        with engine.connect() as c:assert not inspect(c).has_table('mobile_sessions',schema=schema)
        with engine.begin() as c:
            c.exec_driver_sql('SET LOCAL search_path TO '+schema)
            assert migrate_connection(c)['seed_executed'] is False
            migrate_connection(c)
            assert c.scalar(text('SELECT count(*) FROM mobile_sessions'))==0
            assert c.scalar(text('SELECT count(*) FROM users'))==0
            assert {f['options']['ondelete'] for f in inspect(c).get_foreign_keys('mobile_sessions',schema=schema)}=={'CASCADE'}
    finally:
        with engine.begin() as c:c.exec_driver_sql('DROP SCHEMA '+schema+' CASCADE')


def test_full_dump_recovery_revokes_restored_mobile_sessions_without_deleting_business(db_session,shops):
    from scripts.migrate_mobile_sessions import revoke_restored_sessions
    db=db_session;own=Sessions.issue(db,shops['a'][0]);other=Sessions.issue(db,shops['b'][0])
    count=db.connection().scalar(text('SELECT count(*) FROM users'))
    assert revoke_restored_sessions(db.connection())==2
    db.commit()
    assert db.connection().scalar(text('SELECT count(*) FROM users'))==count
    assert db.get(MobileSession,own.session_id).revoked_at is not None
    assert db.get(MobileSession,other.session_id).revoked_at is not None
    assert revoke_restored_sessions(db.connection())==0


def test_migration_refuses_unknown_existing_shape(db_session):
    from scripts.migrate_mobile_sessions import migrate_connection
    engine=db_session.get_bind().engine;schema='mobile_bad_'+uuid.uuid4().hex
    with engine.begin() as c:
        c.exec_driver_sql('CREATE SCHEMA '+schema)
        c.exec_driver_sql('SET LOCAL search_path TO '+schema)
        c.exec_driver_sql('CREATE TABLE tenants(id uuid PRIMARY KEY)')
        c.exec_driver_sql('CREATE TABLE users(id uuid PRIMARY KEY)')
        c.exec_driver_sql('CREATE TABLE mobile_sessions(id uuid PRIMARY KEY,credential_hash text)')
    try:
        with pytest.raises(ValueError):
            with engine.begin() as c:
                c.exec_driver_sql('SET LOCAL search_path TO '+schema);migrate_connection(c)
    finally:
        with engine.begin() as c:c.exec_driver_sql('DROP SCHEMA '+schema+' CASCADE')


def test_company_backup_cannot_import_operational_session_credentials(db_session,shops):
    from app.services.backup_recovery import digest
    a=shops['a'][0];issued=Sessions.issue(db_session,a);saved=save(db_session,a)
    saved['data']['mobile_sessions']=[];saved['checksum_sha256']=digest(saved['data'])
    with pytest.raises(HTTPException) as caught:BackupService.restore_backup(db_session,saved,a)
    assert caught.value.status_code==400
    assert db_session.get(MobileSession,issued.session_id).revoked_at is None


def test_failed_password_commit_rolls_back_session_revocation(db_session,shops,monkeypatch):
    from app.services.auth_service import AuthService
    from app.schemas.auth import ChangePasswordRequest
    from app.core.security import get_password_hash,verify_password
    a=shops['a'][0];a.hashed_password=get_password_hash('Previous123!');db_session.commit()
    issued=Sessions.issue(db_session,a)
    def fail():raise RuntimeError('QA commit failure')
    monkeypatch.setattr(db_session,'commit',fail)
    with pytest.raises(RuntimeError):AuthService.change_password(db_session,a,ChangePasswordRequest(current_password='Previous123!',new_password='Updated123!'))
    db_session.rollback()
    assert db_session.get(MobileSession,issued.session_id).revoked_at is None
    assert verify_password('Previous123!',a.hashed_password)


def test_migration_cli_refuses_implicit_or_nonlocal_maintenance(monkeypatch):
    from scripts import migrate_mobile_sessions as migration
    def no_connection(*args,**kwargs):pytest.fail('Must refuse before connecting')
    monkeypatch.setattr(migration,'create_engine',no_connection)
    with pytest.raises(SystemExit):migration.main([])
    monkeypatch.setenv('DATABASE_URL','postgresql://synthetic:unused@remote.invalid/production')
    assert migration.main(['--apply'])==1
