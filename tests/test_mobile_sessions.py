"""SM1 persistent mobile sessions, not an extension of legacy web refresh JWTs."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from app.models.mobile_session import MobileSession
from app.services.mobile_session_service import MobileSessionService as Sessions
from app.core.security import create_access_token, decode_token, get_password_hash
from app.schemas.auth import ChangePasswordRequest
from app.services.auth_service import AuthService
from tests.test_tenant_sales import shops
from tests.support_saas import saas_actors, headers


def bearer(token):
    return {'Authorization': 'Bearer '+token}


def issue(db, shops, key='a'):
    return Sessions.issue(db, shops[key][0])


def test_persistent_credential_is_hashed_and_survives_90_days(client, db_session, shops, caplog):
    result=issue(db_session,shops)
    row=db_session.get(MobileSession,result.session_id)
    assert row.credential_hash==hashlib.sha256(result.session_token.encode()).hexdigest()
    assert len(row.credential_hash)==64 and result.session_token not in repr(row.__dict__)
    row.created_at=row.last_used_at=datetime.now(timezone.utc)-timedelta(days=90)
    db_session.commit()
    response=client.post('/api/v1/auth/mobile/refresh',json={'session_token':result.session_token})
    assert response.status_code==200
    data=response.json()
    assert data['session_id']==str(result.session_id) and 'session_token' not in data
    assert data['user']['id']==str(shops['a'][0].id)
    payload=decode_token(data['access_token'])
    assert payload['type']=='access' and payload['mobile_session_id']==str(result.session_id)
    assert payload['exp']-payload['iat']<=3600
    assert result.session_token not in repr(result) and result.session_token not in caplog.text


def test_mobile_exchange_uses_authenticated_company_not_body(client,db_session,shops):
    a=shops['a'][0];b=shops['b'][0]
    token=create_access_token(str(a.id),tenant_id=a.tenant_id)['token']
    response=client.post('/api/v1/auth/mobile/session',headers=bearer(token),json={'tenant_id':str(b.tenant_id),'user_id':str(b.id)})
    assert response.status_code==200
    data=response.json();row=db_session.get(MobileSession,uuid.UUID(data['session_id']))
    assert row.tenant_id==a.tenant_id and row.user_id==a.id
    assert data['user']['tenant_id']==str(a.tenant_id)


def test_two_installations_and_web_token_remain_independent(client,db_session,shops):
    first=issue(db_session,shops);second=issue(db_session,shops)
    a=shops['a'][0];web=create_access_token(str(a.id),tenant_id=a.tenant_id)['token']
    for _ in range(2):
        assert client.post('/api/v1/auth/mobile/logout',json={'session_token':first.session_token}).status_code==200
    assert client.get('/api/v1/users/me',headers=bearer(first.access_token)).status_code==401
    assert client.get('/api/v1/users/me',headers=bearer(second.access_token)).status_code==200
    assert client.get('/api/v1/users/me',headers=bearer(web)).status_code==200
    assert client.post('/api/v1/auth/mobile/refresh',json={'session_token':first.session_token}).json()['detail']['code']=='MOBILE_SESSION_REVOKED'


def test_expired_access_can_refresh_and_credential_is_not_api_bearer(client,db_session,shops):
    result=issue(db_session,shops);a=shops['a'][0]
    expired=create_access_token(str(a.id),tenant_id=a.tenant_id,mobile_session_id=result.session_id,expires_delta=timedelta(seconds=-10))['token']
    assert client.get('/api/v1/users/me',headers=bearer(expired)).status_code==401
    assert client.get('/api/v1/users/me',headers=bearer(result.session_token)).status_code==401
    assert client.post('/api/v1/auth/mobile/refresh',json={'session_token':result.session_token}).status_code==200
    assert client.post('/api/v1/auth/mobile/logout',json={'session_token':result.session_token}).status_code==200


@pytest.mark.parametrize('path',['/users/me','/ventas/mis-compras','/backups/stats','/subscriptions/current'])
def test_revocation_applies_to_all_identity_helpers(client,db_session,shops,path):
    result=issue(db_session,shops)
    Sessions.revoke(db_session,result.session_token)
    response=client.get('/api/v1'+path,headers=bearer(result.access_token))
    assert response.status_code==401 and response.json()['detail']['code']=='MOBILE_SESSION_REVOKED'


@pytest.mark.parametrize('body', [{'session_token':'not-a-token'}, {'session_token':None}, {'wrong_field':'synthetic-private-value'}])
def test_bad_credentials_never_echo_input(client,db_session,shops,body):
    response=client.post('/api/v1/auth/mobile/refresh',json=body)
    assert response.status_code in (400,422)
    assert 'not-a-token' not in response.text and 'synthetic-private-value' not in response.text


def test_valid_shape_unknown_secret_does_not_reveal_identity(client,db_session,shops):
    result=issue(db_session,shops)
    wrong=result.session_token[:-1]+('A' if result.session_token[-1]!='A' else 'B')
    response=client.post('/api/v1/auth/mobile/refresh',json={'session_token':wrong})
    assert response.status_code==401
    assert str(shops['a'][0].id) not in response.text and wrong not in response.text
    assert client.post('/api/v1/auth/mobile/logout',json={'session_token':wrong}).status_code==200
    assert client.get('/api/v1/users/me',headers=bearer(result.access_token)).status_code==200


def test_mobile_claim_cannot_be_moved_to_another_user_or_company(client,db_session,shops):
    result=issue(db_session,shops);b=shops['b'][0]
    forged=create_access_token(str(b.id),tenant_id=b.tenant_id,mobile_session_id=result.session_id)['token']
    assert client.get('/api/v1/users/me',headers=bearer(forged)).status_code==401
    assert client.get('/api/v1/users/me',headers=bearer(result.access_token)).status_code==200


def test_platform_cannot_create_mobile_session(client,saas_actors):
    response=client.post('/api/v1/auth/mobile/session',headers=headers(saas_actors['operator']))
    assert response.status_code==403


def test_mobile_access_cannot_clone_another_persistent_session(client,db_session,shops):
    result=issue(db_session,shops)
    assert client.post('/api/v1/auth/mobile/session',headers=bearer(result.access_token)).status_code==400


def test_inactive_user_is_definitively_revoked_but_company_restriction_is_not(db_session,shops):
    result=issue(db_session,shops);a=shops['a'][0]
    a.is_active=False;db_session.commit()
    with pytest.raises(HTTPException) as caught:Sessions.refresh(db_session,result.session_token)
    assert caught.value.status_code==403
    a.is_active=True;db_session.commit()
    with pytest.raises(HTTPException):Sessions.refresh(db_session,result.session_token)


def test_transferred_identity_is_not_reassigned(db_session,shops):
    result=issue(db_session,shops);a=shops['a'][0]
    a.tenant_id=shops['b'][0].tenant_id;db_session.commit()
    with pytest.raises(HTTPException) as caught:Sessions.refresh(db_session,result.session_token)
    assert caught.value.detail['code']=='MOBILE_IDENTITY_CHANGED'
    row=db_session.get(MobileSession,result.session_id)
    assert row.revoked_at is not None


def test_password_change_revokes_own_sessions_atomically(db_session,shops):
    a=shops['a'][0];a.hashed_password=get_password_hash('Previous123!');db_session.commit()
    own=issue(db_session,shops);other=issue(db_session,shops,'b')
    AuthService.change_password(db_session,a,ChangePasswordRequest(current_password='Previous123!',new_password='Updated123!'))
    assert db_session.get(MobileSession,own.session_id).revoked_at is not None
    assert db_session.get(MobileSession,other.session_id).revoked_at is None


def test_database_failure_is_transient_without_leaking_secret(db_session,shops,monkeypatch):
    from sqlalchemy.exc import OperationalError
    result=issue(db_session,shops)
    def unavailable(*args,**kwargs):raise OperationalError('private SQL',{},Exception('internal details'))
    monkeypatch.setattr(db_session,'execute',unavailable)
    with pytest.raises(HTTPException) as caught:Sessions.refresh(db_session,result.session_token)
    assert caught.value.status_code==503
    assert result.session_token not in str(caught.value.detail) and 'private SQL' not in str(caught.value.detail)


def test_legacy_logout_also_revokes_a_mobile_access_session(client,db_session,shops):
    result=issue(db_session,shops)
    response=client.post('/api/v1/auth/logout',headers=bearer(result.access_token),json={})
    assert response.status_code==200
    assert client.post('/api/v1/auth/mobile/refresh',json={'session_token':result.session_token}).status_code==401


def test_contract_suspension_preserves_session_and_current_role(db_session,shops):
    from app.models.role import Role
    a=shops['a'][0];result=issue(db_session,shops)
    a.role_id=db_session.query(Role).filter_by(name='cliente').one().id
    a.tenant.activo=False;db_session.commit()
    with pytest.raises(HTTPException) as caught:Sessions.refresh(db_session,result.session_token)
    assert caught.value.status_code==403 and caught.value.detail['code']=='MOBILE_SERVICE_RESTRICTED'
    assert db_session.get(MobileSession,result.session_id).revoked_at is None
    a.tenant.activo=True;db_session.commit()
    renewed=Sessions.refresh(db_session,result.session_token)
    assert 'users.create' not in renewed.user.permissions


@pytest.mark.parametrize('claim',[None,'bad-uuid',[],{}])
def test_invalid_mobile_claim_is_not_treated_as_a_legacy_web_session(client,db_session,shops,claim):
    from app.config import settings
    import jwt
    a=shops['a'][0];data=decode_token(create_access_token(str(a.id),tenant_id=a.tenant_id)['token'])
    data['mobile_session_id']=claim
    token=jwt.encode(data,settings.SECRET_KEY,algorithm=settings.ALGORITHM)
    assert client.get('/api/v1/users/me',headers=bearer(token)).status_code==401


def test_mobile_credential_routes_do_not_accept_a_legacy_access_token_as_the_credential(client,db_session,shops):
    result=issue(db_session,shops)
    a=shops['a'][0]
    legacy=create_access_token(str(a.id),tenant_id=a.tenant_id)['token']
    for route in ['refresh','logout']:
        assert client.post('/api/v1/auth/mobile/'+route,headers=bearer(legacy),json={}).status_code==422
        response=client.post('/api/v1/auth/mobile/'+route,json={'session_token':legacy})
        assert response.status_code==400 and legacy not in response.text
    assert client.get('/api/v1/users/me',headers=bearer(result.access_token)).status_code==200
