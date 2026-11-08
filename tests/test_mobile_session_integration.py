"""SM4 real PostgreSQL + HTTP contracts; no provider calls or hosted database."""
import uuid
from datetime import datetime,timedelta,timezone
import pytest
from app.core.security import get_password_hash,create_access_token
from app.models.sale import Sale
from app.models.inventory import Inventory
from app.services.wallet_service import WalletService
from tests.test_tenant_sales import shops


def auth(token): return {'Authorization':'Bearer '+token}


@pytest.mark.parametrize('company',['a','b'])
def test_mobile_http_renewal_keeps_wallet_operation_idempotent(client,db_session,shops,company):
    user,variant,branches=shops[company]
    user.hashed_password=get_password_hash('SyntheticSession123!');user.is_verified=True;db_session.commit()
    login=client.post('/api/v1/auth/login',json={'email':user.email,'password':'SyntheticSession123!','tenant_id':str(user.tenant_id)})
    assert login.status_code==200
    issued=client.post('/api/v1/auth/mobile/session',headers=auth(login.json()['access_token']))
    assert issued.status_code==200
    mobile=issued.json()
    expired=create_access_token(str(user.id),tenant_id=user.tenant_id,mobile_session_id=uuid.UUID(mobile['session_id']),expires_delta=timedelta(seconds=-1))['token']
    assert client.get('/api/v1/users/me',headers=auth(expired)).status_code==401
    refresh=client.post('/api/v1/auth/mobile/refresh',json={'session_token':mobile['session_token']})
    assert refresh.status_code==200 and refresh.json()['session_id']==mobile['session_id']
    headers=auth(refresh.json()['access_token'])
    grant=client.post('/api/v1/sync/authorizations',headers=headers,json={'channel':'WALLET','count':1})
    assert grant.status_code==200
    permit=grant.json()['grants'][0]
    wallet=WalletService.topup_cash_admin(db_session,user,user.id,100)
    db_session.commit()
    proof=client.get('/api/v1/wallet/me',headers=headers).json()
    stock=db_session.query(Inventory).filter_by(sucursal_id=branches[0].id,variante_id=variant.id).one().stock_actual
    order={'client_operation_id':permit['client_operation_id'],'offline_authorization':permit['authorization'],
        'sucursal_id':str(branches[0].id),'monto_total':10,'items':[{'variante_id':str(variant.id),'cantidad':1,'precio_unitario':10}],
        'fecha_local':datetime.now(timezone.utc).isoformat(),'nonce':proof['nonce_secuencia'],'firma_cliente':proof['firma_balance'],
        'codigo_autorizacion_offline':proof['firma_balance']}
    first=client.post('/api/v1/wallet/sync-offline-orders',headers=headers,json={'orders':[order]})
    assert first.status_code==200 and first.json()['exitosas']==1
    receipt=first.json()['results'][0]['receipt_number']
    renewed=client.post('/api/v1/auth/mobile/refresh',json={'session_token':mobile['session_token']}).json()
    repeated=client.post('/api/v1/wallet/sync-offline-orders',headers=auth(renewed['access_token']),json={'orders':[order]})
    assert repeated.status_code==200 and repeated.json()['results'][0]['receipt_number']==receipt
    assert client.get('/api/v1/wallet/me',headers=auth(renewed['access_token'])).json()['saldo_actual']==90
    db_session.expire_all()
    assert db_session.query(Sale).filter_by(tenant_id=user.tenant_id).count()==1
    assert db_session.query(Inventory).filter_by(sucursal_id=branches[0].id,variante_id=variant.id).one().stock_actual==stock-1
    other=shops['b' if company=='a' else 'a'][1]
    assert client.get('/api/v1/productos/'+str(other.producto_id),headers=headers).status_code==404
    assert client.post('/api/v1/auth/mobile/logout',json={'session_token':mobile['session_token']}).status_code==200
    assert client.get('/api/v1/wallet/me',headers=headers).status_code==401
    assert client.post('/api/v1/auth/mobile/refresh',json={'session_token':mobile['session_token']}).status_code==401


def test_mobile_http_password_change_revokes_old_installation_only_for_owner(client,db_session,shops):
    issued=[]
    for key in ['a','b']:
        user=shops[key][0];user.hashed_password=get_password_hash('PreviousSession123!');db_session.commit()
        login=client.post('/api/v1/auth/login',json={'email':user.email,'password':'PreviousSession123!','tenant_id':str(user.tenant_id)})
        mobile=client.post('/api/v1/auth/mobile/session',headers=auth(login.json()['access_token']))
        assert mobile.status_code==200;issued.append(mobile.json())
    changed=client.post('/api/v1/auth/change-password',headers=auth(issued[0]['access_token']),json={'current_password':'PreviousSession123!','new_password':'UpdatedSession123!'})
    assert changed.status_code==200
    assert client.post('/api/v1/auth/mobile/refresh',json={'session_token':issued[0]['session_token']}).status_code==401
    assert client.post('/api/v1/auth/mobile/refresh',json={'session_token':issued[1]['session_token']}).status_code==200
