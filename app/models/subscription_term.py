import uuid
from sqlalchemy import Column,String,Integer,Date,DateTime,Numeric,ForeignKey,UniqueConstraint,CheckConstraint,event
from sqlalchemy.dialects.postgresql import UUID,JSONB
from app.database import Base
from app.core.subscription_contract_ddl import install_contract_schema


class SubscriptionTerm(Base):
    __tablename__='subscription_terms'
    __contract_metadata__=True
    id=Column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id=Column(UUID(as_uuid=True),ForeignKey('tenants.id',ondelete='RESTRICT'),nullable=False)
    subscription_id=Column(UUID(as_uuid=True),ForeignKey('subscriptions.id',ondelete='RESTRICT'),nullable=False)
    version_id=Column(UUID(as_uuid=True),ForeignKey('subscription_plan_versions.id',ondelete='RESTRICT'),nullable=True)
    request_id=Column(UUID(as_uuid=True),ForeignKey('subscription_change_requests.id',ondelete='RESTRICT'),nullable=True,unique=True)
    fecha_inicio=Column(Date,nullable=False)
    fecha_fin=Column(Date,nullable=False)
    anchor_day=Column(Integer,nullable=False)
    precio_mensual_bob=Column(Numeric(10,2),nullable=True)
    moneda=Column(String(3),nullable=False,default='BOB')
    periodicidad=Column(String(10),nullable=False)
    max_sucursales=Column(Integer,nullable=False)
    features=Column(JSONB,nullable=False)
    origen=Column(String(25),nullable=False)
    estado=Column(String(12),nullable=False,default='CONFIRMADO')
    accepted_by=Column(UUID(as_uuid=True),nullable=True)
    accepted_at=Column(DateTime(timezone=True),nullable=True)
    approved_by=Column(UUID(as_uuid=True),nullable=True)
    approved_at=Column(DateTime(timezone=True),nullable=True)
    __table_args__=(UniqueConstraint('subscription_id','fecha_inicio',name='uq_term_start'),
        CheckConstraint("fecha_fin>fecha_inicio AND anchor_day BETWEEN 1 AND 31 AND max_sucursales>=1",name='ck_term_dates'),
        CheckConstraint("moneda='BOB' AND estado='CONFIRMADO' AND periodicidad IN ('MENSUAL','LEGADA')",name='ck_term_billing'),
        CheckConstraint("precio_mensual_bob IS NULL OR (precio_mensual_bob>=0 AND precio_mensual_bob!='NaN'::numeric)",name='ck_term_price'),
        CheckConstraint("(origen='REFERENCIA_LEGADA' AND precio_mensual_bob IS NULL AND accepted_by IS NULL AND periodicidad='LEGADA') OR (origen IN ('MANUAL','PAYPAL') AND precio_mensual_bob IS NOT NULL AND request_id IS NOT NULL AND accepted_by IS NOT NULL AND accepted_at IS NOT NULL AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND periodicidad='MENSUAL')",name='ck_term_origin'))


event.listen(SubscriptionTerm.__table__,'after_create',install_contract_schema)
