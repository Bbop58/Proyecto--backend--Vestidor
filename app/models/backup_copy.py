import uuid
from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class BackupCopy(Base):
    __tablename__ = 'backup_copies'
    __contract_metadata__ = True
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id = Column(UUID(as_uuid=True), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    due_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True))
    status = Column(String(10), nullable=False)
    object_key = Column(String(500), nullable=False)
    checksum = Column(String(64))
    size = Column(Integer)
    schema_version = Column(String(20))
    error_code = Column(String(50))
    __table_args__ = (
        ForeignKeyConstraint(['policy_id', 'tenant_id'], ['backup_policies.id', 'backup_policies.tenant_id']),
        UniqueConstraint('policy_id', 'due_at', name='uq_backup_copy_window'),
        CheckConstraint("status IN ('RUNNING','SUCCESS','FAILED','DELETING','DELETED')", name='ck_backup_copy_status'),
        CheckConstraint("status <> 'SUCCESS' OR (checksum IS NOT NULL AND size BETWEEN 1 AND 10485760 AND schema_version IS NOT NULL AND completed_at IS NOT NULL)", name='ck_backup_copy_success'),
    )
