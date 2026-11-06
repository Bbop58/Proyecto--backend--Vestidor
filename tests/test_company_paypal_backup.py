import json,copy
import pytest
from fastapi import HTTPException
from app.models.company_paypal_credential import CompanyPayPalCredential as Credential
from app.services.company_paypal_service import CompanyPayPalService as Service
from app.services.backup_service import BackupService,json_serializer
from app.services.backup_recovery import digest
from app.core.tenant_scope import bind_tenant
from tests.test_company_paypal_config import payment_key,shops,body


def snapshot(db,actor):
    bind_tenant(db,actor.tenant_id,actor.id)
    return json.loads(json.dumps(BackupService.create_backup(db,actor),default=json_serializer))


def test_backup_never_exports_secret_or_ciphertext_and_does_not_import_versions(db_session,shops):
    a=shops['a'][0];Service.configure(db_session,a,body())
    row=db_session.query(Credential).one();identifier=row.id;cipher=row.secret_ciphertext
    payload=snapshot(db_session,a);text=json.dumps(payload)
    assert payload['version']=='4.6.0' and 'synthetic-secret-never-return' not in text and cipher not in text
    assert 'company_paypal_credentials' not in payload['data']
    BackupService.restore_backup(db_session,payload,a)
    assert db_session.get(Credential,identifier).secret_ciphertext==cipher


def test_stale_backup_cannot_revert_rotated_or_disabled_collection_account(db_session,shops):
    a=shops['a'][0];Service.configure(db_session,a,body());payload=snapshot(db_session,a)
    Service.configure(db_session,a,body('merchant-new','synthetic-new',1))
    with pytest.raises(HTTPException) as rejected:BackupService.restore_backup(db_session,payload,a)
    assert rejected.value.status_code==409
    assert Service.resolve(db_session,a.tenant_id)['client_id']=='merchant-new'


def test_forged_metadata_or_plaintext_cannot_enter_backup(db_session,shops):
    a=shops['a'][0];Service.configure(db_session,a,body());payload=snapshot(db_session,a)
    payload['data']['tenants'][0]['paypal_client_secret']='forged-secret'
    payload['checksum_sha256']=digest(payload['data'])
    with pytest.raises(HTTPException) as rejected:BackupService.restore_backup(db_session,payload,a)
    assert rejected.value.status_code==400


def test_legacy_backup_export_requires_explicit_migration(db_session,shops):
    from app.models import Tenant
    a=shops['a'][0];db_session.get(Tenant,a.tenant_id).paypal_client_secret='legacy-private-value';db_session.commit()
    with pytest.raises(HTTPException) as rejected:snapshot(db_session,a)
    assert rejected.value.status_code==409
