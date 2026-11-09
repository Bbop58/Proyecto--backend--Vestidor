"""Per-installation FCM bindings and durable, independently retried delivery."""
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update, text
from fastapi import HTTPException
from app.models.notification_push import NotificationDevice as Device, NotificationPushDelivery as Delivery
from app.models import Notification, User, Tenant, TokenBlacklist, MobileSession
from app.config import settings

logger=logging.getLogger(__name__)
def now():return datetime.now(timezone.utc)
def digest(value):return hashlib.sha256(value.encode()).hexdigest()

def register(db,user,payload,access_payload):
    # Core lookup is intentionally global: a physical installation can change
    # company only with proof of its locally stored, random installation secret.
    table=Device.__table__;connection=db.connection()
    keys={payload.installation_id.int % (2**63-1),int(digest(payload.token)[:15],16)}
    for key in sorted(keys):
        connection.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':key})
    if access_payload.get('jti') and db.query(TokenBlacklist.id).filter_by(token_jti=access_payload['jti']).first():
        raise HTTPException(401,'La sesión ya no está vigente')
    if access_payload.get('mobile_session_id'):
        session=db.query(MobileSession).filter_by(id=access_payload['mobile_session_id']).populate_existing().first()
        if not session or session.revoked_at is not None or session.user_id!=user.id:
            raise HTTPException(401,'La sesión ya no está vigente')
    existing=connection.execute(select(table).where(table.c.id==payload.installation_id)).mappings().first()
    secret=digest(payload.installation_secret)
    if existing and not hmac.compare_digest(existing['secret_hash'],secret):
        raise HTTPException(409,'La instalación no está disponible')
    refresh_jti=None
    session_expires_at=None
    if payload.refresh_token:
        from app.core.security import decode_token
        try:
            claims=decode_token(payload.refresh_token)
            if (claims.get('type')!='refresh' or claims.get('sub')!=str(user.id)
                or claims.get('tenant_id')!=str(user.tenant_id)):raise ValueError()
            refresh_jti=claims['jti']
            session_expires_at=datetime.fromtimestamp(claims['exp'],timezone.utc)
            if db.query(TokenBlacklist.id).filter_by(token_jti=refresh_jti).first():raise ValueError()
        except Exception:raise HTTPException(400,'Sesión de registro no válida') from None
    if not access_payload.get('mobile_session_id') and not refresh_jti:
        raise HTTPException(400,'Se requiere una sesión vigente para registrar avisos')
    same=(existing and existing['active'] and existing['user_id']==user.id
        and existing['tenant_id']==user.tenant_id and existing['token_hash']==digest(payload.token))
    binding=existing['binding_id'] if same else uuid.uuid4()
    values=dict(binding_id=binding,secret_hash=secret,token_hash=digest(payload.token),token=payload.token,
        credential_hash=digest(user.hashed_password),
        platform=payload.platform,user_id=user.id,tenant_id=user.tenant_id,
        mobile_session_id=access_payload.get('mobile_session_id'),refresh_jti=refresh_jti,
        session_expires_at=session_expires_at,
        active=True,updated_at=now())
    # A rotated/reissued provider token must never retain another receiver.
    connection.execute(update(table).where(table.c.token_hash==digest(payload.token),
        table.c.id!=payload.installation_id).values(active=False))
    if existing:connection.execute(update(table).where(table.c.id==payload.installation_id).values(**values))
    else:connection.execute(table.insert().values(id=payload.installation_id,**values))
    db.commit()
    return {'installation_id':payload.installation_id,'binding_id':binding,'active':True}

def unregister(db,payload):
    table=Device.__table__;connection=db.connection()
    connection.execute(update(table).where(table.c.id==payload.installation_id,
        table.c.binding_id==payload.binding_id,table.c.secret_hash==digest(payload.installation_secret))
        .values(active=False,updated_at=now()))
    db.commit()
    # Identical response for missing or stale bindings; no ownership oracle.
    return {'active':False}

def enqueue(db,notification):
    devices=db.query(Device).filter_by(user_id=notification.user_id,active=True).all()
    for device in devices:
        db.add(Delivery(notification_id=notification.id,device_id=device.id,binding_id=device.binding_id))

def eligible(db,device,notification):
    user=db.get(User,device.user_id);tenant=db.get(Tenant,device.tenant_id)
    if (not device.active or not user or not user.is_active or not tenant or not tenant.activo
        or user.tenant_id!=device.tenant_id or notification.user_id!=user.id or user.is_platform_admin
        or device.credential_hash!=digest(user.hashed_password)):return False
    if device.mobile_session_id:
        session=db.get(MobileSession,device.mobile_session_id)
        if not session or session.revoked_at is not None or session.user_id!=user.id:return False
    elif (not device.refresh_jti or device.session_expires_at is None
          or device.session_expires_at<=now()):return False
    if device.refresh_jti and db.query(TokenBlacklist.id).filter_by(token_jti=device.refresh_jti).first():return False
    return True

_firebase_app=None
def firebase_send(device,notification):
    global _firebase_app
    import firebase_admin
    from firebase_admin import credentials,messaging
    if _firebase_app is None:
        raw=settings.FIREBASE_ADMIN_JSON.get_secret_value() if settings.FIREBASE_ADMIN_JSON else None
        if not raw:raise RuntimeError('FCM_NOT_CONFIGURED')
        certificate=json.loads(raw)
        if certificate.get('project_id')!=settings.FIREBASE_PROJECT_ID:raise RuntimeError('FCM_PROJECT_MISMATCH')
        _firebase_app=firebase_admin.initialize_app(credentials.Certificate(certificate),
            {'projectId':settings.FIREBASE_PROJECT_ID,'httpTimeout':10},name='fashionstore-push')
    # No private business information is placed on a locked screen.
    identifier=str(notification.id)
    message=messaging.Message(fid=device.token,
        notification=messaging.Notification(title='FashionStore',body='Tenés una actualización. Abrí Notificaciones para verla.'),
        data={'notification_id':identifier,'kind':notification.type,'binding_id':str(device.binding_id)},
        android=messaging.AndroidConfig(priority='high',ttl=timedelta(hours=1),
            notification=messaging.AndroidNotification(tag=identifier,channel_id='ficct_store_channel')),
        webpush=messaging.WebpushConfig(headers={'TTL':'3600'},
            notification=messaging.WebpushNotification(tag=identifier,renotify=False),
            fcm_options=messaging.WebpushFCMOptions(link=settings.NOTIFICATION_WEB_URL)))
    return messaging.send(message,app=_firebase_app)

def deliver_pending(factory,send=firebase_send,limit=20):
    completed=0
    for _ in range(limit):
        with factory() as db:
            row=(db.query(Delivery).filter(Delivery.status.in_(['PENDING','RETRY']),
                Delivery.next_attempt_at<=now()).order_by(Delivery.next_attempt_at,Delivery.id)
                .with_for_update(skip_locked=True,of=Delivery).first())
            if row is None:break
            device=db.get(Device,row.device_id);notification=db.get(Notification,row.notification_id)
            if (not device or not notification or row.binding_id!=device.binding_id
                or not eligible(db,device,notification)):
                row.status='SKIPPED';db.commit();continue
            row.attempts+=1
            try:
                send(device,notification)
                row.status='SENT';row.delivered_at=now();row.last_error=None;completed+=1
            except Exception as error:
                code=type(error).__name__
                permanent=code in {'UnregisteredError','SenderIdMismatchError','InvalidArgumentError'}
                row.last_error=code[:80]
                if permanent:device.active=False
                row.status='FAILED' if permanent or row.attempts>=6 else 'RETRY'
                row.next_attempt_at=now()+timedelta(seconds=min(300,5*2**row.attempts))
                logger.warning('FCM delivery %s: %s',row.id,code)
            db.commit()
    return completed
