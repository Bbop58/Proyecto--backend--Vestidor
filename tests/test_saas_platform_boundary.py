"""API regression: platform identity cannot become a commercial administrator."""
import pytest
import uuid
from datetime import date, timedelta
from fastapi import HTTPException
from sqlalchemy import select
from app.config import settings
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User
from app.models.platform_audit_log import PlatformAuditLog
from app.models.subscription import Subscription
from app.models.subscription_plan import SubscriptionPlan
from app.services.platform_audit_service import PlatformAuditService
from tests.support_saas import saas_actors, headers


@pytest.mark.parametrize('path', [
    '/users', '/roles', '/permissions', '/productos', '/categorias', '/sucursales',
    '/proveedores', '/temporadas', '/inventario/movimientos', '/inventario/alertas',
    '/reservas', '/reservas/mis-reservas', '/ventas', '/ventas/mis-compras',
    '/ventas/reportes/top-productos', '/wallet/me', '/wallet/history', '/wallet/admin/list',
    '/notifications', '/favorites', '/recommendations/trending', '/backups/stats', '/backups/download',
    '/analytics/forecast', '/analytics/segments', '/analytics/recommendations', '/analytics/recommendations/report-data', '/analytics/recommendations/export.xlsx',
])
def test_platform_operator_denied_business_even_with_admin_permissions(client, saas_actors, path):
    response = client.get('/api/v1' + path, headers=headers(saas_actors['operator']))
    assert response.status_code == 403, (path, response.status_code)


def test_platform_operator_cannot_train_commercial_models(client, saas_actors):
    response = client.post('/api/v1/analytics/train', headers=headers(saas_actors['operator']))
    assert response.status_code == 403


def test_platform_profile_is_identity_only(client, saas_actors):
    response = client.get('/api/v1/users/me', headers=headers(saas_actors['operator']))
    assert response.status_code == 200
    data = response.json()
    assert data.get('access_scope') == 'PLATFORM'
    assert data['is_platform_admin'] is True
    assert data['permissions'] == []
    assert 'hashed_password' not in data


def test_revoked_operator_does_not_fall_back_to_commercial_admin(client, saas_actors):
    for path in ['/users', '/ventas', '/users/me', '/platform/companies']:
        response = client.get('/api/v1' + path, headers=headers(saas_actors['revoked']))
        assert response.status_code == 403, (path, response.status_code)


def test_company_admin_cannot_enter_platform(client, saas_actors):
    response = client.get('/api/v1/platform/companies', headers=headers(saas_actors['admin']))
    assert response.status_code == 403


def test_operator_company_is_not_in_public_directory(client, saas_actors):
    response = client.get('/api/v1/subscriptions/public/tenants')
    assert response.status_code == 200
    assert str(saas_actors['operator_company'].id) not in {r['id'] for r in response.json()}


def test_all_commercial_routes_reject_operator_before_validation_or_provider_call(client, saas_actors):
    exempt_prefixes=('/api/v1/auth/', '/api/v1/platform/', '/api/v1/subscriptions/admin/')
    exempt_exact={'/api/v1/users/me','/api/v1/users/me/change-password',
        '/api/v1/subscriptions/public/tenants','/api/v1/subscriptions/plans','/api/v1/subscriptions/onboarding',
        '/api/v1/payments/paypal/return','/api/v1/payments/paypal/cancel','/api/v1/subscriptions/paypal/webhook'}
    results=[]
    import re
    for path, operations in app.openapi()['paths'].items():
        if not path.startswith('/api/v1/') or path in exempt_exact or path.startswith(exempt_prefixes):continue
        if path.endswith('/openapi.json'):continue
        concrete=re.sub(r'\{[^}]+\}',str(uuid.UUID(int=0)),path)
        for method in sorted({m.upper() for m in operations} & {'GET','POST','PUT','PATCH','DELETE'}):
            response=client.request(method,concrete,headers=headers(saas_actors['operator']),json={})
            results.append((method,path,response.status_code))
    assert len(results)>=120
    assert not [r for r in results if r[2]!=403],results


def test_company_admin_access_and_cross_company_boundary_remain(client, saas_actors):
    own=headers(saas_actors['admin'])
    for path in ['/users','/productos','/inventario/movimientos','/ventas','/backups/stats']:
        assert client.get('/api/v1'+path,headers=own).status_code==200
    assert client.get('/api/v1/users/'+str(saas_actors['other'].id),headers=own).status_code==404


def test_platform_response_has_only_allowed_fields(client, saas_actors):
    response=client.get('/api/v1/platform/companies',headers=headers(saas_actors['operator']))
    assert response.status_code==200
    data=response.json()
    assert data['total']==2
    assert {r['id'] for r in data['companies']}=={str(saas_actors['a_company'].id),str(saas_actors['b_company'].id)}
    expected={'id','nombre','razon_social','nit_rut','slug','email_contacto','telefono',
              'activo','platform_revision','created_at','sucursales_activas'}
    assert all(set(row)==expected for row in data['companies'])


def _status(active=True,version=0):
    return {'operation_id':str(uuid.uuid4()),'activo':active,'expected_version':version,'reason':'Administrative test'}


def test_suspend_retry_keeps_same_state_and_does_not_extend_subscription(client,db_session,saas_actors):
    company=saas_actors['a_company'];company_id=company.id
    plan=SubscriptionPlan(codigo='SAAS-TEST',nombre='Test',max_sucursales=2,precio_mensual_bob=100)
    db_session.add(plan);db_session.flush()
    end=date.today()+timedelta(days=15)
    sub=Subscription(tenant_id=company_id,plan_id=plan.id,fecha_vencimiento=end)
    db_session.add(sub);db_session.commit()
    req=_status(False)
    url=f'/api/v1/platform/companies/{company_id}/status';auth=headers(saas_actors['operator'])
    first=client.put(url,headers=auth,json=req)
    assert first.status_code==200,first.text
    again=client.put(url,headers=auth,json=req)
    assert again.status_code==200 and again.json()==first.json()
    assert first.json()['activo'] is False and first.json()['platform_revision']==1
    assert db_session.query(PlatformAuditLog).filter_by(operation_id=req['operation_id']).count()==1
    assert db_session.get(Subscription,sub.id).fecha_vencimiento==end
    stale=client.put(url,headers=auth,json=_status(True,0))
    assert stale.status_code==409
    reactivate=client.put(url,headers=auth,json=_status(True,1))
    assert reactivate.status_code==200 and reactivate.json()['activo'] is True
    assert db_session.get(Subscription,sub.id).fecha_vencimiento==end


def test_platform_cannot_suspend_operator_company_or_reuse_key_for_another_payload(client,saas_actors):
    auth=headers(saas_actors['operator']);req=_status(False)
    assert client.put(f"/api/v1/platform/companies/{saas_actors['operator_company'].id}/status",headers=auth,json=req).status_code==404
    url=f"/api/v1/platform/companies/{saas_actors['a_company'].id}/status"
    assert client.put(url,headers=auth,json=req).status_code==200
    req['activo']=True
    assert client.put(url,headers=auth,json=req).status_code==409


def test_platform_create_is_metadata_only_and_rejects_company_credentials(client,db_session,saas_actors):
    auth=headers(saas_actors['operator'])
    payload={'operation_id':str(uuid.uuid4()),'nombre':'New company','razon_social':'Company Test',
             'nit_rut':'12345','slug':'new-company','reason':'Create metadata only'}
    for key in ['admin_password','paypal_client_secret','es_operadora']:
        invalid={**payload,key:'not-allowed'}
        assert client.post('/api/v1/platform/companies',headers=auth,json=invalid).status_code==422
    response=client.post('/api/v1/platform/companies',headers=auth,json=payload)
    assert response.status_code==201,response.text
    company_id=response.json()['id']
    again=client.post('/api/v1/platform/companies',headers=auth,json=payload)
    assert again.status_code==201 and again.json()==response.json()
    assert response.json()['activo'] is False
    assert db_session.query(User).filter_by(tenant_id=company_id).count()==0
    assert db_session.query(Subscription).filter_by(tenant_id=company_id).count()==0


def test_audit_failure_rolls_back_company_change(client,db_session,saas_actors,monkeypatch):
    def fail(*args,**kwargs):raise HTTPException(503,'Injected audit failure')
    monkeypatch.setattr(PlatformAuditService,'record',fail)
    company=saas_actors['a_company'];company_id=company.id
    response=client.put(f'/api/v1/platform/companies/{company_id}/status',
        headers=headers(saas_actors['operator']),json=_status(False))
    assert response.status_code==503
    db_session.expire_all()
    assert db_session.get(Tenant,company_id).activo is True
    assert db_session.get(Tenant,company_id).platform_revision==0


def test_uuid_grant_in_wrong_company_is_not_platform_or_business_access(client,saas_actors,monkeypatch):
    monkeypatch.setattr(settings,'PLATFORM_ADMIN_USER_IDS',[str(saas_actors['admin'].id)])
    for path in ['/platform/companies','/users','/users/me']:
        assert client.get('/api/v1'+path,headers=headers(saas_actors['admin'])).status_code==403


def test_company_admin_cannot_create_reserved_role_or_register_operator(client,saas_actors):
    auth=headers(saas_actors['admin'])
    assert client.post('/api/v1/roles',headers=auth,json={'name':'superadmin_plataforma'}).status_code==403
    response=client.post('/api/v1/auth/register',json={'email':'new@test.example.com',
        'password':'OnlyRegistration123!','full_name':'Untrusted operator',
        'tenant_id':str(saas_actors['operator_company'].id)})
    assert response.status_code==403


def test_operator_login_refresh_profile_password_and_revocation(client,saas_actors,monkeypatch):
    actor=saas_actors['operator']
    response=client.post('/api/v1/auth/login',json={'email':actor.email,
        'password':'SaaSOnlyTest123!','tenant_id':str(actor.tenant_id)})
    assert response.status_code==200,response.text
    pair=response.json()
    assert pair['user']['access_scope']=='PLATFORM' and pair['user']['permissions']==[]
    auth={'Authorization':'Bearer '+pair['access_token']}
    updated=client.put('/api/v1/users/me',headers=auth,json={'full_name':'Operator renamed'})
    assert updated.status_code==200,updated.text
    assert updated.json()['access_scope']=='PLATFORM' and updated.json()['permissions']==[]
    refresh=client.post('/api/v1/auth/refresh',json={'refresh_token':pair['refresh_token']})
    assert refresh.status_code==200,refresh.text
    password=client.post('/api/v1/auth/change-password',headers=auth,
        json={'current_password':'SaaSOnlyTest123!','new_password':'SaaSChangedOnly123!'})
    assert password.status_code==200,password.text
    monkeypatch.setattr(settings,'PLATFORM_ADMIN_USER_IDS',[])
    assert client.get('/api/v1/platform/companies',headers=auth).status_code==403
    assert client.get('/api/v1/ventas',headers=auth).status_code==403
    assert client.post('/api/v1/auth/refresh',json={'refresh_token':refresh.json()['refresh_token']}).status_code==403


def test_platform_logout_invalidates_access_token(client,saas_actors):
    actor=saas_actors['operator'];auth=headers(actor)
    result=client.post('/api/v1/auth/logout',headers=auth,json={})
    assert result.status_code==200,result.text
    assert client.get('/api/v1/users/me',headers=auth).status_code==401
