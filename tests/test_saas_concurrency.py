"""Real PostgreSQL transactions; isolated schema in the validated test database."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from fastapi import HTTPException
from sqlalchemy import text, select, func
from sqlalchemy.orm import Session
from app.database import Base
from app.config import settings
from app.models.tenant import Tenant
from app.models.role import Role
from app.models.user import User
from app.models.platform_audit_log import PlatformAuditLog
from app.api.deps import get_authenticated_identity
from app.schemas.platform import PlatformCompanyStatus
from app.services.platform_company_service import PlatformCompanyService
from tests.conftest import test_engine


@pytest.mark.parametrize('same_operation',[True,False])
def test_status_concurrent_requests_have_one_effect(monkeypatch,same_operation):
    schema='saas_concurrency_'+uuid.uuid4().hex
    with test_engine.begin() as connection:
        connection.execute(text('CREATE SCHEMA '+schema))
    engine=test_engine.execution_options(schema_translate_map={None:schema})
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            operator=Tenant(nombre='Operator',razon_social='Operator',nit_rut='op',slug='operator',activo=True,es_operadora=True)
            company=Tenant(nombre='Shop',razon_social='Shop',nit_rut='shop',slug='shop',activo=True)
            db.add_all([operator,company]);db.flush()
            role=Role(name='superadmin_plataforma',tenant_id=operator.id)
            db.add(role);db.flush()
            actor=User(tenant_id=operator.id,role_id=role.id,email='concurrency@test.example.com',
                       full_name='Synthetic',hashed_password='not-a-login-hash',is_active=True)
            db.add(actor);db.commit()
            actor_id,company_id,operator_id=actor.id,company.id,operator.id
        monkeypatch.setattr(settings,'PLATFORM_ADMIN_USER_IDS',[str(actor_id)])
        barrier=Barrier(2);operation_id=uuid.uuid4()
        def request(index):
            with Session(engine) as db:
                actor=get_authenticated_identity(db,{'sub':str(actor_id),'tenant_id':str(operator_id)})
                payload=PlatformCompanyStatus(operation_id=operation_id if same_operation else uuid.uuid4(),
                    activo=False,expected_version=0,reason='Concurrent synthetic suspension')
                barrier.wait(timeout=10)
                try:
                    return 200,PlatformCompanyService.set_company_status(db,actor,company_id,payload)
                except HTTPException as error:
                    return error.status_code,None
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes=list(workers.map(request,range(2)))
        assert sorted(code for code,_ in outcomes)==([200,200] if same_operation else [200,409])
        if same_operation:assert outcomes[0][1]==outcomes[1][1]
        with Session(engine) as db:
            assert db.get(Tenant,company_id).activo is False
            assert db.get(Tenant,company_id).platform_revision==1
            assert db.scalar(select(func.count()).select_from(PlatformAuditLog))==1
    finally:
        with test_engine.begin() as connection:
            connection.execute(text('DROP SCHEMA '+schema+' CASCADE'))
