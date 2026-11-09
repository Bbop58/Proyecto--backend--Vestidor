import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base

def now():return datetime.now(timezone.utc)

class NotificationDevice(Base):
    __tablename__='notification_devices'
    __operational_metadata__=True
    id=Column(UUID(as_uuid=True),primary_key=True)
    binding_id=Column(UUID(as_uuid=True),nullable=False,default=uuid.uuid4)
    secret_hash=Column(String(64),nullable=False)
    credential_hash=Column(String(64),nullable=False)
    token_hash=Column(String(64),nullable=False,index=True)
    token=Column(Text,nullable=False)  # Firebase Installation ID (FID), not a login credential.
    platform=Column(String(12),nullable=False)
    user_id=Column(UUID(as_uuid=True),ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    tenant_id=Column(UUID(as_uuid=True),ForeignKey('tenants.id',ondelete='CASCADE'),nullable=False,index=True)
    mobile_session_id=Column(UUID(as_uuid=True),ForeignKey('mobile_sessions.id',ondelete='CASCADE'),nullable=True)
    refresh_jti=Column(String(255),nullable=True)
    session_expires_at=Column(DateTime(timezone=True),nullable=True)
    active=Column(Boolean,nullable=False,default=True)
    updated_at=Column(DateTime(timezone=True),nullable=False,default=now)

class NotificationPushDelivery(Base):
    __tablename__='notification_push_deliveries'
    __operational_metadata__=True
    __table_args__=(UniqueConstraint('notification_id','device_id',name='uq_notification_device_delivery'),)
    id=Column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    notification_id=Column(UUID(as_uuid=True),ForeignKey('notifications.id',ondelete='CASCADE'),nullable=False,index=True)
    device_id=Column(UUID(as_uuid=True),ForeignKey('notification_devices.id',ondelete='CASCADE'),nullable=False,index=True)
    binding_id=Column(UUID(as_uuid=True),nullable=False)
    status=Column(String(12),nullable=False,default='PENDING',index=True)
    attempts=Column(Integer,nullable=False,default=0)
    next_attempt_at=Column(DateTime(timezone=True),nullable=False,default=now,index=True)
    delivered_at=Column(DateTime(timezone=True),nullable=True)
    last_error=Column(String(80),nullable=True)
    device=relationship('NotificationDevice')
