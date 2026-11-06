import uuid
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class BackupPolicy(Base):
    __tablename__ = 'backup_policies'
    __contract_metadata__ = True  # Operational state is never exported/restored.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey('tenants.id'), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=False)
    interval_hours = Column(Integer, nullable=False, default=48)
    next_run_at = Column(DateTime(timezone=True))
    last_success_at = Column(DateTime(timezone=True))
    last_error_code = Column(String(50))
    retry_count = Column(Integer, nullable=False, default=0)
    revision = Column(Integer, nullable=False, default=0)
    __table_args__ = (
        UniqueConstraint('id', 'tenant_id', name='uq_backup_policy_owner'),
        CheckConstraint('interval_hours BETWEEN 1 AND 168 OR (interval_hours BETWEEN 192 AND 720 AND interval_hours % 24 = 0)', name='ck_backup_interval'),
        CheckConstraint('revision >= 0 AND retry_count BETWEEN 0 AND 2', name='ck_backup_policy_counters'),
        CheckConstraint('(enabled AND next_run_at IS NOT NULL) OR (NOT enabled AND next_run_at IS NULL)', name='ck_backup_policy_schedule'),
    )
