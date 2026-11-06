import copy,json
import pytest
from fastapi import HTTPException
from app.core.tenant_scope import bind_tenant
from app.models import Tenant,SubscriptionTerm,SubscriptionPlanVersion
from app.services.backup_service import BackupService,json_serializer
from app.services.backup_recovery import digest
from tests.test_saas_subscription_contracts import contract_setup,accept,decide
from tests.support_saas import saas_actors,headers
from tests.test_saas_plan_catalog import create_plan,draft,transition
from tests.test_tenant_sales import shops


def snapshot(db,actor):
    bind_tenant(db,actor.tenant_id,actor.id)
    return json.loads(json.dumps(BackupService.create_backup(db,actor),default=json_serializer))


@pytest.fixture
def future_plan(client,db_session,contract_setup):
    setup=contract_setup
    plan=create_plan(client,setup['operator'],'S6_FUTURE')
    version=draft(client,setup['operator'],plan,precio_mensual_bob='333.00')
    assert transition(client,setup['operator'],version,'publish').status_code==200
    quoted=client.post('/api/v1/subscriptions/quote',headers=headers(setup['admin']),json={'version_id':version['id'],'kind':'RENOVACION'})
    assert quoted.status_code==200,quoted.text
    request,_=accept(client,setup,quoted.json());assert decide(client,setup,request).status_code==200
    return setup,plan,version


def test_company_snapshot_references_future_plan_and_versions_without_owning_them(client,db_session,future_plan):
    setup,plan,version=future_plan;actor=setup['admin'];payload=snapshot(db_session,actor)
    assert payload['version']=='4.6.0'
    assert str(plan) in {row['id'] for row in payload['data']['reference_plans']}
    assert version['id'] in {row['id'] for row in payload['data']['reference_plan_versions']}
    assert 'subscription_plan_versions' not in payload['data'] and 'platform_audit_logs' not in payload['data']
    terms=[(t.id,t.precio_mensual_bob) for t in db_session.query(SubscriptionTerm).all()]
    BackupService.restore_backup(db_session,payload,actor)
    assert [(t.id,t.precio_mensual_bob) for t in db_session.query(SubscriptionTerm).all()]==terms


def test_forged_catalog_is_rejected_before_any_company_mutation(client,db_session,future_plan):
    setup,_,_=future_plan;actor=setup['admin'];payload=snapshot(db_session,actor)
    original=actor.tenant.nombre;payload['data']['tenants'][0]['nombre']='Must not persist'
    payload['data']['reference_plan_versions'][0]['precio_mensual_bob']=1
    payload['checksum_sha256']=digest(payload['data'])
    with pytest.raises(HTTPException) as rejected:BackupService.restore_backup(db_session,payload,actor)
    assert rejected.value.status_code==409
    assert db_session.get(Tenant,actor.tenant_id).nombre==original


@pytest.mark.parametrize('extra',['platform_audit_logs','subscription_plan_versions'])
def test_company_import_never_imports_platform_tables(db_session,shops,extra):
    actor=shops['a'][0];payload=snapshot(db_session,actor);payload['data'][extra]=[];payload['checksum_sha256']=digest(payload['data'])
    with pytest.raises(HTTPException) as rejected:BackupService.restore_backup(db_session,payload,actor)
    assert rejected.value.status_code==400


def test_previous_snapshot_format_gives_clear_error_without_partial_restore(db_session,shops):
    actor=shops['a'][0];payload=snapshot(db_session,actor);original=actor.tenant.nombre
    payload['version']='4.3.0';payload['data'].pop('reference_plan_versions')
    payload['data']['tenants'][0]['nombre']='Old format must not persist';payload['checksum_sha256']=digest(payload['data'])
    with pytest.raises(HTTPException) as rejected:BackupService.restore_backup(db_session,payload,actor)
    assert rejected.value.status_code==409 and 'Formato incompatible' in rejected.value.detail
    assert db_session.get(Tenant,actor.tenant_id).nombre==original


def test_changed_wallet_key_rolls_back_company_and_preserves_central_contract(db_session,shops,monkeypatch):
    from app.services.wallet_service import WalletService
    from app.config import settings
    actor=shops['a'][0];WalletService.topup_cash_admin(db_session,actor,actor.id,25)
    payload=snapshot(db_session,actor);original=actor.tenant.nombre
    payload['data']['tenants'][0]['nombre']='Must rollback with invalid signature';payload['checksum_sha256']=digest(payload['data'])
    monkeypatch.setattr(settings,'WALLET_SIGNING_KEY','different-private-synthetic-key-for-s6-only')
    with pytest.raises(HTTPException) as rejected:BackupService.restore_backup(db_session,payload,actor)
    assert rejected.value.status_code==409 and 'firma' in rejected.value.detail
    assert db_session.get(Tenant,actor.tenant_id).nombre==original
