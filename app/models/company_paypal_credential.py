import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, event
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class CompanyPayPalCredential(Base):
    __tablename__ = 'company_paypal_credentials'
    __contract_metadata__ = True  # Company snapshots reference settings; never import secrets.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey('tenants.id'), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False)
    client_id = Column(String(255), nullable=True)
    mode = Column(String(10), nullable=True)
    merchant_key = Column(String(64), nullable=True)
    secret_ciphertext = Column(Text, nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint('tenant_id', 'revision', name='uq_company_paypal_revision'),
        CheckConstraint('revision > 0', name='ck_company_paypal_revision'),
        CheckConstraint("(enabled AND client_id IS NOT NULL AND mode IS NOT NULL AND mode IN ('sandbox','live') AND merchant_key IS NOT NULL AND secret_ciphertext IS NOT NULL) OR (NOT enabled AND client_id IS NULL AND mode IS NULL AND merchant_key IS NULL AND secret_ciphertext IS NULL)", name='ck_company_paypal_shape'),
    )


@event.listens_for(CompanyPayPalCredential.__table__, 'after_create')
def install_guards(target, connection, **kwargs):
    from app.core.plan_catalog_ddl import _execute_in_catalog_schema
    _execute_in_catalog_schema(connection,target,(Path(__file__).resolve().parents[2] / 'migrations/payments_02_company_credentials.sql').read_text(encoding='utf-8').split('-- Permission grant')[0])
