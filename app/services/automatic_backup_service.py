"""Finite automatic backups. A session advisory lock spans SQL and storage I/O."""
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.config import settings
from app.models.backup_policy import BackupPolicy as Policy
from app.models.backup_copy import BackupCopy as Copy
from app.models.tenant import Tenant
from app.models.audit_log import AuditLog
from app.services.backup_storage import BackupStorage, StorageError, MAX_BYTES, storage_ready
from app.services.backup_recovery import export_snapshot, serialize, VERSION


def utcnow():
    return datetime.now(timezone.utc)


def lock_key(tenant_id):
    return int.from_bytes(hashlib.sha256(('automatic-backup:' + str(tenant_id)).encode()).digest()[:8], 'big', signed=True)


def _company(db, actor):
    from app.core.access_policy import resolve_access_scope, AccessScope
    if resolve_access_scope(actor) != AccessScope.COMPANY or not actor.tenant_id:
        raise HTTPException(403, 'Se requiere una sesión empresarial')
    if db.info.get('platform_actor_id') or db.info.get('tenant_scope', actor.tenant_id) != actor.tenant_id:
        raise HTTPException(403, 'Contexto empresarial incompatible')
    return actor.tenant_id


def seed_policies(connection):
    """Explicit seed/migration only: preserve existing UUID, schedule and settings."""
    companies = connection.execute(select(Tenant.id).where(Tenant.es_operadora.is_(False))).scalars()
    for tenant_id in companies:
        connection.execute(insert(Policy).values(id=uuid.uuid4(), tenant_id=tenant_id,
            enabled=False, interval_hours=48, retry_count=0, revision=0).on_conflict_do_nothing(index_elements=['tenant_id']))


def get_policy(db, actor):
    tenant_id = _company(db, actor)
    policy = db.scalar(select(Policy).where(Policy.tenant_id == tenant_id))
    if policy is None:
        db.execute(insert(Policy).values(id=uuid.uuid4(), tenant_id=tenant_id,
            enabled=False, interval_hours=48, retry_count=0, revision=0).on_conflict_do_nothing(index_elements=['tenant_id']))
        db.commit()
        policy = db.scalar(select(Policy).where(Policy.tenant_id == tenant_id))
    return policy


def policy_summary(policy):
    return {field: getattr(policy, field) for field in (
        'enabled', 'interval_hours', 'revision', 'next_run_at', 'last_success_at', 'last_error_code')}


def update_policy(db, actor, payload, now_utc=None):
    tenant_id = _company(db, actor)
    policy = get_policy(db, actor)
    if not db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': lock_key(tenant_id)}):
        raise HTTPException(409, 'Hay una copia en curso; vuelve a intentarlo al terminar')
    db.refresh(policy)
    if policy.revision != payload.revision:
        raise HTTPException(409, 'La configuración cambió; actualiza antes de guardar')
    if payload.enabled and not storage_ready():
        raise HTTPException(503, 'El almacenamiento de respaldos no está configurado')
    now_utc = now_utc or utcnow()
    policy.enabled, policy.interval_hours = payload.enabled, payload.interval_hours
    policy.revision += 1
    policy.retry_count, policy.last_error_code = 0, None
    policy.next_run_at = now_utc + timedelta(hours=payload.interval_hours) if payload.enabled else None
    from app.services.audit_service import AuditService
    AuditService.record(db, 'Configuración de respaldo automático actualizada', 'Backups', actor)
    db.commit()
    return policy


def list_copies(db, actor):
    tenant_id = _company(db, actor)
    rows = db.scalars(select(Copy).where(Copy.tenant_id == tenant_id, Copy.status == 'SUCCESS')
        .order_by(Copy.completed_at.desc(), Copy.id.desc()).limit(7)).all()
    return [{key: getattr(row, key) for key in ('id', 'started_at', 'completed_at', 'size', 'schema_version')} for row in rows]


def download_copy(db, actor, copy_id, storage=None):
    tenant_id = _company(db, actor)
    row = db.scalar(select(Copy).where(Copy.id == copy_id, Copy.tenant_id == tenant_id, Copy.status == 'SUCCESS'))
    if row is None:
        raise HTTPException(404, 'Copia no disponible')
    own_storage = storage is None
    storage = storage or BackupStorage.configured()
    try:
        content = storage.get_verified(row.object_key, row.checksum)
        from app.services.audit_service import AuditService
        AuditService.record(db, 'Copia automática descargada', 'Backups', actor)
        db.commit()
        return content
    finally:
        if own_storage:
            storage.close()


def _service_audit(db, tenant_id, action):
    db.add(AuditLog(tenant_id=tenant_id, user_id=None, user_name='Servicio de respaldos',
        user_role='servicio_interno', action=action, module='Backups'))


def _prune(db, tenant_id, policy_id, storage):
    delete_failed = False
    rows = db.scalars(select(Copy).where(Copy.tenant_id == tenant_id,
        Copy.policy_id == policy_id, Copy.status == 'SUCCESS')
        .order_by(Copy.completed_at.desc(), Copy.id.desc())).all()
    for row in rows[7:]:
        row.status = 'DELETING'
    db.commit()
    for row in db.scalars(select(Copy).where(Copy.policy_id == policy_id, Copy.status == 'DELETING')).all():
        try:
            storage.delete(row.object_key)
            row.status = 'DELETED'
            row.error_code = None
        except StorageError:
            row.error_code = 'DELETE_FAILED'
            delete_failed = True
        db.commit()
    # Only terminal metadata, never storage objects from other generations.
    cutoff = utcnow() - timedelta(days=30)
    old = db.scalars(select(Copy).where(Copy.policy_id == policy_id,
        Copy.status.in_(['FAILED', 'DELETED']), Copy.completed_at < cutoff,
        # Never purge the active retry window: its key/checksum recover uploads.
        Copy.due_at != select(Policy.next_run_at).where(Policy.id == policy_id).scalar_subquery())).all()
    for row in old:
        db.delete(row)
    policy = db.get(Policy, policy_id)
    if delete_failed and policy is not None:
        policy.last_error_code = 'RETENTION_FAILED'
    db.commit()


def _execute(db, policy, storage, now, environment):
    tenant_id, policy_id, revision, due = policy.tenant_id, policy.id, policy.revision, policy.next_run_at
    row = db.scalar(select(Copy).where(Copy.policy_id == policy_id, Copy.due_at == due))
    if row is None:
        identifier = uuid.uuid4()
        row = Copy(id=identifier, policy_id=policy_id, tenant_id=tenant_id, due_at=due,
            started_at=now, status='RUNNING', object_key=f'{environment}/{tenant_id}/{policy_id}/{identifier}.json')
        db.add(row)
    else:
        row.status, row.error_code = 'RUNNING', None
    db.commit()
    copy_id, key = row.id, row.object_key
    try:
        # An interrupted upload may already be complete; reuse its exact payload.
        # Checksum was durably saved before the original upload.
        if row.checksum:
            try:
                content = storage.get_verified(key, row.checksum)
            except StorageError:
                content = None
        else:
            content = None
        if content is None:
            db.info.update(tenant_scope=tenant_id, backup_worker=True)
            snapshot = export_snapshot(db)
            content = json.dumps(snapshot, default=serialize, ensure_ascii=False, sort_keys=True).encode('utf-8')
            db.commit()  # Release export's SHARE locks before any network I/O.
            if not 0 < len(content) <= MAX_BYTES:
                raise StorageError('SIZE_LIMIT')
            row.checksum = hashlib.sha256(content).hexdigest()
            row.size, row.schema_version = len(content), VERSION
            db.commit()
            storage.put_verified(key, content, {'version': VERSION, 'policy-id': str(policy_id)})
        db.expire_all()
        current = db.scalar(select(Policy).where(Policy.id == policy_id, Policy.revision == revision))
        if current is None:
            db.rollback()
            return 'discarded'
        row = db.get(Copy, copy_id)
        row.status, row.completed_at, row.error_code = 'SUCCESS', utcnow(), None
        current.last_success_at, current.last_error_code, current.retry_count = row.completed_at, None, 0
        current.next_run_at = now + timedelta(hours=current.interval_hours)
        _service_audit(db, tenant_id, 'Respaldo automático generado correctamente')
        db.commit()
    except Exception as error:
        db.rollback()
        db.expire_all()
        current = db.scalar(select(Policy).where(Policy.id == policy_id, Policy.revision == revision))
        if current is None:
            db.rollback()
            return 'discarded'
        row = db.get(Copy, copy_id)
        # Stable public codes only; provider exception bodies may include secrets.
        code = str(error) if isinstance(error, StorageError) and str(error) in {
            'SIZE_LIMIT', 'UPLOAD_FAILED', 'DOWNLOAD_FAILED', 'INTEGRITY_FAILED'} else 'BACKUP_FAILED'
        row.status, row.completed_at, row.error_code = 'FAILED', utcnow(), code
        current.last_error_code = code
        current.retry_count += 1
        if current.retry_count >= 3:
            current.retry_count = 0
            current.next_run_at = now + timedelta(hours=current.interval_hours)
        _service_audit(db, tenant_id, 'Falló el respaldo automático: ' + code)
        db.commit()
        return 'failed'
    _prune(db, tenant_id, policy_id, storage)
    return 'success'


def run_due_backups(engine, now_utc=None, max_companies=10, budget_seconds=240, storage=None, environment=None):
    now, started = now_utc or utcnow(), time.monotonic()
    environment = environment or settings.BACKUP_ENVIRONMENT
    import re
    if not environment or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', environment):
        raise StorageError('STORAGE_NOT_CONFIGURED')
    own_storage = storage is None
    storage = storage or BackupStorage.configured()
    result = {'success': 0, 'failed': 0, 'skipped': 0, 'discarded': 0}
    try:
        with Session(engine) as db:
            pending = db.execute(select(Policy.id, Policy.tenant_id).join(Tenant, Tenant.id == Policy.tenant_id)
                .where(Policy.enabled.is_(True), Policy.next_run_at <= now, Tenant.es_operadora.is_(False))
                .order_by(Policy.next_run_at).limit(max_companies)).all()
        for policy_id, tenant_id in pending:
            if time.monotonic() - started >= budget_seconds - 45:
                break
            with engine.connect() as connection:
                acquired = connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': lock_key(tenant_id)})
                connection.commit()
                if not acquired:
                    result['skipped'] += 1
                    continue
                try:
                    with Session(bind=connection) as db:
                        db.execute(text("SET LOCAL statement_timeout='30s'"))
                        policy = db.scalar(select(Policy).where(Policy.id == policy_id,
                            Policy.enabled.is_(True), Policy.next_run_at <= now))
                        if policy is None:
                            result['skipped'] += 1
                        else:
                            result[_execute(db, policy, storage, now, environment)] += 1
                finally:
                    connection.rollback()
                    connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': lock_key(tenant_id)})
                    connection.commit()
    finally:
        if own_storage:
            storage.close()
    return result
