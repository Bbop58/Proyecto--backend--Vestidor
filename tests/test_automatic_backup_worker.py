"""Real PostgreSQL isolation, locking, recovery and retention; fake private S3."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import pytest
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Tenant, BackupPolicy, BackupCopy
from app.services.automatic_backup_service import run_due_backups, seed_policies
from app.services.backup_storage import BackupStorage
from tests.test_backup_storage import FakeS3
from tests.test_automatic_backups import NOW
from tests.conftest import test_engine


@pytest.fixture
def worker_db():
    schema = 'backup_worker_' + uuid.uuid4().hex
    with test_engine.begin() as connection:
        connection.execute(text('CREATE SCHEMA ' + schema))
    engine = create_engine(test_engine.url, connect_args={'options': '-csearch_path=' + schema},
        execution_options={'schema_translate_map': {None: schema}})
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            tenant = Tenant(nombre='Worker QA', razon_social='QA', nit_rut='worker', slug='worker')
            db.add(tenant); db.flush()
            policy = BackupPolicy(tenant_id=tenant.id, enabled=True, interval_hours=48, next_run_at=NOW)
            db.add(policy); db.commit()
            identifiers = tenant.id, policy.id
        # Do not hold fixture reads open while testing transactional DDL.
        yield engine, *identifiers
    finally:
        engine.dispose()
        with test_engine.begin() as connection:
            connection.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))


def test_worker_creates_one_valid_snapshot_and_advances(worker_db):
    engine, tenant_id, policy_id = worker_db
    s3 = FakeS3(); storage = BackupStorage(s3, 'qa')
    result = run_due_backups(engine, NOW, storage=storage, environment='qa')
    assert result['success'] == 1
    assert run_due_backups(engine, NOW, storage=storage, environment='qa')['success'] == 0
    assert len(s3.objects) == 1
    snapshot = json.loads(next(iter(s3.objects.values())))
    assert snapshot['tenant_id'] == str(tenant_id)
    assert 'backup_policies' not in snapshot['data'] and 'backup_copies' not in snapshot['data']
    with Session(engine) as db:
        assert db.get(BackupPolicy, policy_id).next_run_at == NOW + timedelta(hours=48)
        assert db.scalar(select(func.count()).select_from(BackupCopy)) == 1


def test_concurrent_worker_skips_locked_company(worker_db):
    engine, _, _ = worker_db
    entered, release = Event(), Event()
    s3 = FakeS3()
    original = s3.put_object
    def hold(**kwargs):
        entered.set()
        assert release.wait(10)
        original(**kwargs)
    s3.put_object = hold
    storage = BackupStorage(s3, 'qa')
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(run_due_backups, engine, NOW, storage=storage, environment='qa')
        try:
            assert entered.wait(10)
            second = run_due_backups(engine, NOW, storage=storage, environment='qa')
            assert second['skipped'] == 1
        finally:
            release.set()
        assert future.result(timeout=15)['success'] == 1
    assert len(s3.objects) == 1


def test_three_failures_reuse_window_then_schedule_next(worker_db):
    engine, _, policy_id = worker_db
    s3 = FakeS3(); s3.fail_put = True
    storage = BackupStorage(s3, 'qa')
    for n in range(3):
        assert run_due_backups(engine, NOW, storage=storage, environment='qa')['failed'] == 1
    with Session(engine) as db:
        p = db.get(BackupPolicy, policy_id)
        assert p.retry_count == 0 and p.last_success_at is None
        assert p.next_run_at == NOW + timedelta(hours=48)
        assert db.scalar(select(func.count()).select_from(BackupCopy)) == 1
    assert not s3.objects


def test_retry_recovers_upload_before_database_confirmation(worker_db, monkeypatch):
    from app.services import automatic_backup_service as service
    engine, _, policy_id = worker_db
    s3 = FakeS3(); storage = BackupStorage(s3, 'qa')
    original = service._service_audit
    def fail_after_upload(*args):
        if 'correctamente' in args[-1]:
            raise RuntimeError('Simulated crash after upload')
        return original(*args)
    monkeypatch.setattr(service, '_service_audit', fail_after_upload)
    assert run_due_backups(engine, NOW, storage=storage, environment='qa')['failed'] == 1
    first = dict(s3.objects)
    monkeypatch.setattr(service, '_service_audit', original)
    assert run_due_backups(engine, NOW, storage=storage, environment='qa')['success'] == 1
    assert s3.objects == first
    with Session(engine) as db:
        assert db.get(BackupPolicy, policy_id).last_error_code is None


def test_retention_only_after_new_success_and_delete_retry(worker_db):
    engine, _, policy_id = worker_db
    s3 = FakeS3(); storage = BackupStorage(s3, 'qa')
    for n in range(7):
        assert run_due_backups(engine, NOW + timedelta(hours=48*n), storage=storage, environment='qa')['success'] == 1
    assert len(s3.objects) == 7
    s3.fail_put = True
    assert run_due_backups(engine, NOW + timedelta(hours=48*7), storage=storage, environment='qa')['failed'] == 1
    assert len(s3.objects) == 7
    s3.fail_put = False; s3.fail_delete = True
    assert run_due_backups(engine, NOW + timedelta(hours=48*7), storage=storage, environment='qa')['success'] == 1
    assert len(s3.objects) == 8
    with Session(engine) as db:
        assert db.get(BackupPolicy, policy_id).last_error_code == 'RETENTION_FAILED'
    s3.fail_delete = False
    run_due_backups(engine, NOW + timedelta(hours=48*8), storage=storage, environment='qa')
    assert len(s3.objects) == 7
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(BackupCopy).where(BackupCopy.status == 'SUCCESS')) == 7


def test_seed_is_idempotent_and_does_not_disable_existing_schedule(worker_db):
    engine, _, policy_id = worker_db
    with engine.begin() as connection:
        seed_policies(connection); seed_policies(connection)
    with Session(engine) as db:
        assert db.get(BackupPolicy, policy_id).enabled
        assert db.scalar(select(func.count()).select_from(BackupPolicy)) == 1


def test_fresh_generation_fences_old_upload(worker_db):
    engine, tenant_id, policy_id = worker_db
    s3 = FakeS3(); original = s3.put_object
    def replace_generation(**kwargs):
        original(**kwargs)
        with engine.begin() as connection:
            connection.execute(BackupCopy.__table__.delete())
            connection.execute(BackupPolicy.__table__.delete())
            seed_policies(connection)
    s3.put_object = replace_generation
    result = run_due_backups(engine, NOW, storage=BackupStorage(s3, 'qa'), environment='qa')
    assert result['discarded'] == 1
    with Session(engine) as db:
        p = db.scalar(select(BackupPolicy).where(BackupPolicy.tenant_id == tenant_id))
        assert p.id != policy_id and not p.enabled and p.last_success_at is None


def test_stale_due_date_generates_only_one_current_copy(worker_db):
    engine, _, policy_id = worker_db
    result = run_due_backups(engine, NOW + timedelta(days=20), storage=BackupStorage(FakeS3(), 'qa'), environment='qa')
    assert result['success'] == 1
    with Session(engine) as db:
        assert db.get(BackupPolicy, policy_id).next_run_at == NOW + timedelta(days=22)


def test_process_interruption_recovers_running_record(worker_db, monkeypatch):
    from app.services import automatic_backup_service as service
    engine, _, _ = worker_db
    s3 = FakeS3(); storage = BackupStorage(s3, 'qa')
    original = service._service_audit
    monkeypatch.setattr(service, '_service_audit', lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        run_due_backups(engine, NOW, storage=storage, environment='qa')
    with Session(engine) as db:
        assert db.scalar(select(BackupCopy.status)) == 'RUNNING'
    objects = dict(s3.objects)
    monkeypatch.setattr(service, '_service_audit', original)
    assert run_due_backups(engine, NOW, storage=storage, environment='qa')['success'] == 1
    assert s3.objects == objects


def test_policy_edit_conflicts_with_running_export(worker_db):
    from app.services.automatic_backup_service import lock_key
    engine, tenant_id, _ = worker_db
    with engine.connect() as held:
        held.execute(text('SELECT pg_advisory_lock(:key)'), {'key': lock_key(tenant_id)})
        held.commit()
        with engine.connect() as other:
            assert not other.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': lock_key(tenant_id)})
        held.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': lock_key(tenant_id)})
        held.commit()


def test_explicit_migration_repeats_without_changing_uuid_or_schedule(worker_db):
    from scripts.migrate_automatic_backups import migrate_connection
    engine, _, policy_id = worker_db
    with engine.begin() as connection:
        migrate_connection(connection)
        migrate_connection(connection)
    with Session(engine) as db:
        policy = db.get(BackupPolicy, policy_id)
        assert policy.enabled and policy.next_run_at == NOW


def test_failed_company_does_not_prevent_other_company_backup(worker_db):
    engine, tenant_id, _ = worker_db
    with Session(engine) as db:
        tenant = Tenant(nombre='Other QA', razon_social='Other', nit_rut='other', slug='other')
        db.add(tenant); db.flush()
        db.add(BackupPolicy(tenant_id=tenant.id, enabled=True, interval_hours=48, next_run_at=NOW))
        db.commit()
    s3 = FakeS3(); original = s3.put_object
    def selective(**kwargs):
        if str(tenant_id) in kwargs['Key']:
            raise RuntimeError('Synthetic company failure')
        original(**kwargs)
    s3.put_object = selective
    result = run_due_backups(engine, NOW, storage=BackupStorage(s3, 'qa'), environment='qa')
    assert result['failed'] == result['success'] == 1
    assert len(s3.objects) == 1


def test_disabled_policy_and_exhausted_budget_do_not_write(worker_db):
    engine, _, policy_id = worker_db
    s3 = FakeS3(); storage = BackupStorage(s3, 'qa')
    assert run_due_backups(engine, NOW, storage=storage, environment='qa', budget_seconds=0)['success'] == 0
    with Session(engine) as db:
        policy = db.get(BackupPolicy, policy_id)
        policy.enabled, policy.next_run_at = False, None
        db.commit()
    assert run_due_backups(engine, NOW, storage=storage, environment='qa')['success'] == 0
    assert not s3.objects


def test_migration_can_roll_back_new_tables_without_touching_public(worker_db):
    from scripts.migrate_automatic_backups import migrate_connection
    from sqlalchemy import inspect
    engine, _, _ = worker_db
    schema = engine.get_execution_options()['schema_translate_map'][None]
    with engine.begin() as connection:
        BackupCopy.__table__.drop(connection)
        BackupPolicy.__table__.drop(connection)
    with engine.connect() as connection:
        transaction = connection.begin()
        migrate_connection(connection)
        assert inspect(connection).has_table('backup_policies', schema=schema)
        transaction.rollback()
        assert not inspect(connection).has_table('backup_policies', schema=schema)


def test_partial_upload_is_rejected_then_recovers(worker_db):
    engine, _, _ = worker_db
    s3 = FakeS3(); original = s3.put_object
    def partial(**kwargs):
        kwargs['Body'] = kwargs['Body'][:5]
        original(**kwargs)
    s3.put_object = partial
    storage = BackupStorage(s3, 'qa')
    assert run_due_backups(engine, NOW, storage=storage, environment='qa')['failed'] == 1
    with Session(engine) as db:
        assert db.scalar(select(BackupCopy.status)) == 'FAILED'
    s3.put_object = original
    assert run_due_backups(engine, NOW, storage=storage, environment='qa')['success'] == 1


def test_migration_grants_only_business_admin_and_not_cashier_or_operator(worker_db):
    from scripts.migrate_automatic_backups import migrate_connection
    from app.models import Role, Permission, role_permissions
    engine, tenant_id, _ = worker_db
    with Session(engine) as db:
        technical = Tenant(nombre='Operator QA', razon_social='QA', nit_rut='op', slug='operator', es_operadora=True)
        db.add(technical); db.flush()
        admin = Role(name='admin', tenant_id=tenant_id)
        cashier = Role(name='cajero', tenant_id=tenant_id)
        operator = Role(name='superadmin_plataforma', tenant_id=technical.id)
        db.add_all([admin, cashier, operator]); db.commit()
        expected_id = admin.id
    with engine.begin() as connection:
        migrate_connection(connection)
    with Session(engine) as db:
        permitted = db.scalars(select(role_permissions.c.role_id).join(Permission,
            Permission.id == role_permissions.c.permission_id).where(Permission.code == 'backups.configure')).all()
        assert permitted == [expected_id]
        assert db.scalar(select(func.count()).select_from(BackupPolicy)) == 1
