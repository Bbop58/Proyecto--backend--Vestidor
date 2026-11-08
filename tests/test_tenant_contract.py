import uuid
import pytest
from fastapi import HTTPException
from app.core.security import create_access_token
from app.api.deps import get_current_tenant
from app.models.tenant import Tenant
from app.models.user import User
from tests.test_tenant_sales import shops


def headers(user):
    return {'Authorization':'Bearer '+create_access_token(str(user.id),tenant_id=user.tenant_id)['token']}


def test_registration_requires_company_and_never_grants_admin(client,db_session,shops):
    a,_,_=shops['a']
    data={'email':'new@example.com','password':'TestPassword123!','full_name':'New Customer'}
    assert client.post('/api/v1/auth/register',json=data).status_code==422
    response=client.post('/api/v1/auth/register',json={**data,'tenant_id':str(a.tenant_id),'role_id':str(a.role_id)})
    assert response.status_code==201,response.text
    assert response.json()['user']['tenant_id']==str(a.tenant_id)
    assert response.json()['user']['role']['name']=='cliente'


def test_no_automatic_company_for_unassigned_account(db_session,shops):
    a,_,_=shops['a'];a.tenant_id=None;db_session.commit()
    before=db_session.query(Tenant).count()
    with pytest.raises(HTTPException) as exc:get_current_tenant(a,db_session)
    assert exc.value.status_code==403
    assert db_session.query(Tenant).count()==before


@pytest.mark.parametrize('resource',['user','branch','product','variant','inventory','wallet'])
def test_real_session_cannot_read_or_write_foreign_resources(client,db_session,shops,resource):
    a,va,ba=shops['a'];b,vb,bb=shops['b']
    from app.models.inventory import Inventory
    inv=db_session.query(Inventory).filter_by(sucursal_id=bb[0].id,variante_id=vb.id).one()
    paths={
        'user':('get',f'/api/v1/users/{b.id}',None),
        'branch':('put',f'/api/v1/sucursales/{bb[0].id}',{'nombre':'Intrusion'}),
        'product':('put',f'/api/v1/productos/{vb.producto_id}',{'nombre':'Intrusion'}),
        'variant':('put',f'/api/v1/variantes/{vb.id}',{'color':'Intrusion'}),
        'inventory':('put',f'/api/v1/inventario/{inv.id}',{'stock_minimo':999}),
        'wallet':('post','/api/v1/wallet/admin/topup-cash',{'cliente_id':str(b.id),'monto_bs':10})}
    method,path,payload=paths[resource]
    response=client.request(method.upper(),path,headers=headers(a),**({'json':payload} if payload else {}))
    assert response.status_code in (403,404),response.text


def test_first_public_account_is_customer(client,db_session):
    tenant=Tenant(nombre='Store',razon_social='Store',slug='first',nit_rut='first',activo=True)
    db_session.add(tenant);db_session.commit()
    response=client.post('/api/v1/auth/register',json={'tenant_id':str(tenant.id),
        'email':'first@example.com','full_name':'First Account','password':'TestPassword123!'})
    assert response.status_code==201,response.text
    assert response.json()['user']['role']['name']=='cliente'


def test_customer_cannot_promote_own_role(client,db_session,shops):
    a,_,_=shops['a']
    from app.models.role import Role
    admin_role=a.role_id
    a.role_id=db_session.query(Role).filter_by(name='cliente').one().id
    db_session.commit()
    response=client.put('/api/v1/users/me',headers=headers(a),json={'role_id':str(admin_role)})
    assert response.status_code==403,response.text
    db_session.refresh(a)
    assert a.role_id != admin_role


def test_indirect_foreign_category_is_rejected_atomically(client,db_session,shops):
    a,va,_=shops['a'];_,vb,_=shops['b']
    before=va.producto.categoria_id
    response=client.put(f'/api/v1/productos/{va.producto_id}',headers=headers(a),
        json={'categoria_id':str(vb.producto.categoria_id),'nombre':'Must not persist'})
    assert response.status_code in (400,403,404),response.text
    db_session.refresh(va.producto)
    assert va.producto.categoria_id==before
    assert va.producto.nombre=='Product a'


def test_shared_roles_are_read_only_and_custom_roles_are_owned(client,db_session,shops):
    a,_,_=shops['a'];b,_,_=shops['b']
    response=client.put(f'/api/v1/roles/{a.role_id}/permissions',headers=headers(a),json={'permission_ids':[]})
    assert response.status_code==403,response.text
    created=client.post('/api/v1/roles',headers=headers(a),json={'name':'Own Staff','description':'A only'})
    assert created.status_code==201,created.text
    foreign=client.put(f'/api/v1/roles/{created.json()["id"]}',headers=headers(b),json={'description':'Intrusion'})
    assert foreign.status_code==404,foreign.text


def test_session_bound_to_original_company_and_inactive_company(client,db_session,shops):
    a,_,_=shops['a'];b,_,_=shops['b']
    from app.core.security import create_access_token
    wrong=create_access_token(str(a.id),tenant_id=b.tenant_id)['token']
    assert client.get('/api/v1/users/me',headers={'Authorization':'Bearer '+wrong}).status_code==401
    tenant=db_session.get(Tenant,a.tenant_id);tenant.activo=False;db_session.commit()
    assert client.get('/api/v1/users/me',headers=headers(a)).status_code==403


def test_email_never_grants_platform_privileges(client,db_session,shops):
    a,_,_=shops['a'];a.email='admin@sistema.com';db_session.commit()
    response=client.get('/api/v1/subscriptions/admin/tenants-overview',headers=headers(a))
    assert response.status_code==403


def test_customer_report_and_plan_changes_are_forbidden(client,db_session,shops):
    a,_,_=shops['a']
    from app.models.role import Role
    a.role_id=db_session.query(Role).filter_by(name='cliente').one().id;db_session.commit()
    assert client.post('/api/v1/reportes-personalizados/generate',headers=headers(a),json={}).status_code==403
    assert client.post('/api/v1/subscriptions/change-plan',headers=headers(a),json={'plan_codigo':'PROFESIONAL'}).status_code==403


def test_lists_and_backup_include_only_own_company(client,db_session,shops):
    a,_,_=shops['a'];b,_,_=shops['b']
    response=client.get('/api/v1/users',headers=headers(a))
    assert response.status_code==200,response.text
    assert {u['id'] for u in response.json()}=={str(a.id)}
    backup=client.get('/api/v1/backups/download',headers=headers(a))
    assert backup.status_code==200,backup.text
    data=backup.json()['data']
    assert all(u['id']!=str(b.id) for u in data['users'])
    assert all(t['id']==str(a.tenant_id) for t in data['tenants'])


def test_user_and_wallet_foreign_ids_are_not_written(client,db_session,shops):
    a,_,_=shops['a'];b,_,_=shops['b']
    from app.models.user_wallet import UserWallet
    response=client.post('/api/v1/wallet/admin/topup-cash',headers=headers(a),json={'cliente_id':str(b.id),'monto_bs':10})
    assert response.status_code==404,response.text
    assert db_session.query(UserWallet).count()==0


def test_voice_sql_report_is_tenant_scoped(client,db_session,shops):
    from tests.test_tenant_sales import payload
    from app.core.tenant_scope import bind_tenant
    from app.services.voice_report_service import VoiceReportService
    a,va,ba=shops['a'];b,vb,bb=shops['b']
    for user,variant,branch in [(a,va,ba[0]),(b,vb,bb[0])]:
        response=client.post('/api/v1/ventas/presencial',headers=headers(user),json=payload(branch,variant))
        assert response.status_code==201,response.text
    bind_tenant(db_session,a.tenant_id,a.id)
    report=VoiceReportService.execute_voice_report(db_session,'Ventas totales agrupadas por sucursal')
    rows=report.filas
    assert rows
    assert {row['sucursal'] for row in rows}<={'a0','a1'}
    assert sum(row['total_recaudado_bs'] for row in rows)==10


def test_voice_sql_cannot_bypass_scope_with_schema(client,db_session,shops,monkeypatch):
    from app.core.tenant_scope import bind_tenant
    from app.services.voice_report_service import VoiceReportService
    a,_,_=shops['a'];bind_tenant(db_session,a.tenant_id,a.id)
    monkeypatch.setattr(VoiceReportService,'_generate_sql_with_gemini',lambda *args,**kwargs:('SELECT email FROM public.users','audit'))
    with pytest.raises(HTTPException) as exc:
        VoiceReportService.execute_voice_report(db_session,'Listar usuarios')
    assert exc.value.status_code==400


def test_column_queries_and_aliases_are_isolated(db_session,shops):
    from sqlalchemy.orm import aliased
    from app.core.tenant_scope import bind_tenant
    a,_,_=shops['a'];b,_,_=shops['b'];bind_tenant(db_session,a.tenant_id,a.id)
    alias=aliased(User)
    assert db_session.query(alias.id).filter(alias.id==b.id).first() is None
    assert {row[0] for row in db_session.query(User.id).all()}=={a.id}


def test_unassigned_and_inactive_registration_do_not_create_accounts(client,db_session,shops):
    a,_,_=shops['a'];tenant=db_session.get(Tenant,a.tenant_id);tenant.activo=False;db_session.commit()
    before=db_session.query(User).count()
    for tenant_id in (tenant.id,uuid.uuid4()):
        response=client.post('/api/v1/auth/register',json={'tenant_id':str(tenant_id),'email':'inactive@example.com',
            'full_name':'Inactive Customer','password':'Password123!'})
        assert response.status_code==404,response.text
    assert db_session.query(User).count()==before


def test_exchange_receipt_requires_identity_and_company(client,db_session,shops):
    from app.models.sale import Sale
    from app.models.product_exchange import ProductExchange
    a,_,_=shops['a'];b,_,bb=shops['b']
    sale=Sale(tenant_id=b.tenant_id,numero_recibo='PRIVATE-EXCHANGE',sucursal_id=bb[0].id,cliente_id=b.id)
    db_session.add(sale);db_session.flush()
    exchange=ProductExchange(tenant_id=b.tenant_id,numero_cambio='PRIVATE-EXCHANGE',venta_id=sale.id,
        sucursal_id=bb[0].id,cliente_id=b.id,cajero_id=b.id,motivo='Cambio de talla')
    db_session.add(exchange);db_session.commit()
    path=f'/api/v1/cambios/{exchange.id}/nota-devolucion'
    assert client.get(path,headers=headers(b)).status_code==200
    assert client.get(path).status_code==401
    assert client.get(path,headers=headers(a)).status_code==404


def test_sync_collision_cannot_replay_another_company_result(client,db_session,shops):
    from datetime import datetime,timezone
    from app.models.sale import Sale
    a,va,ba=shops['a'];b,vb,bb=shops['b'];operation_id=str(uuid.uuid4())
    def operation(branch,variant):
        return {'operations':[{'client_operation_id':operation_id,'operation_type':'VENTA_CONTADO_OFFLINE',
            'sucursal_id':str(branch.id),'fecha_local':datetime.now(timezone.utc).isoformat(),
            'items':[{'variante_id':str(variant.id),'cantidad':1,'precio_unitario':10}],
            'monto_total':10,'monto_efectivo_recibido':10,'cambio_entregado':0,'metodo_pago':'EFECTIVO'}]}
    first=client.post('/api/v1/sync/batch',headers=headers(b),json=operation(bb[0],vb))
    assert first.status_code==200,first.text
    assert first.json()['total_confirmed']==1,first.text
    second=client.post('/api/v1/sync/batch',headers=headers(a),json=operation(ba[0],va))
    assert second.status_code==200,second.text
    result=second.json()['results'][0]
    assert result['status']=='CONFLICTO'
    assert result.get('receipt_number') is None
    assert result.get('server_entity_id') is None
    assert db_session.query(Sale).filter_by(tenant_id=a.tenant_id).count()==0


def test_subscription_query_does_not_provision_a_free_plan(client,db_session,shops):
    from app.models.subscription import Subscription
    a,_,_=shops['a']
    before=db_session.query(Subscription).count()
    response=client.get('/api/v1/subscriptions/current',headers=headers(a))
    assert response.status_code==404,response.text
    assert db_session.query(Subscription).count()==before


def test_optional_identity_never_accepts_revoked_or_expired_tokens(client,db_session,shops,monkeypatch):
    from datetime import timedelta
    from app.services.chatbot_service import ChatbotService
    def forbidden(*args,**kwargs):pytest.fail('Invalid session reached the AI provider')
    monkeypatch.setattr(ChatbotService,'process_message',forbidden)
    a,_,_=shops['a'];auth=headers(a)
    assert client.post('/api/v1/auth/logout',headers=auth,json={}).status_code==200
    assert client.post('/api/v1/chatbot/message',headers=auth,json={'message':'Hola'}).status_code==401
    expired=create_access_token(str(a.id),expires_delta=timedelta(seconds=-1),tenant_id=a.tenant_id)['token']
    assert client.get('/api/v1/users/me',headers={'Authorization':'Bearer '+expired}).status_code==401


def test_private_routes_have_authentication_dependencies():
    from app.main import app
    from fastapi.routing import APIRoute
    public={'/auth/register','/auth/login','/auth/login/swagger','/auth/refresh',
            # Like legacy refresh, these authenticate a body credential instead
            # of requiring an unexpired access JWT. No commercial-route wildcard.
            '/auth/mobile/refresh','/auth/mobile/logout',
            '/payments/paypal/return','/payments/paypal/cancel','/subscriptions/paypal/webhook',
            '/subscriptions/public/tenants','/subscriptions/plans','/subscriptions/onboarding','/health','/health/ready','/'}
    def dependencies(dep):
        names={getattr(dep.call,'__name__','')}
        for child in dep.dependencies:names |= dependencies(child)
        return names
    def routes(router):
        for route in router.routes:
            if hasattr(route,'original_router'):yield from routes(route.original_router)
            elif isinstance(route,APIRoute):yield route
    for route in routes(app):
        if route.path not in public:
            assert dependencies(route.dependant) & {'get_current_user','get_current_user_optional','get_current_token_payload'},route.path


def test_public_company_directory_never_exposes_provider_secrets(client,db_session,shops):
    a,_,_=shops['a'];tenant=db_session.get(Tenant,a.tenant_id)
    tenant.paypal_client_secret='audit-dummy-not-a-credential';db_session.commit()
    response=client.get('/api/v1/subscriptions/public/tenants')
    assert response.status_code==200,response.text
    assert all('paypal_client_secret' not in row for row in response.json())
    assert 'audit-dummy-not-a-credential' not in response.text


def test_platform_uuid_alone_cannot_grant_platform_or_commercial_fallback(client,db_session,shops,monkeypatch):
    from app.config import settings
    a,_,_=shops['a']
    monkeypatch.setattr(settings,'PLATFORM_ADMIN_USER_IDS',[str(a.id)])
    response=client.get('/api/v1/users/me',headers=headers(a))
    # S1 supersedes the old mixed admin/platform contract: UUID alone is not
    # sufficient without the reserved role and the operator company.
    assert response.status_code==403,response.text
    overview=client.get('/api/v1/subscriptions/admin/tenants-overview',headers=headers(a))
    assert overview.status_code==403,overview.text
    from tests.test_tenant_sales import payload
    _,variant,branches=shops['b']
    purchase=client.post('/api/v1/ventas/digital',headers=headers(a),json=payload(branches[0],variant,True))
    assert purchase.status_code==403,purchase.text


def test_public_or_short_jwt_keys_are_rejected():
    from app.config import Settings
    for key in ('short','supersecretkey-change-this-in-production-long-random-string',
                'REPLACE_WITH_A_PRIVATE_RANDOM_KEY_OF_AT_LEAST_32_CHARACTERS'):
        with pytest.raises(ValueError):Settings(SECRET_KEY=key)


def test_company_onboarding_requires_a_real_admin_password(client,db_session):
    before=db_session.query(Tenant).count()
    response=client.post('/api/v1/subscriptions/onboarding',json={'nombre':'New Store','razon_social':'New Store SRL',
        'nit_rut':'123456','slug':'new-store','admin_email':'new-admin@example.com'})
    assert response.status_code==400,response.text
    assert db_session.query(Tenant).count()==before


def test_parsed_report_handles_comments_without_bypassing_company(db_session,shops):
    from app.core.tenant_scope import bind_tenant
    from app.core.tenant_sql import scope_report_sql
    from sqlalchemy import text
    a,_,_=shops['a'];bind_tenant(db_session,a.tenant_id,a.id)
    scoped=scope_report_sql('SELECT email FROM/**/users',db_session,a.tenant_id)
    assert [row[0] for row in db_session.execute(text(scoped)).all()]==[a.email]
    with pytest.raises(HTTPException):
        scope_report_sql("SELECT pg_read_file/**/('/etc/passwd')",db_session,a.tenant_id)


@pytest.mark.parametrize('operation',['create-order','capture-order'])
def test_custom_customer_role_cannot_record_payments_for_other_customers(client,db_session,shops,monkeypatch,operation):
    from app.models.role import Role
    from app.services.paypal_service import PayPalService
    def forbidden(*args,**kwargs):pytest.fail('Customer without sales permission contacted the provider for another account')
    monkeypatch.setattr(PayPalService,'create_order',forbidden)
    monkeypatch.setattr(PayPalService,'capture_order',forbidden)
    a,va,ba=shops['a']
    role=Role(name='shopper',tenant_id=a.tenant_id)
    db_session.add(role);db_session.flush()
    other=User(email='same-company@example.com',full_name='Other Customer',role_id=role.id,
        tenant_id=a.tenant_id,hashed_password='unused',is_active=True)
    a.role_id=role.id;db_session.add(other);db_session.commit()
    data={'sucursal_id':str(ba[0].id),'cliente_id':str(other.id),'monto_bob':10}
    if operation=='capture-order':
        data={'sucursal_id':str(ba[0].id),'cliente_id':str(other.id),'order_id':'audit-no-charge',
            'detalles':[{'variante_id':str(va.id),'cantidad':1}]}
    response=client.post('/api/v1/payments/paypal/'+operation,headers=headers(a),json=data)
    assert response.status_code==403,response.text


def test_all_commercial_models_have_an_ownership_boundary():
    from app.database import Base
    from app.core.tenant_scope import predicates
    owned=predicates(uuid.uuid4())
    unscoped={mapper.class_.__name__ for mapper in Base.registry.mappers if mapper.class_ not in owned}
    # SaaS audit is global platform metadata, never part of a commercial graph.
    # Verified webhook events are central and refer to a tenant-owned billing
    # record; they have no generic commercial route/import or ORM write path.
    assert unscoped=={'TokenBlacklist','Permission','SubscriptionPlan','SubscriptionPlanVersion','PlatformAuditLog','SubscriptionPaymentEvent'}


def test_deferred_relationship_foreign_key_cannot_bypass_scope(db_session,shops):
    from app.core.tenant_scope import bind_tenant
    from app.models.product_variant import ProductVariant
    a,_,_=shops['a'];_,vb,_=shops['b'];foreign_product=vb.producto
    bind_tenant(db_session,a.tenant_id,a.id)
    injected=ProductVariant(producto=foreign_product,sku='must-not-persist',talla='M',color='Black',activo=True)
    db_session.add(injected)
    with pytest.raises(HTTPException) as exc:db_session.flush()
    assert exc.value.status_code==404
