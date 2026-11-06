"""Recovery must never erase or invent externally tracked commercial payments."""
import copy
import json
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.models import PaymentIntent, Product
from app.core.tenant_scope import bind_tenant
from app.services.backup_service import BackupService, json_serializer
from app.services.backup_recovery import digest
from tests.test_tenant_sales import shops


def snapshot(db, actor):
    bind_tenant(db, actor.tenant_id, actor.id)
    return json.loads(json.dumps(BackupService.create_backup(db, actor), default=json_serializer))


def intent(db, actor, state='APPLIED'):
    row = PaymentIntent(tenant_id=actor.tenant_id, user_id=actor.id, purpose='WALLET', state=state,
        provider_order_id='QA_ORDER_' + uuid.uuid4().hex, capture_id='QA_CAPTURE_' + uuid.uuid4().hex,
        amount_bob=6.96, amount_usd=1, exchange_rate=6.96, merchant_key='a' * 64,
        provider_mode='sandbox', payload={})
    db.add(row); db.commit(); return row


def test_restore_cannot_erase_a_commercial_capture_created_after_backup(db_session, shops):
    actor, variant, _ = shops['a']
    saved = snapshot(db_session, actor)
    row = intent(db_session, actor); identifier, capture = row.id, row.capture_id
    variant.producto.nombre = 'Latest company data'; db_session.commit()
    with pytest.raises(HTTPException) as error:
        BackupService.restore_backup(db_session, saved, actor)
    assert error.value.status_code == 409
    assert db_session.get(PaymentIntent, identifier).capture_id == capture
    assert db_session.get(Product, variant.producto_id).nombre == 'Latest company data'


def test_restore_cannot_rewind_a_refunded_commercial_capture(db_session, shops):
    actor = shops['a'][0]; row = intent(db_session, actor)
    saved = snapshot(db_session, actor)
    row.state, row.refund_id = 'REFUNDED', 'QA_REFUND'; db_session.commit()
    with pytest.raises(HTTPException) as error:
        BackupService.restore_backup(db_session, saved, actor)
    assert error.value.status_code == 409
    assert db_session.get(PaymentIntent, row.id).refund_id == 'QA_REFUND'


def test_restore_cannot_import_forged_capture_even_with_recomputed_checksum(db_session, shops):
    actor = shops['a'][0]; intent(db_session, actor)
    saved = snapshot(db_session, actor)
    saved['data']['payment_intents'][0]['capture_id'] = 'FORGED_CAPTURE'
    saved['checksum_sha256'] = digest(saved['data'])
    with pytest.raises(HTTPException) as error:
        BackupService.restore_backup(db_session, saved, actor)
    assert error.value.status_code == 409
    assert db_session.scalar(select(PaymentIntent.capture_id)) != 'FORGED_CAPTURE'


def test_unchanged_commercial_capture_is_compatible_with_restore(db_session, shops):
    actor = shops['a'][0]; row = intent(db_session, actor)
    identifier, capture = row.id, row.capture_id
    saved = snapshot(db_session, actor)
    assert BackupService.restore_backup(db_session, saved, actor)['success']
    assert db_session.get(PaymentIntent, identifier).capture_id == capture
