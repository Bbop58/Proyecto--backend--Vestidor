import uuid
from datetime import datetime,timezone
from sqlalchemy import Column,String,DateTime,ForeignKey,Boolean
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base

class CompanyInvitation(Base):
    __tablename__='company_invitations'
    __operational_metadata__=True
    id=Column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id=Column(UUID(as_uuid=True),ForeignKey('tenants.id',ondelete='CASCADE'),nullable=False,unique=True)
    created_by=Column(UUID(as_uuid=True),ForeignKey('users.id'),nullable=False)
    operation_id=Column(UUID(as_uuid=True),nullable=False,unique=True)
    fingerprint=Column(String(64),nullable=False)
    owner_email=Column(String(255),nullable=False,unique=True)
    owner_name=Column(String(255),nullable=False)
    token_hash=Column(String(64),nullable=False,unique=True)
    expires_at=Column(DateTime(timezone=True),nullable=False)
    accepted_at=Column(DateTime(timezone=True))
    email_sent=Column(Boolean,default=False,nullable=False)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),nullable=False)
