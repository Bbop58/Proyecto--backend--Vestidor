"""Isolated SaaS actors; never uses the hosted demo or the phone's account."""
import pytest
from functools import lru_cache
from app.config import settings
from app.core.security import create_access_token, get_password_hash
from app.models.tenant import Tenant
from app.models.role import Role
from app.models.user import User
from app.seed_permissions import seed


@lru_cache(maxsize=1)
def _test_password_hash():
    return get_password_hash('SaaSOnlyTest123!')


def headers(user):
    token = create_access_token(subject=str(user.id), tenant_id=user.tenant_id)['token']
    return {'Authorization': 'Bearer ' + token}


@pytest.fixture
def saas_actors(db_session, monkeypatch):
    db = db_session
    seed(db)
    admin = db.query(Role).filter_by(name='admin').one()
    cashier = db.query(Role).filter_by(name='cajero').one()
    customer = db.query(Role).filter_by(name='cliente').one()
    result = {}
    for key in ['operator', 'a', 'b']:
        company = Tenant(nombre=key, razon_social=key, nit_rut='saas-' + key,
                         slug='saas-' + key, activo=True)
        if key == 'operator':
            company.es_operadora = True
        db.add(company)
        db.flush()
        result[key + '_company'] = company
    reserved = Role(tenant_id=result['operator_company'].id,
                    name='superadmin_plataforma', description='Reserved SaaS role')
    # Deliberately retain commercial permissions to reproduce privilege inheritance.
    reserved.permissions = list(admin.permissions)
    db.add(reserved)
    db.flush()
    for key, company, role in [('operator', 'operator', reserved), ('revoked', 'operator', reserved),
                                ('admin', 'a', admin), ('cashier', 'a', cashier),
                                ('customer', 'a', customer), ('other', 'b', admin)]:
        actor = User(tenant_id=result[company + '_company'].id, role_id=role.id,
                     email=f'{key}@saas-test.example.com', full_name='SaaS ' + key,
                     hashed_password=_test_password_hash(),
                     is_active=True, is_verified=True)
        db.add(actor)
        result[key] = actor
    db.commit()
    monkeypatch.setattr(settings, 'PLATFORM_ADMIN_USER_IDS', [str(result['operator'].id)])
    return result
