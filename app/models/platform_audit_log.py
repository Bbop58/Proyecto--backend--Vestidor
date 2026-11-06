import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class PlatformAuditLog(Base):
    __tablename__ = 'platform_audit_logs'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    action = Column(String(80), nullable=False)
    target_type = Column(String(30), nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    reason = Column(String(500), nullable=False)
    before_data = Column(JSONB, nullable=False, default=dict)
    after_data = Column(JSONB, nullable=False, default=dict)
    operation_id = Column(UUID(as_uuid=True), nullable=True)
    request_fingerprint = Column(String(64), nullable=True)
    result_payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint('actor_id', 'operation_id', name='uq_platform_actor_operation'),)
