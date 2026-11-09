import uuid
import pytest
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect
from tests.test_tenant_sales import shops
from tests.test_tenant_contract import headers
from app.models import Notification, Role
from app.services.notification_service import NotificationService


def test_product_notice_targets_only_clients_of_explicit_company(db_session, shops):
    a, _, _ = shops['a']; b, _, _ = shops['b']
    role = db_session.query(Role).filter_by(name='cliente').one()
    a.role_id = role.id; b.role_id = role.id; db_session.commit()
    sent = NotificationService.notify_all_clients(db_session, 'Nuevo', 'Prenda', tenant_id=a.tenant_id)
    assert sent == 1
    assert [n.user_id for n in db_session.query(Notification).all()] == [a.id]


def test_no_clients_does_not_fall_back_to_staff(db_session, shops):
    a, _, _ = shops['a']
    assert NotificationService.notify_all_clients(db_session, 'Nuevo', 'Prenda', tenant_id=a.tenant_id) == 0
    assert db_session.query(Notification).count() == 0


def test_notification_roundtrip_preserves_spanish_and_emoji(db_session,shops):
    user,_,_=shops['a']
    notice=NotificationService.create(db_session,user.id,'¡Tu reserva está lista! 📦','Confirmación y devolución: acción.')
    assert notice is not None
    db_session.expire_all()
    assert db_session.get(Notification,notice.id).title=='¡Tu reserva está lista! 📦'


def test_websocket_authentication_and_initial_private_snapshot(client, db_session, shops):
    a, _, _ = shops['a']; b, _, _ = shops['b']
    own = NotificationService.create(db_session, a.id, 'Propia', 'Solo A')
    NotificationService.create(db_session, b.id, 'Ajena', 'Solo B')
    token = headers(a)['Authorization'].split()[1]
    with client.websocket_connect('/api/v1/notifications/ws', headers={'origin':'http://localhost:4200'}) as ws:
        ws.send_json({'token':token})
        snapshot = ws.receive_json()
        assert snapshot['type'] == 'notifications.changed'
        assert snapshot['latest_id'] == str(own.id)
        assert snapshot['unread_count'] == 1
        assert 'message' not in snapshot and 'token' not in snapshot


@pytest.mark.parametrize('token,origin', [('invalid','http://localhost:4200'), ('invalid','https://evil.example')])
def test_websocket_rejects_invalid_credentials_or_origin(client, token, origin):
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect('/api/v1/notifications/ws', headers={'origin':origin}) as ws:
            ws.send_json({'token':token})
            ws.receive_json()
    assert error.value.code == (4403 if origin == 'https://evil.example' else 4401)


def test_monitor_changes_only_for_own_messages_and_read_status(db_session, shops):
    from app.services.notification_realtime import snapshot
    a, _, _ = shops['a']; b, _, _ = shops['b']
    token = headers(a)['Authorization'].split()[1]
    first = snapshot(db_session, token)
    # Simulate another request's company context, not the request-owned session A.
    db_session.info.pop('tenant_scope',None);db_session.info.pop('actor_id',None)
    foreign=NotificationService.create(db_session, b.id, 'Ajena', 'Solo B')
    assert foreign is not None
    assert snapshot(db_session, token) == first
    notice = NotificationService.create(db_session, a.id, 'Propia', 'Solo A')
    second = snapshot(db_session, token)
    assert second['revision'] != first['revision'] and second['unread_count'] == 1
    NotificationService.mark_as_read(db_session, notice.id, a.id)
    assert snapshot(db_session, token)['unread_count'] == 0


def test_monitor_revalidates_revocation_and_expiry(db_session, shops):
    from app.services.notification_realtime import snapshot
    from app.core.security import create_access_token
    from app.models.token_blacklist import TokenBlacklist
    a, _, _ = shops['a']
    access = create_access_token(a.id, tenant_id=a.tenant_id)
    snapshot(db_session, access['token'])
    db_session.add(TokenBlacklist(token_jti=access['jti'],
        expires_at=access['expires_at']))
    db_session.commit()
    with pytest.raises(HTTPException):snapshot(db_session, access['token'])
    expired = create_access_token(a.id,tenant_id=a.tenant_id,expires_delta=timedelta(seconds=-1))
    with pytest.raises(HTTPException):snapshot(db_session, expired['token'])
