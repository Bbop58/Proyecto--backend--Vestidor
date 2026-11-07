"""Only server-generated artifacts; atomic replacement, no model deserialization here."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import re
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select, text, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from app.models.analytics_model import AnalyticsModel
from app.ml.analytics_data import company_id, normalized_cutoff

MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
_GUARD = object()


def _kind(kind):
    if kind not in ('FORECAST', 'RFM'): raise HTTPException(422, 'Tipo de análisis no permitido')
    return kind


def lock_key(tenant_id):
    return int.from_bytes(hashlib.sha256(('analytics:' + str(UUID(str(tenant_id)))).encode()).digest()[:8], 'big', signed=True)


def make_metadata(dataset_hash, cutoff):
    if not isinstance(dataset_hash, str) or not re.fullmatch('[a-f0-9]{64}', dataset_hash):
        raise HTTPException(422, 'Huella de datos no válida')
    return {'dataset_hash': dataset_hash, 'cutoff': normalized_cutoff(cutoff).isoformat(),
        'schema_version': 1, 'sklearn_version': version('scikit-learn'),
        'joblib_version': version('joblib'), 'source': 'BASE_DATOS_EMPRESA', 'environment': 'DEMO'}


def _valid_metadata(metadata):
    try:
        return isinstance(metadata, dict) and metadata == make_metadata(metadata['dataset_hash'],
            datetime.fromisoformat(metadata['cutoff']))
    except (KeyError, ValueError, TypeError, HTTPException): return False


@dataclass
class StoredAnalysis:
    state: str
    artifact: bytes | None = None
    result: dict | None = None
    metadata: dict | None = None
    trained_at: datetime | None = None


@contextmanager
def analysis_update(engine, tenant_id):
    # Own the connection until UNLOCK: commits must never return a locked
    # connection to a pool that another request could borrow.
    with engine.connect() as connection:
        locked = connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': lock_key(tenant_id)})
        connection.commit()
        if not locked: raise HTTPException(409, 'Hay un análisis en curso para esta empresa')
        try:
            connection = connection.execution_options(isolation_level='REPEATABLE READ')
            with Session(bind=connection) as db:
                tenant = company_id(db, tenant_id)
                db.info.update(tenant_scope=tenant, analytics_guard=_GUARD)
                try:
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback(); raise
        finally:
            connection.rollback()
            connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': lock_key(tenant_id)})
            connection.commit()


def save_analysis(db, tenant_id, kind, artifact, result, metadata):
    tenant, kind = company_id(db, tenant_id), _kind(kind)
    if db.info.get('analytics_guard') is not _GUARD:
        raise HTTPException(403, 'El guardado requiere una actualización empresarial exclusiva')
    if not isinstance(artifact, bytes) or not 0 < len(artifact) <= MAX_ARTIFACT_BYTES:
        raise HTTPException(413, 'El modelo supera el tamaño permitido o está vacío')
    if not _valid_metadata(metadata): raise HTTPException(422, 'Metadatos del modelo incompatibles')
    try:
        if not isinstance(result, dict): raise ValueError()
        encoded = json.dumps(result, allow_nan=False)
        if len(encoded.encode()) > 1024 * 1024: raise ValueError()
    except (ValueError, TypeError): raise HTTPException(422, 'Resultado del análisis no válido')
    values = dict(tenant_id=tenant, kind=kind, artifact=artifact, result=result,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(), model_metadata=metadata,
        trained_at=datetime.now(timezone.utc))
    statement = insert(AnalyticsModel).values(**values)
    db.connection().execute(statement.on_conflict_do_update(
        constraint='uq_analytics_company_kind', set_={k:v for k,v in values.items() if k not in ('tenant_id','kind')}))


def load_analysis(db, tenant_id, kind):
    tenant, kind = company_id(db, tenant_id), _kind(kind)
    row = db.scalar(select(AnalyticsModel).where(AnalyticsModel.tenant_id == tenant, AnalyticsModel.kind == kind))
    if row is None: return StoredAnalysis('SIN_ANALISIS')
    valid = (0 < len(row.artifact) <= MAX_ARTIFACT_BYTES and _valid_metadata(row.model_metadata)
        and hashlib.sha256(row.artifact).hexdigest() == row.artifact_sha256)
    if not valid: return StoredAnalysis('REQUIERE_ACTUALIZAR', trained_at=row.trained_at)
    return StoredAnalysis('LISTO', bytes(row.artifact), row.result, row.model_metadata, row.trained_at)


def lock_analytics_restore(connection, tenant_id):
    if not connection.in_transaction(): raise ValueError('Explicit restore transaction required')
    if not connection.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': lock_key(tenant_id)}):
        raise HTTPException(409, 'Hay un análisis en curso; reintenta la restauración al terminar')


def invalidate_analytics(connection, tenant_id):
    lock_analytics_restore(connection, tenant_id)
    connection.execute(delete(AnalyticsModel.__table__).where(AnalyticsModel.tenant_id == UUID(str(tenant_id))))
