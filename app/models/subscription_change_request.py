import uuid
from datetime import datetime,timezone
from sqlalchemy import Column,String,Integer,DateTime,ForeignKey,UniqueConstraint,CheckConstraint
from sqlalchemy.dialects.postgresql import UUID,JSONB
from app.database import Base


class SubscriptionChangeRequest(Base):
    __tablename__='subscription_change_requests'
    __contract_metadata__=True
    id=Column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id=Column(UUID(as_uuid=True),ForeignKey('tenants.id',ondelete='RESTRICT'),nullable=False)
    subscription_id=Column(UUID(as_uuid=True),ForeignKey('subscriptions.id',ondelete='RESTRICT'),nullable=False)
    version_id=Column(UUID(as_uuid=True),ForeignKey('subscription_plan_versions.id',ondelete='RESTRICT'),nullable=False)
    kind=Column(String(12),nullable=False)
    quote_payload=Column(JSONB,nullable=False)
    accepted_by=Column(UUID(as_uuid=True),nullable=False)
    accepted_at=Column(DateTime(timezone=True),nullable=False)
    quote_expires_at=Column(DateTime(timezone=True),nullable=False)
    estado=Column(String(12),nullable=False,default='PENDIENTE')
    lock_version=Column(Integer,nullable=False,default=0)
    idempotency_key=Column(UUID(as_uuid=True),nullable=False)
    fingerprint=Column(String(64),nullable=False)
    decision_key=Column(UUID(as_uuid=True),nullable=True)
    decision_fingerprint=Column(String(64),nullable=True)
    decided_by=Column(UUID(as_uuid=True),nullable=True)
    decided_at=Column(DateTime(timezone=True),nullable=True)
    reason=Column(String(500),nullable=True)
    decision_result=Column(JSONB,nullable=True)
    __table_args__=(UniqueConstraint('tenant_id','idempotency_key',name='uq_contract_request_key'),
        UniqueConstraint('decided_by','decision_key',name='uq_contract_decision_key'),
        CheckConstraint("estado IN ('PENDIENTE','APROBADA','RECHAZADA','CANCELADA') AND lock_version>=0",name='ck_contract_request_state'),
        CheckConstraint("kind IN ('ALTA','RENOVACION','CAMBIO')",name='ck_contract_request_kind'))
