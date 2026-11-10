import uuid
from datetime import datetime,timedelta,timezone
import pytest
from sqlalchemy import select,update
from app.models.tenant import Tenant
from app.models.user import User
from app.models.subscription import Subscription
from app.models.company_invitation import CompanyInvitation as Invitation
from app.services import company_invitation_service as service
from tests.support_saas import saas_actors,headers
from tests.test_saas_subscription_billing import provider
from tests.test_commercial_invariants import committed_shop

@pytest.fixture
def mail(monkeypatch):
    sent=[]
    monkeypatch.setattr(service,'mail_available',lambda:True)
    monkeypatch.setattr(service,'send_invitation',lambda email,name,url:sent.append((email,name,url)))
    return sent

def payload():
    return dict(operation_id=str(uuid.uuid4()),nombre='Nueva tienda',razon_social='Nueva tienda SRL',
        nit_rut='123456',slug='nueva-tienda',owner_email='new-owner@example.com',owner_name='Nuevo Propietario',reason='Alta por invitación')

def create(client,actors,mail):
    body=payload();r=client.post('/api/v1/platform/company-invitations',headers=headers(actors['operator']),json=body)
    assert r.status_code==201,r.text
    return body,r.json(),mail[-1][2].split('#')[1]

def test_invite_verify_login_and_no_free_contract(client,db_session,saas_actors,mail):
    body,result,token=create(client,saas_actors,mail)
    assert result['email_sent'] and result['state']=='PENDIENTE'
    assert token not in str(result)
    company_id=result['company']['id']
    assert db_session.get(Tenant,uuid.UUID(company_id)).activo is False
    assert not db_session.query(User).filter_by(email=body['owner_email']).first()
    again=client.post('/api/v1/platform/company-invitations',headers=headers(saas_actors['operator']),json=body)
    assert again.status_code==201 and len(mail)==1
    assert client.post('/api/v1/auth/company-invitations/preview',json={'token':token}).json()['company_name']==body['nombre']
    accept=client.post('/api/v1/auth/company-invitations/accept',json={'token':token,'password':'OwnerOnly123!'})
    assert accept.status_code==200,accept.text
    user=db_session.query(User).filter_by(email=body['owner_email']).one()
    assert user.is_verified and str(user.tenant_id)==company_id and user.role.name=='admin'
    assert db_session.query(Subscription).filter_by(tenant_id=user.tenant_id).count()==0
    login=client.post('/api/v1/auth/login',json={'email':body['owner_email'],'password':'OwnerOnly123!','tenant_id':company_id})
    assert login.status_code==200,login.text
    auth={'Authorization':'Bearer '+login.json()['access_token']}
    profile=client.get('/api/v1/users/me',headers=auth)
    assert profile.status_code==200 and profile.json()['service_restricted'] is True
    assert client.get('/api/v1/subscriptions/contract',headers=auth).json()['subscription_id'] is None
    assert client.post('/api/v1/productos',headers=auth,json={}).status_code==403
    assert client.get('/api/v1/platform/companies',headers=auth).status_code==403
    assert client.post('/api/v1/auth/company-invitations/accept',json={'token':token,'password':'Another123!'}).status_code==400

def test_resend_invalidates_previous_and_expiry(client,db_session,saas_actors,mail):
    _,result,old=create(client,saas_actors,mail)
    body={'operation_id':str(uuid.uuid4())}
    url='/api/v1/platform/company-invitations/'+result['company']['id']+'/resend'
    assert client.post(url,headers=headers(saas_actors['operator']),json=body).status_code==200
    token=mail[-1][2].split('#')[1]
    assert token!=old and len(mail)==2
    assert client.post(url,headers=headers(saas_actors['operator']),json=body).status_code==200 and len(mail)==2
    assert client.post('/api/v1/auth/company-invitations/preview',json={'token':old}).status_code==400
    db_session.connection().execute(update(Invitation).values(expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)));db_session.commit()
    assert client.post('/api/v1/auth/company-invitations/accept',json={'token':token,'password':'OwnerOnly123!'}).status_code==400

def test_mail_failure_recover_without_duplicate(client,db_session,saas_actors,mail,monkeypatch):
    monkeypatch.setattr(service,'send_invitation',lambda *a:(_ for _ in ()).throw(TimeoutError()))
    body=payload();r=client.post('/api/v1/platform/company-invitations',headers=headers(saas_actors['operator']),json=body)
    assert r.status_code==201 and not r.json()['email_sent']
    assert client.post('/api/v1/platform/company-invitations',headers=headers(saas_actors['operator']),json=body).status_code==201
    assert db_session.query(Tenant).filter_by(slug=body['slug']).count()==1
    monkeypatch.setattr(service,'send_invitation',lambda e,n,u:mail.append((e,n,u)))
    assert client.post('/api/v1/platform/company-invitations/'+r.json()['company']['id']+'/resend',headers=headers(saas_actors['operator']),json={'operation_id':str(uuid.uuid4())}).json()['email_sent']

def test_invitation_permissions_uniqueness_and_unconfigured(client,db_session,saas_actors,mail,monkeypatch):
    body=payload()
    assert client.post('/api/v1/platform/company-invitations',headers=headers(saas_actors['admin']),json=body).status_code==403
    body['owner_email']=saas_actors['admin'].email
    assert client.post('/api/v1/platform/company-invitations',headers=headers(saas_actors['operator']),json=body).status_code==409
    assert not db_session.query(Tenant).filter_by(slug=body['slug']).first()
    body=payload();monkeypatch.setattr(service,'mail_available',lambda:False)
    assert client.post('/api/v1/platform/company-invitations',headers=headers(saas_actors['operator']),json=body).status_code==503
    assert not db_session.query(Tenant).filter_by(slug=body['slug']).first()

def test_verified_owner_can_request_initial_contract_but_not_sell_before_payment(client,db_session,saas_actors,mail,provider):
    from app.models.subscription_plan import SubscriptionPlan
    from app.core.plan_catalog_ddl import ensure_legacy_versions
    body,result,token=create(client,saas_actors,mail)
    assert client.post('/api/v1/auth/company-invitations/accept',json={'token':token,'password':'OwnerOnly123!'}).status_code==200
    plan=SubscriptionPlan(codigo='INVITE-PLAN',nombre='Plan de prueba',max_sucursales=2,precio_mensual_bob=100)
    db_session.add(plan);db_session.flush();ensure_legacy_versions(db_session.connection());db_session.commit()
    login=client.post('/api/v1/auth/login',json={'email':body['owner_email'],'password':'OwnerOnly123!'}).json()
    auth={'Authorization':'Bearer '+login['access_token']}
    offer=next(x for x in client.get('/api/v1/subscriptions/plans').json() if x['codigo']=='INVITE-PLAN')
    quote=client.post('/api/v1/subscriptions/quote',headers=auth,json={'version_id':offer['version_id'],'kind':'ALTA'})
    assert quote.status_code==200,quote.text
    r=client.post('/api/v1/subscriptions/requests',headers=auth,json={'quote_token':quote.json()['quote_token'],'acceptance':True,'idempotency_key':str(uuid.uuid4())})
    assert r.status_code==201,r.text
    decision=client.post('/api/v1/platform/subscription-requests/'+r.json()['id']+'/decision',headers=headers(saas_actors['operator']),json={'decision':'APROBADA','reason':'Alta verificada por operador','expected_version':r.json()['lock_version'],'idempotency_key':str(uuid.uuid4())})
    assert decision.status_code==200,decision.text
    assert decision.json()['billing_payment_id']
    assert client.post('/api/v1/productos',headers=auth,json={}).status_code==403
    # A pending initial subscription remains ALTA, not a legacy renewal.
    assert client.get('/api/v1/subscriptions/contract',headers=auth).json()['terms']==[]
    payment=decision.json()['billing_payment_id']
    assert client.post('/api/v1/subscriptions/billing/'+payment+'/create',headers=auth,json={}).status_code==200
    paid=client.post('/api/v1/subscriptions/billing/'+payment+'/capture',headers=auth,json={})
    assert paid.status_code==200 and paid.json()['state']=='APPLIED',paid.text
    assert client.get('/api/v1/users/me',headers=auth).json()['service_restricted'] is False
    branch=client.post('/api/v1/sucursales',headers=auth,json={'nombre':'Central','ciudad':'Tarija','direccion':'Calle QA 123'})
    assert branch.status_code==201,branch.text

def test_private_invitation_metadata_and_secrets(client,db_session,saas_actors,mail):
    from app.services.backup_recovery import models_for
    body,result,token=create(client,saas_actors,mail)
    row=db_session.connection().execute(select(Invitation.__table__)).mappings().one()
    assert row['token_hash']!=token and token not in str(dict(row))
    assert 'company_invitations' not in models_for(row['tenant_id'])
    bad=client.post('/api/v1/auth/company-invitations/accept',json={'token':token,'password':'tiny'})
    assert bad.status_code==422 and token not in bad.text and 'tiny' not in bad.text
    unicode=client.post('/api/v1/auth/company-invitations/accept',json={'token':token,'password':'ñ'*50})
    assert unicode.status_code==422
    changed=body|{'nombre':'Otra empresa'}
    assert client.post('/api/v1/platform/company-invitations',headers=headers(saas_actors['operator']),json=changed).status_code==409
    duplicate=payload()|{'operation_id':str(uuid.uuid4()),'slug':'another-company'}
    assert client.post('/api/v1/platform/company-invitations',headers=headers(saas_actors['operator']),json=duplicate).status_code==409

def test_accept_does_not_override_an_explicit_company_suspension(client,db_session,saas_actors,mail):
    _,result,token=create(client,saas_actors,mail)
    db_session.connection().execute(update(Tenant).where(Tenant.id==uuid.UUID(result['company']['id'])).values(activo=False,platform_revision=2));db_session.commit()
    response=client.post('/api/v1/auth/company-invitations/accept',json={'token':token,'password':'OwnerOnly123!'})
    assert response.status_code==409 and 'suspendida' in response.json()['detail']
    assert not db_session.query(User).filter_by(email='new-owner@example.com').first()

def test_brevo_invitation_has_one_private_fragment_and_no_attachment(monkeypatch):
    import json
    from pydantic import SecretStr
    from app.config import settings
    from app.services import transactional_mail as mailer
    monkeypatch.setattr(settings,'BREVO_API_KEY',SecretStr('private-key'))
    monkeypatch.setattr(settings,'SMTP_FROM','sender@example.com')
    monkeypatch.setattr(settings,'SUBSCRIPTION_WEB_URL','https://example.com/empresa/suscripcion')
    captured=[]
    class Response:
        status=201
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self):return b'{"messageId":"qa-message"}'
    monkeypatch.setattr(mailer,'urlopen',lambda request,timeout:(captured.append(request) or Response()))
    link=mailer.invitation_url('private-token')
    assert link=='https://example.com/auth/invitacion-empresa#private-token'
    mailer.send_invitation('owner@example.com','Mi tienda',link)
    payload=json.loads(captured[0].data)
    assert link in payload['textContent'] and 'attachment' not in payload
    assert 'private-key' not in captured[0].data.decode()

def test_concurrent_accept_creates_one_verified_owner(committed_shop):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.models.role import Role
    from app.schemas.company_invitation import InvitationAccept
    ids,factory=committed_shop;token='OnlySyntheticInvitationTokenForConcurrency123'
    with factory() as db:
        creator=db.get(User,ids[0]);tenant_id=creator.tenant_id
        db.add(Role(name='admin',tenant_id=None));db.flush()
        db.add(Invitation(tenant_id=tenant_id,created_by=creator.id,operation_id=uuid.uuid4(),fingerprint='a'*64,
            owner_email='concurrent-owner@example.com',owner_name='Owner',token_hash=service.digest(token),expires_at=service.now()+timedelta(hours=1)))
        db.commit()
    gate=Barrier(2)
    def consume():
        from fastapi import HTTPException
        with factory() as db:
            gate.wait(timeout=10)
            try:service.accept(db,InvitationAccept(token=token,password='OwnerOnly123!'));return 200
            except HTTPException as error:return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(consume) for _ in range(2)];results=sorted(f.result(timeout=25) for f in futures)
    assert results==[200,400]
    with factory() as db:assert db.query(User).filter_by(email='concurrent-owner@example.com').count()==1
