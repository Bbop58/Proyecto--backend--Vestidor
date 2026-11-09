import uuid
from contextlib import nullcontext
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from tests.test_tenant_sales import shops
from tests.test_tenant_contract import headers
from app.core.security import create_refresh_token
from app.schemas.notification_push import DeviceRegistration,DeviceRevocation
from app.models.notification_push import NotificationDevice as Device,NotificationPushDelivery as Delivery
from app.services import notification_push_service as push
from app.services.notification_service import NotificationService
from tests.test_commercial_invariants import committed_shop

def registration(user,**overrides):
    values=dict(installation_id=uuid.uuid4(),installation_secret='only-qa-installation-secret-32-chars',
        token='only-qa-provider-token-'+str(uuid.uuid4()),platform='web',
        refresh_token=create_refresh_token(user.id,tenant_id=user.tenant_id)['token'])
    return DeviceRegistration(**{**values,**overrides})

def test_registration_rotation_owner_switch_and_stale_offline_logout(db_session,shops):
    a,_,_=shops['a'];b,_,_=shops['b'];payload=registration(a)
    first=push.register(db_session,a,payload,{})
    assert push.register(db_session,a,payload,{})['binding_id']==first['binding_id']
    stolen=payload.model_copy(update={'installation_secret':'different-secret-of-more-than-32-characters'})
    with pytest.raises(HTTPException):push.register(db_session,b,stolen,{})
    db_session.rollback()
    reassigned=registration(b,installation_id=payload.installation_id,installation_secret=payload.installation_secret)
    second=push.register(db_session,b,reassigned,{})
    assert first['binding_id']!=second['binding_id']
    push.unregister(db_session,DeviceRevocation(installation_id=payload.installation_id,
        installation_secret=payload.installation_secret,binding_id=first['binding_id']))
    assert db_session.get(Device,payload.installation_id).active
    push.unregister(db_session,DeviceRevocation(installation_id=payload.installation_id,
        installation_secret=payload.installation_secret,binding_id=second['binding_id']))
    assert not db_session.get(Device,payload.installation_id).active

def test_delivery_once_and_no_old_history_for_new_device(db_session,shops):
    a,_,_=shops['a']
    NotificationService.create(db_session,a.id,'Anterior','Anterior')
    assert db_session.query(Delivery).count()==0
    push.register(db_session,a,registration(a),{})
    notice=NotificationService.create(db_session,a.id,'Nueva','Detalle privado')
    assert db_session.query(Delivery).one().notification_id==notice.id
    send=Mock();factory=lambda:nullcontext(db_session)
    assert push.deliver_pending(factory,send)==1
    assert push.deliver_pending(factory,send)==0
    assert send.call_count==1 and db_session.query(Delivery).one().status=='SENT'

def test_transient_failure_retries_and_permanent_failure_disables_token(db_session,shops):
    a,_,_=shops['a'];push.register(db_session,a,registration(a),{})
    NotificationService.create(db_session,a.id,'Nueva','Detalle privado')
    factory=lambda:nullcontext(db_session)
    push.deliver_pending(factory,Mock(side_effect=TimeoutError()))
    row=db_session.query(Delivery).one()
    assert row.status=='RETRY' and row.attempts==1
    assert push.deliver_pending(factory,Mock())==0
    row.next_attempt_at=datetime.now(timezone.utc)-timedelta(seconds=1);db_session.commit()
    UnregisteredError=type('UnregisteredError',(Exception,),{})
    push.deliver_pending(factory,Mock(side_effect=UnregisteredError()))
    assert db_session.query(Delivery).one().status=='FAILED'
    assert not db_session.query(Device).one().active

def test_pending_delivery_never_follows_installation_to_another_company(db_session,shops):
    a,_,_=shops['a'];b,_,_=shops['b'];payload=registration(a)
    push.register(db_session,a,payload,{})
    NotificationService.create(db_session,a.id,'A','Solo A')
    push.register(db_session,b,registration(b,installation_id=payload.installation_id,
        installation_secret=payload.installation_secret),{})
    send=Mock();push.deliver_pending(lambda:nullcontext(db_session),send)
    send.assert_not_called();assert db_session.query(Delivery).one().status=='SKIPPED'

def test_push_rejects_foreign_refresh_and_does_not_echo_credentials(client,db_session,shops,monkeypatch):
    from app.config import settings
    from pydantic import SecretStr
    monkeypatch.setattr(settings,'NOTIFICATION_PUSH_ENABLED',True)
    monkeypatch.setattr(settings,'FIREBASE_ADMIN_JSON',SecretStr('{}'))
    a,_,_=shops['a'];b,_,_=shops['b'];body=registration(b).model_dump(mode='json')
    result=client.put('/api/v1/notifications/push/devices',headers=headers(a),json=body)
    assert result.status_code==400 and body['refresh_token'] not in result.text
    body['platform']='not-a-platform'
    result=client.put('/api/v1/notifications/push/devices',headers=headers(a),json=body)
    assert result.status_code==422 and body['token'] not in result.text

def test_unregister_missing_binding_has_no_ownership_oracle(client):
    body={'installation_id':str(uuid.uuid4()),'installation_secret':'random-secret-longer-than-thirty-two',
        'binding_id':str(uuid.uuid4())}
    assert client.post('/api/v1/notifications/push/unregister',json=body).json()=={'active':False}

def test_revoked_web_session_suppresses_queued_push(db_session,shops):
    from app.core.security import decode_token
    from app.models.token_blacklist import TokenBlacklist
    a,_,_=shops['a'];payload=registration(a);push.register(db_session,a,payload,{})
    NotificationService.create(db_session,a.id,'Nueva','Privado')
    claims=decode_token(payload.refresh_token)
    db_session.add(TokenBlacklist(token_jti=claims['jti'],expires_at=datetime.fromtimestamp(claims['exp'],timezone.utc)))
    db_session.commit();send=Mock()
    push.deliver_pending(lambda:nullcontext(db_session),send)
    send.assert_not_called();assert db_session.query(Delivery).one().status=='SKIPPED'


def test_company_backup_excludes_device_credentials_and_restore_revokes_bindings(db_session,shops):
    import json
    from app.core.tenant_scope import bind_tenant
    from app.services.backup_service import BackupService,json_serializer
    a,_,_=shops['a'];payload=registration(a);push.register(db_session,a,payload,{})
    bind_tenant(db_session,a.tenant_id,a.id)
    backup=BackupService.create_backup(db_session,a)
    serialized=json.dumps(backup,default=json_serializer)
    assert payload.token not in serialized and payload.installation_secret not in serialized
    assert 'notification_devices' not in backup['data']
    BackupService.restore_backup(db_session,json.loads(serialized),a)
    device=db_session.get(Device,payload.installation_id)
    assert device is None or not device.active


def test_password_change_suppresses_delivery_of_old_session(db_session,shops):
    a,_,_=shops['a'];push.register(db_session,a,registration(a),{})
    NotificationService.create(db_session,a.id,'Cambio','Privado')
    a.hashed_password='a-new-credential';db_session.commit();send=Mock()
    push.deliver_pending(lambda:nullcontext(db_session),send)
    send.assert_not_called();assert db_session.query(Delivery).one().status=='SKIPPED'


def test_two_workers_send_one_delivery_once(committed_shop):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier,Lock
    import time
    from app.models import User
    ids,factory=committed_shop
    with factory() as db:
        user=db.get(User,ids[0]);push.register(db,user,registration(user),{})
        NotificationService.create(db,user.id,'Nueva','Privado')
    calls=[];lock=Lock();barrier=Barrier(2)
    def send(device,notification):
        with lock:calls.append(notification.id)
        time.sleep(.2)
    def work(_):barrier.wait(timeout=20);return push.deliver_pending(factory,send)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,[0,1]))
    assert sum(results)==1 and len(calls)==1
    with factory() as db:assert db.query(Delivery).one().status=='SENT'


def test_concurrent_registration_of_same_provider_recipient_has_one_active_binding(committed_shop):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.models import User
    ids,factory=committed_shop;barrier=Barrier(2)
    def bind(_):
        with factory() as db:
            user=db.get(User,ids[0]);payload=registration(user,token='qa-one-provider-recipient')
            barrier.wait(timeout=20);return push.register(db,user,payload,{})
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(bind,[0,1]))
    with factory() as db:assert db.query(Device).filter_by(active=True).count()==1


def test_web_registration_stops_delivering_after_refresh_expiry(db_session,shops,monkeypatch):
    from app.core.security import decode_token
    a,_,_=shops['a'];payload=registration(a);push.register(db_session,a,payload,{})
    NotificationService.create(db_session,a.id,'Nueva','Privado')
    expiry=datetime.fromtimestamp(decode_token(payload.refresh_token)['exp'],timezone.utc)
    monkeypatch.setattr(push,'now',lambda:expiry+timedelta(seconds=1))
    send=Mock();push.deliver_pending(lambda:nullcontext(db_session),send)
    send.assert_not_called();assert db_session.query(Delivery).one().status=='SKIPPED'


@pytest.mark.parametrize('suspended',['user','company'])
def test_suspended_identity_does_not_receive_pending_delivery(db_session,shops,suspended):
    from app.models import Tenant
    a,_,_=shops['a'];push.register(db_session,a,registration(a),{})
    NotificationService.create(db_session,a.id,'Nueva','Privado')
    if suspended=='user':a.is_active=False
    else:db_session.get(Tenant,a.tenant_id).activo=False
    db_session.commit();send=Mock();push.deliver_pending(lambda:nullcontext(db_session),send)
    send.assert_not_called();assert db_session.query(Delivery).one().status=='SKIPPED'


def test_rolled_back_operation_cannot_leak_a_delivery(committed_shop):
    from app.models import User,Notification
    ids,factory=committed_shop
    with factory() as db:
        user=db.get(User,ids[0]);push.register(db,user,registration(user),{})
        notice=Notification(user_id=user.id,title='Abortada',message='No confirmar')
        db.add(notice);db.flush();push.enqueue(db,notice);db.flush();notice_id=notice.id
        db.rollback()
    send=Mock();assert push.deliver_pending(factory,send)==0;send.assert_not_called()
    with factory() as db:
        assert db.get(Notification,notice_id) is None
        assert db.query(Delivery).filter_by(notification_id=notice_id).count()==0


def test_crash_after_provider_acceptance_reuses_event_identifier(committed_shop):
    from app.models import User,Notification
    ids,factory=committed_shop
    with factory() as db:
        user=db.get(User,ids[0]);push.register(db,user,registration(user),{})
        notice_id=NotificationService.create(db,user.id,'Nueva','Privado').id
    accepted=[]
    def accepted_then_crash(device,notification):
        accepted.append(str(notification.id));raise SystemExit('simulated process loss')
    with pytest.raises(SystemExit):push.deliver_pending(factory,accepted_then_crash)
    assert push.deliver_pending(factory,lambda device,notification:accepted.append(str(notification.id)))==1
    assert accepted==[str(notice_id),str(notice_id)]
    with factory() as db:
        assert db.query(Notification).filter_by(id=notice_id).count()==1
        assert db.query(Delivery).filter_by(notification_id=notice_id).one().status=='SENT'


@pytest.mark.parametrize('revoked',[False,True])
def test_persistent_mobile_session_uses_revocation_not_web_refresh_expiry(db_session,shops,monkeypatch,revoked):
    from app.models.mobile_session import MobileSession
    a,_,_=shops['a'];session=MobileSession(user_id=a.id,tenant_id=a.tenant_id,credential_hash='a'*64)
    db_session.add(session);db_session.commit()
    push.register(db_session,a,registration(a,platform='android',refresh_token=None),{'mobile_session_id':str(session.id)})
    NotificationService.create(db_session,a.id,'Nueva','Privado')
    future=datetime.now(timezone.utc)+timedelta(days=30)
    if revoked:session.revoked_at=future;db_session.commit()
    monkeypatch.setattr(push,'now',lambda:future);send=Mock()
    assert push.deliver_pending(lambda:nullcontext(db_session),send)==(0 if revoked else 1)
    assert send.call_count==(0 if revoked else 1)


def test_transient_retries_stop_at_six_attempts(db_session,shops,monkeypatch):
    a,_,_=shops['a'];push.register(db_session,a,registration(a),{})
    NotificationService.create(db_session,a.id,'Nueva','Privado')
    clock=datetime.now(timezone.utc);monkeypatch.setattr(push,'now',lambda:clock)
    send=Mock(side_effect=TimeoutError())
    for attempt in range(1,7):
        push.deliver_pending(lambda:nullcontext(db_session),send)
        row=db_session.query(Delivery).one();assert row.attempts==attempt
        assert row.status==('FAILED' if attempt==6 else 'RETRY')
        clock=row.next_attempt_at+timedelta(seconds=1)
    push.deliver_pending(lambda:nullcontext(db_session),send);assert send.call_count==6
