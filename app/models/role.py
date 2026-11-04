import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.role_permission import role_permissions


def utc_now():
    return datetime.now(timezone.utc)


class Role(Base):
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(50), index=True, nullable=False)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # A sale/exchange may join several users. Joining each permission collection
    # multiplies rows (buyer x cashier x exchange staff); fetch collections once.
    permissions = relationship("Permission", secondary=role_permissions, backref="roles", lazy="selectin")
    users = relationship("User", back_populates="role")

    __table_args__ = (UniqueConstraint("tenant_id","name",name="uq_roles_tenant_name"),
        Index("uq_roles_global_name","name",unique=True,postgresql_where=text("tenant_id IS NULL")),)

    @property
    def is_shared(self):
        return self.tenant_id is None
