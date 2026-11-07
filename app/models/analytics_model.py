import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, LargeBinary, DateTime, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class AnalyticsModel(Base):
    __tablename__ = 'analytics_models'
    __contract_metadata__ = True  # Derived operational analysis, never a commercial snapshot.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False)
    kind = Column(String(12), nullable=False)
    artifact = Column(LargeBinary, nullable=False)
    artifact_sha256 = Column(String(64), nullable=False)
    result = Column(JSONB, nullable=False)
    model_metadata = Column(JSONB, nullable=False)
    trained_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint('tenant_id', 'kind', name='uq_analytics_company_kind'),
        CheckConstraint("kind IN ('FORECAST','RFM')", name='ck_analytics_kind'),
        CheckConstraint('octet_length(artifact) BETWEEN 1 AND 4194304', name='ck_analytics_artifact_size'),
    )
