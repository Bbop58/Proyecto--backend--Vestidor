import uuid
from datetime import datetime,timezone
from sqlalchemy import Column,String,Integer,Boolean,Numeric,Text,DateTime,ForeignKey,UniqueConstraint,CheckConstraint,event
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base
from app.core.plan_catalog_ddl import install_catalog_schema


class SubscriptionPlanVersion(Base):
    __tablename__='subscription_plan_versions'
    id=Column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    plan_id=Column(UUID(as_uuid=True),ForeignKey('subscription_plans.id',ondelete='RESTRICT'),nullable=False)
    revision=Column(Integer,nullable=False)
    estado=Column(String(20),nullable=False,default='BORRADOR')
    lock_version=Column(Integer,nullable=False,default=0)
    precio_mensual_bob=Column(Numeric(10,2),nullable=False)
    moneda=Column(String(3),nullable=False,default='BOB')
    periodicidad=Column(String(10),nullable=False,default='MENSUAL')
    max_sucursales=Column(Integer,nullable=False)
    permite_reportes_ia=Column(Boolean,nullable=False)
    permite_analitica_avanzada=Column(Boolean,nullable=False)
    permite_offline=Column(Boolean,nullable=False)
    descripcion=Column(Text,nullable=True)
    origen=Column(String(25),nullable=False,default='PLATAFORMA')
    published_at=Column(DateTime(timezone=True),nullable=True)
    created_at=Column(DateTime(timezone=True),nullable=False,default=lambda:datetime.now(timezone.utc))
    __table_args__=(
        UniqueConstraint('plan_id','revision',name='uq_plan_version_revision'),
        CheckConstraint('revision>0 AND lock_version>=0 AND max_sucursales>=1',name='ck_plan_version_limits'),
        CheckConstraint("precio_mensual_bob>=0 AND precio_mensual_bob != 'NaN'::numeric",name='ck_plan_version_price'),
        CheckConstraint("moneda='BOB' AND periodicidad='MENSUAL'",name='ck_plan_version_billing'),
        CheckConstraint("estado IN ('BORRADOR','PUBLICADA','RETIRADA')",name='ck_plan_version_state'),
        CheckConstraint("origen IN ('PLATAFORMA','REFERENCIA_LEGADA')",name='ck_plan_version_origin'),
        CheckConstraint("(estado='BORRADOR' AND published_at IS NULL) OR (estado!='BORRADOR' AND published_at IS NOT NULL)",name='ck_plan_version_publication'),
    )


event.listen(SubscriptionPlanVersion.__table__,'after_create',install_catalog_schema)
