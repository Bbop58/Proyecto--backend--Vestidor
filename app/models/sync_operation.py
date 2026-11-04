import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class SyncOperation(Base):
    __tablename__ = "sync_operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    client_operation_id = Column(UUID(as_uuid=True), nullable=False)
    __table_args__ = (
        UniqueConstraint('client_operation_id', name='uq_sync_client_operation'),
        UniqueConstraint('supersedes_operation_id', name='uq_sync_successor'),
        CheckConstraint('supersedes_operation_id IS NULL OR supersedes_operation_id <> client_operation_id',name='ck_sync_not_self'),)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    
    request_fingerprint = Column(String(64), nullable=True)
    supersedes_operation_id = Column(UUID(as_uuid=True), ForeignKey('sync_operations.client_operation_id',name='fk_sync_supersedes'), nullable=True)
    operation_type = Column(String(50), nullable=False)  # 'VENTA_CONTADO_OFFLINE', 'RESERVA_OFFLINE'
    client_created_at = Column(DateTime(timezone=True), nullable=False)
    sync_status = Column(String(50), nullable=False)  # 'CONFIRMADA', 'RECHAZADA_POR_STOCK', 'CONFLICTO'
    
    entity_id = Column(UUID(as_uuid=True), nullable=True)  # ID de la venta o reserva creada
    response_payload = Column(JSONB, nullable=True)  # Respuesta cacheada para replay idempotente
    error_detail = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    tenant = relationship("Tenant", lazy="joined")
    branch = relationship("Branch", lazy="joined")
    user = relationship("User", lazy="joined")
