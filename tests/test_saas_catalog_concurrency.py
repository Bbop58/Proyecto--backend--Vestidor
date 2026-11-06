"""Independent PostgreSQL sessions, never the shared API savepoint fixture."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import text,select,func
from sqlalchemy.orm import Session
from app.database import Base
from app.config import settings
from app.models.tenant import Tenant
from app.models.role import Role
from app.models.user import User
from app.models.subscription_plan import SubscriptionPlan
from app.models.subscription_plan_version import SubscriptionPlanVersion
from app.models.platform_audit_log import PlatformAuditLog
from app.api.deps import get_authenticated_identity
from app.schemas.plan_catalog import PlanDraftCreate,PlanVersionDecision
from app.services.plan_catalog_service import PlanCatalogService
from tests.conftest import test_engine


@pytest.mark.parametrize('operation',['draft','publish'])
def test_concurrent_catalog_mutations_have_unique_revisions_and_one_publication(monkeypatch,operation):
    schema='saas_catalog_'+uuid.uuid4().hex
    with test_engine.begin() as connection:connection.execute(text('CREATE SCHEMA '+schema))
    engine=test_engine.execution_options(schema_translate_map={None:schema})
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            company=Tenant(nombre='Operator',razon_social='Operator',nit_rut='op',slug='op',activo=True,es_operadora=True)
            db.add(company);db.flush()
            role=Role(name='superadmin_plataforma',tenant_id=company.id);db.add(role);db.flush()
            actor=User(tenant_id=company.id,role_id=role.id,email='synthetic@catalog.example.com',
                full_name='Synthetic',hashed_password='not-a-password',is_active=True)
            plan=SubscriptionPlan(codigo='CONCURRENT',nombre='Concurrent',max_sucursales=1,catalog_managed=True)
            db.add_all([actor,plan]);db.commit()
            actor_id,company_id,plan_id=actor.id,company.id,plan.id
        monkeypatch.setattr(settings,'PLATFORM_ADMIN_USER_IDS',[str(actor_id)])
        payload=PlanDraftCreate(precio_mensual_bob='100.00',max_sucursales=2,
            features={'reportes_ia':False,'analitica_avanzada':True,'offline':True},reason='Concurrent catalog test')
        if operation=='publish':
            with Session(engine) as db:
                actor=get_authenticated_identity(db,{'sub':str(actor_id),'tenant_id':str(company_id)})
                version=PlanCatalogService.create_draft(db,actor,plan_id,payload)
                version_id=uuid.UUID(version['id'])
        barrier=Barrier(2)
        def perform(index):
            with Session(engine) as db:
                actor=get_authenticated_identity(db,{'sub':str(actor_id),'tenant_id':str(company_id)})
                barrier.wait(timeout=10)
                if operation=='draft':return PlanCatalogService.create_draft(db,actor,plan_id,payload)
                return PlanCatalogService.publish_version(db,actor,version_id,
                    PlanVersionDecision(expected_version=0,reason='Concurrent publication test'))
        with ThreadPoolExecutor(max_workers=2) as workers:results=list(workers.map(perform,range(2)))
        with Session(engine) as db:
            if operation=='draft':
                assert sorted(row['revision'] for row in results)==[1,2]
                assert db.scalar(select(func.count()).select_from(SubscriptionPlanVersion))==2
            else:
                assert results[0]==results[1] and results[0]['estado']=='PUBLICADA'
                assert db.scalar(select(func.count()).select_from(PlatformAuditLog).where(PlatformAuditLog.action=='PLAN_PUBLISH'))==1
    finally:
        with test_engine.begin() as connection:connection.execute(text('DROP SCHEMA '+schema+' CASCADE'))
