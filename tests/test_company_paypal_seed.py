import pytest
from sqlalchemy import select,text
from app.models.company_paypal_credential import CompanyPayPalCredential as Credential
from app.models import Tenant
from app.services.company_paypal_seed import seed_company_paypal,validate_seed_config
from app.services.company_paypal_service import CompanyPayPalService as Service
from scripts.migrate_company_paypal import migrate_connection
from tests.test_company_paypal_config import payment_key,shops,body


def config(slug):return {slug:{'client_id':'merchant-seed','client_secret':'synthetic-seed-secret','mode':'sandbox'}}

def test_seed_is_repeatable_preserves_owner_changes_and_does_not_claim_oauth(db_session,shops):
    a=shops['a'][0];slug=db_session.get(Tenant,a.tenant_id).slug;values=config(slug)
    seed_company_paypal(db_session.connection(),values);db_session.commit()
    original=[dict(r) for r in db_session.connection().execute(select(Credential.__table__)).mappings()]
    seed_company_paypal(db_session.connection(),values)
    assert [dict(r) for r in db_session.connection().execute(select(Credential.__table__)).mappings()]==original
    assert Service.state(db_session,a)['status']=='configured_unverified'
    Service.configure(db_session,a,body('owner-edited','owner-private-synthetic',1))
    seed_company_paypal(db_session.connection(),values)
    assert Service.resolve(db_session,a.tenant_id)['client_id']=='owner-edited'
    Service.disable(db_session,a,2);seed_company_paypal(db_session.connection(),values)
    assert not Service.availability(db_session,a)['enabled']


def test_legacy_migration_encrypts_and_repeats_without_changing_history(db_session,shops):
    a=shops['a'][0];tenant=db_session.get(Tenant,a.tenant_id)
    tenant.paypal_client_id='merchant-legacy';tenant.paypal_client_secret='legacy-synthetic-private';tenant.paypal_mode='sandbox';db_session.commit()
    assert migrate_connection(db_session.connection())['legacy_companies_encrypted']==1
    db_session.commit();db_session.refresh(tenant)
    assert tenant.paypal_client_secret is None
    before=db_session.query(Credential).one().secret_ciphertext
    assert migrate_connection(db_session.connection())['legacy_companies_encrypted']==0
    assert db_session.query(Credential).one().secret_ciphertext==before
    assert Service.resolve(db_session,a.tenant_id)['client_id']=='merchant-legacy'


@pytest.mark.parametrize('value',[[],{'a':{'client_id':'x'}},{'a':{'client_id':'merchant','client_secret':'secret','mode':'live'}}])
def test_invalid_demo_configuration_is_rejected_before_reset(value):
    with pytest.raises(ValueError):validate_seed_config(value)


def test_legacy_invalid_pair_rolls_back_without_clearing_source(db_session,shops):
    a=shops['a'][0];tenant=db_session.get(Tenant,a.tenant_id);tenant.paypal_client_id='merchant-incomplete';tenant.paypal_client_secret=None;db_session.commit()
    with pytest.raises(Exception),db_session.begin_nested():migrate_connection(db_session.connection())
    db_session.expire_all();assert db_session.get(Tenant,a.tenant_id).paypal_client_id=='merchant-incomplete'
    assert db_session.query(Credential).count()==0
