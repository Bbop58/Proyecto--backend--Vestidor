"""Maintenance gates: no implicit remote writes, no grant by email or empty role."""
import uuid
import pytest
from sqlalchemy import text
from app.saas_integrity import verify_counts
from scripts.migrate_saas import validate_target
from tests.test_tenant_sales import shops

@pytest.mark.parametrize('url',[
 'postgresql://audit@remote.example/fashionstore_test_demo',
 'postgresql://audit@localhost/real_company',
 'postgresql://audit@localhost/postgres',
 'postgresql://audit@localhost/fashionstore_test_demo?host=remote.example',
 'sqlite:///fashionstore_test_demo',
])
def test_maintenance_refuses_implicit_destination(url):
    with pytest.raises(ValueError):validate_target(url)


def test_readonly_verifier_rejects_unknown_grant_and_null_operator_role_owner(db_session,shops):
    from app.models import Tenant,User
    actor=shops['a'][0]
    tenant_id=actor.tenant_id
    before=db_session.query(User).count()
    assert verify_counts(db_session.connection(),[uuid.uuid4()])['configured_operator_invalid']==1
    assert db_session.query(User).count()==before
    # Reserved role without a tenant owner must fail even with SQL NULL ownership.
    conn=db_session.connection()
    conn.execute(text('UPDATE tenants SET es_operadora=true WHERE id=:id'),{'id':tenant_id})
    conn.execute(text("UPDATE roles SET name='superadmin_plataforma',tenant_id=NULL WHERE id=:id"),{'id':actor.role_id})
    assert verify_counts(conn)['operator_context']>=1


def test_nonlocal_guard_does_not_remove_postgres_or_query_restrictions():
    assert validate_target('postgresql://audit@remote.example/demo',True).database=='demo'
    with pytest.raises(ValueError):validate_target('postgresql://audit@localhost/postgres',True)
    with pytest.raises(ValueError):validate_target('postgresql://audit@localhost/demo?host=remote.example',True)
