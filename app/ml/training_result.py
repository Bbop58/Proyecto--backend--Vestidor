"""Internal outcomes. Only the server builds compressed model envelopes."""
from dataclasses import dataclass
import io
import joblib


@dataclass
class TrainingOutcome:
    artifact: bytes
    result: dict


def outcome(dataset, kind, model, result=None, message='', warnings=()):
    payload = {'estado':'LISTO' if result is not None else 'DATOS_INSUFICIENTES',
        'calidad':dict(dataset.quality), 'advertencias':list(dataset.warnings)+list(warnings),
        'mensaje':message, 'resultado':result}
    stream=io.BytesIO()
    joblib.dump({'format':1,'kind':kind,'tenant_id':str(dataset.tenant_id),
        'dataset_hash':dataset.fingerprint,'model':model},stream,compress=3)
    return TrainingOutcome(stream.getvalue(),payload)


def load_server_model(stored,tenant_id,kind):
    """Internal QA/inference only: caller obtains StoredAnalysis from trusted DB.

    Never expose a model upload or call this with arbitrary client bytes. Reads of
    the dashboard use stored JSON and do not need to deserialize an estimator.
    """
    if stored.state!='LISTO' or not stored.artifact:raise ValueError('Validated server artifact required')
    payload=joblib.load(io.BytesIO(stored.artifact))
    if (not isinstance(payload,dict) or payload.get('format')!=1 or payload.get('kind')!=kind
        or payload.get('tenant_id')!=str(tenant_id)
        or payload.get('dataset_hash')!=stored.metadata['dataset_hash']):
        raise ValueError('Incompatible company model envelope')
    return payload['model']
