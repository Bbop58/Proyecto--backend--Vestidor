"""Automatic company backups: policy contract and tenant boundaries."""
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from app.models.backup_policy import BackupPolicy
from app.models.backup_copy import BackupCopy
from app.schemas.backup import AutomaticBackupUpdate
from app.services.automatic_backup_service import get_policy, update_policy, policy_summary
from app.services.backup_service import BackupService
from app.core.tenant_scope import bind_tenant
from tests.test_tenant_sales import shops

NOW = datetime(2026, 11, 6, 19, tzinfo=timezone.utc)


@pytest.mark.parametrize('hours', [0, -1, 169, 719, 721, 1.5, True, '48'])
def test_interval_rejects_unsupported_values(hours):
    with pytest.raises(ValidationError):
        AutomaticBackupUpdate(enabled=True, interval_hours=hours, revision=0)


@pytest.mark.parametrize('hours', [1, 48, 168, 192, 720])
def test_interval_accepts_hours_or_whole_days(hours):
    assert AutomaticBackupUpdate(enabled=True, interval_hours=hours, revision=0).interval_hours == hours


def test_policy_starts_disabled_and_is_stable(db_session, shops):
    actor = shops['a'][0]
    first = get_policy(db_session, actor)
    identifier = first.id
    assert not first.enabled and first.interval_hours == 48 and first.next_run_at is None
    assert get_policy(db_session, actor).id == identifier


def test_interval_change_disable_and_revision(db_session, shops, monkeypatch):
    from app.services import automatic_backup_service as service
    monkeypatch.setattr(service, 'storage_ready', lambda: True)
    a = shops['a'][0]
    p = update_policy(db_session, a, AutomaticBackupUpdate(enabled=True, interval_hours=48, revision=0), NOW)
    assert p.next_run_at == NOW + timedelta(hours=48)
    with pytest.raises(HTTPException) as error:
        update_policy(db_session, a, AutomaticBackupUpdate(enabled=False, interval_hours=48, revision=0), NOW)
    assert error.value.status_code == 409
    p = update_policy(db_session, a, AutomaticBackupUpdate(enabled=False, interval_hours=48, revision=1), NOW)
    assert p.next_run_at is None and not p.enabled
    assert policy_summary(p)['revision'] == 2


def test_absent_storage_does_not_enable_policy(db_session, shops, monkeypatch):
    from app.services import automatic_backup_service as service
    monkeypatch.setattr(service, 'storage_ready', lambda: False)
    with pytest.raises(HTTPException) as error:
        update_policy(db_session, shops['a'][0], AutomaticBackupUpdate(enabled=True, interval_hours=48, revision=0), NOW)
    assert error.value.status_code == 503
    assert not get_policy(db_session, shops['a'][0]).enabled


def test_backups_omit_operational_tables_and_restore_preserves_policy(db_session, shops):
    a = shops['a'][0]
    p = get_policy(db_session, a)
    identifier = p.id
    bind_tenant(db_session, a.tenant_id, a.id)
    data = BackupService.create_backup(db_session, a)
    assert 'backup_policies' not in data['data'] and 'backup_copies' not in data['data']
    BackupService.restore_backup(db_session, data, a)
    assert db_session.get(BackupPolicy, identifier) is not None


def test_policy_http_scoped_permissions_and_no_paths(client, db_session, shops, monkeypatch):
    from tests.test_tenant_contract import headers
    from app.services import automatic_backup_service as service
    monkeypatch.setattr(service, 'storage_ready', lambda: True)
    a, b = shops['a'][0], shops['b'][0]
    ra = client.get('/api/v1/backups/automatic', headers=headers(a))
    rb = client.get('/api/v1/backups/automatic', headers=headers(b))
    assert ra.status_code == rb.status_code == 200
    assert ra.json()['enabled'] is False
    response = client.put('/api/v1/backups/automatic', headers=headers(a), json={
        'enabled': True, 'interval_hours': 48, 'revision': 0, 'tenant_id': str(b.tenant_id)})
    assert response.status_code == 422
    assert client.get('/api/v1/backups/automatic/copies', headers=headers(a)).json() == []


def test_customer_cannot_read_or_configure_automatic_backups(client, db_session, shops):
    from app.models import Role
    from tests.test_tenant_contract import headers
    a = shops['a'][0]
    a.role_id = db_session.scalar(select(Role.id).where(Role.name == 'cliente'))
    db_session.commit()
    for path in ('/automatic', '/automatic/copies'):
        assert client.get('/api/v1/backups' + path, headers=headers(a)).status_code == 403
    assert client.put('/api/v1/backups/automatic', headers=headers(a), json={
        'enabled': False, 'interval_hours': 48, 'revision': 0}).status_code == 403


def test_download_is_own_success_only_and_does_not_expose_storage(client, db_session, shops, monkeypatch):
    import hashlib
    import uuid
    from tests.test_tenant_contract import headers
    from app.services.backup_storage import BackupStorage
    from tests.test_backup_storage import FakeS3
    from app.services import automatic_backup_service as service
    a, b = shops['a'][0], shops['b'][0]
    policy = get_policy(db_session, a)
    identifier = uuid.uuid4()
    row = BackupCopy(id=identifier, policy_id=policy.id, tenant_id=a.tenant_id,
        due_at=NOW, started_at=NOW, completed_at=NOW, status='SUCCESS',
        object_key='private/only/server.json', checksum=hashlib.sha256(b'{}').hexdigest(), size=2, schema_version='4.6.0')
    db_session.add(row); db_session.commit()
    fake = FakeS3(); fake.objects[row.object_key] = b'{}'
    monkeypatch.setattr(service.BackupStorage, 'configured', lambda: BackupStorage(fake, 'qa'))
    url = f'/api/v1/backups/automatic/copies/{identifier}/download'
    assert client.get(url, headers=headers(b)).status_code == 404
    response = client.get(url, headers=headers(a))
    assert response.status_code == 200 and response.content == b'{}'
    listing = client.get('/api/v1/backups/automatic/copies', headers=headers(a))
    assert 'private/only' not in listing.text and 'object_key' not in listing.text
    assert listing.json()[0]['id'] == str(identifier)


def test_manual_download_remains_available_without_storage(client, db_session, shops, monkeypatch):
    from tests.test_tenant_contract import headers
    from app.services import automatic_backup_service as service
    monkeypatch.setattr(service, 'storage_ready', lambda: False)
    response = client.get('/api/v1/backups/download', headers=headers(shops['a'][0]))
    assert response.status_code == 200 and response.json()['version'] == '4.6.0'
