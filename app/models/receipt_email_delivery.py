import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class ReceiptEmailDelivery(Base):
    __tablename__='receipt_email_deliveries'
    __operational_metadata__=True
    __table_args__=(
        UniqueConstraint('tenant_id','actor_id','operation_id',name='uq_receipt_email_operation'),
        CheckConstraint("status IN ('PENDING','ACCEPTED','FAILED','UNKNOWN')",name='ck_receipt_email_status'),
        Index('ix_receipt_email_actor_created','tenant_id','actor_id','created_at'),
    )
    id=Column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id=Column(UUID(as_uuid=True),ForeignKey('tenants.id',ondelete='CASCADE'),nullable=False)
    # The user UUID is retained across commercial restore. A cascading user FK
    # would erase accepted-send receipts and permit duplicate email on replay.
    actor_id=Column(UUID(as_uuid=True),nullable=False)
    operation_id=Column(UUID(as_uuid=True),nullable=False)
    kind=Column(String(20),nullable=False)
    entity_id=Column(UUID(as_uuid=True),nullable=False)
    recipient=Column(String(320),nullable=False)
    request_hash=Column(String(64),nullable=False)
    pdf_hash=Column(String(64),nullable=False)
    status=Column(String(12),nullable=False,default='PENDING')
    created_at=Column(DateTime(timezone=True),nullable=False,default=lambda:datetime.now(timezone.utc))
    updated_at=Column(DateTime(timezone=True),nullable=False,default=lambda:datetime.now(timezone.utc))
