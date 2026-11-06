"""Central subscription billing; never a sale or a commercial wallet entry."""
import uuid
from datetime import datetime,timezone
from sqlalchemy import Column,String,Numeric,DateTime,ForeignKey,CheckConstraint,event,Boolean
from sqlalchemy.dialects.postgresql import UUID,JSONB
from app.database import Base


def install_billing(target,connection,**kwargs):
    from pathlib import Path
    from app.core.plan_catalog_ddl import _execute_in_catalog_schema
    sql=(Path(__file__).resolve().parents[2]/'migrations/payments_03_subscription_billing.sql').read_text(encoding='utf-8')
    marker='-- TERM_GUARD\n' if target.name=='subscription_terms' else '-- GUARDS\n'
    sql=sql.split(marker,1)[1]
    if target.name!='subscription_terms':
        # The term table may not exist yet in a fresh translated schema.
        # Never let search_path fall through and alter public's existing table.
        sql=sql.split('-- TERM_GUARD\n',1)[0]
    _execute_in_catalog_schema(connection,target,sql)


class SubscriptionPayment(Base):
    __tablename__='subscription_payments'
    __contract_metadata__=True
    id=Column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    request_id=Column(UUID(as_uuid=True),ForeignKey('subscription_change_requests.id'),nullable=False,unique=True)
    tenant_id=Column(UUID(as_uuid=True),ForeignKey('tenants.id'),nullable=False,index=True)
    subscription_id=Column(UUID(as_uuid=True),ForeignKey('subscriptions.id'),nullable=False)
    version_id=Column(UUID(as_uuid=True),ForeignKey('subscription_plan_versions.id'),nullable=False)
    approved_by=Column(UUID(as_uuid=True),nullable=False)
    approved_at=Column(DateTime(timezone=True),nullable=False)
    expires_at=Column(DateTime(timezone=True),nullable=False)
    amount_bob=Column(Numeric(10,2),nullable=False)
    amount_usd=Column(Numeric(10,2),nullable=False)
    exchange_rate=Column(Numeric(14,6),nullable=False)
    merchant_key=Column(String(64),nullable=False)
    provider_mode=Column(String(10),nullable=False)
    state=Column(String(24),nullable=False,default='READY')
    provider_order_id=Column(String(100),unique=True)
    capture_id=Column(String(100),unique=True)
    captured_at=Column(DateTime(timezone=True))
    refund_id=Column(String(100),unique=True)
    approve_url=Column(String(1000))
    created_at=Column(DateTime(timezone=True),nullable=False,default=lambda:datetime.now(timezone.utc))
    updated_at=Column(DateTime(timezone=True),nullable=False,default=lambda:datetime.now(timezone.utc))
    __table_args__=(CheckConstraint("state IN ('READY','CREATING','CREATED','CAPTURING','CAPTURED','APPLIED','REFUND_PENDING','REFUNDED','REVIEW','EXPIRED')",name='ck_subscription_payment_state'),
        CheckConstraint("amount_bob>0 AND amount_bob<100000000 AND amount_usd>0 AND amount_usd<100000000 AND exchange_rate>0 AND exchange_rate<100000000 AND provider_mode IN ('sandbox','live') AND expires_at>approved_at",name='ck_subscription_payment_amount'))


class SubscriptionPaymentEvent(Base):
    __tablename__='subscription_payment_events'
    __contract_metadata__=True
    event_id=Column(String(128),primary_key=True)
    fingerprint=Column(String(64),nullable=False)
    payment_id=Column(UUID(as_uuid=True),ForeignKey('subscription_payments.id'),nullable=False)
    event_type=Column(String(80),nullable=False)
    processed=Column(Boolean,nullable=False,default=False)
    created_at=Column(DateTime(timezone=True),nullable=False,default=lambda:datetime.now(timezone.utc))


event.listen(SubscriptionPayment.__table__,'after_create',install_billing)
