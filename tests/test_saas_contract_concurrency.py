import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Event
import time
from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import text,select,func
from sqlalchemy.orm import Session
from app.database import Base
from app.config import settings
from app.models import Tenant,Role,User,SubscriptionPlan,Subscription,Branch,SubscriptionTerm,PlatformAuditLog
from app.api.deps import get_authenticated_identity
from app.core.billing_calendar import business_today
from app.core.plan_catalog_ddl import ensure_legacy_versions
from app.core.subscription_contract_ddl import ensure_legacy_terms
from app.seed_permissions import seed
from app.services.subscription_contract_service import SubscriptionContractService as Service
from app.services.plan_catalog_service import PlanCatalogService
from app.services.branch_service import BranchService
from app.schemas.subscription_contract import ContractChangeRequest,ContractDecision
from app.schemas.plan_catalog import PlanDraftCreate,PlanVersionDecision
from app.schemas.branch import BranchCreate
from tests.conftest import test_engine
from tests.test_saas_subscription_billing import provider
from app.services.subscription_billing_service import SubscriptionBillingService as Billing


@pytest.mark.parametrize('mode',['same-key','different-keys','sibling-requests','branch-race','billing-create','billing-capture','billing-backup-lock'])
def test_contract_approvals_are_serialized_and_quota_is_atomic(monkeypatch,mode,provider):
    schema='saas_contract_'+uuid.uuid4().hex
    with test_engine.begin() as connection:connection.execute(text('CREATE SCHEMA '+schema))
    engine=test_engine.execution_options(schema_translate_map={None:schema})
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            seed(db,include_seasons=False)
            company=Tenant(nombre='Shop',razon_social='Shop',nit_rut='s',slug='shop',activo=True)
            operator_company=Tenant(nombre='Operator',razon_social='Operator',nit_rut='o',slug='operator',activo=True,es_operadora=True)
            db.add_all([company,operator_company]);db.flush()
            reserved=Role(name='superadmin_plataforma',tenant_id=operator_company.id);db.add(reserved);db.flush()
            role=db.query(Role).filter_by(name='admin').one()
            admin=User(tenant_id=company.id,role_id=role.id,email='admin@synthetic.example.com',full_name='Synthetic',hashed_password='no-login',is_active=True,is_verified=True)
            operator=User(tenant_id=operator_company.id,role_id=reserved.id,email='operator@synthetic.example.com',full_name='Synthetic',hashed_password='no-login',is_active=True,is_verified=True)
            plan=SubscriptionPlan(codigo='CONTRACT',nombre='Contract',max_sucursales=4,precio_mensual_bob=100)
            db.add_all([admin,operator,plan]);db.flush();ensure_legacy_versions(db.connection())
            sub=Subscription(tenant_id=company.id,plan_id=plan.id,fecha_inicio=business_today()-timedelta(days=2),fecha_vencimiento=business_today()+timedelta(days=10))
            db.add(sub)
            if mode=='branch-race':
                for i in range(2):db.add(Branch(tenant_id=company.id,nombre=f'Branch {i}',ciudad='SC',direccion='Test',activa=True))
            db.flush();ensure_legacy_terms(db.connection());db.commit()
            admin_id,operator_id,company_id,operator_tenant_id,plan_id=admin.id,operator.id,company.id,operator_company.id,plan.id
        monkeypatch.setattr(settings,'PLATFORM_ADMIN_USER_IDS',[str(operator_id)])
        with Session(engine) as db:
            actor=get_authenticated_identity(db,{'sub':str(operator_id),'tenant_id':str(operator_tenant_id)})
            version=PlanCatalogService.create_draft(db,actor,plan_id,PlanDraftCreate(precio_mensual_bob='100.00',
                max_sucursales=2 if mode=='branch-race' else 4,features={'reportes_ia':False,'analitica_avanzada':True,'offline':True},reason='Synthetic offer'))
            PlanCatalogService.publish_version(db,actor,uuid.UUID(version['id']),PlanVersionDecision(expected_version=0,reason='Synthetic publication'))
        with Session(engine) as db:
            actor=get_authenticated_identity(db,{'sub':str(admin_id),'tenant_id':str(company_id)})
            quoted=Service.quote(db,actor,uuid.UUID(version['id']),'RENOVACION')
            # Exercise the historical MANUAL contract race; P3 billing races
            # have their own provider/reconciliation suite.
            from tests.test_saas_subscription_contracts import legacy_quote
            if not mode.startswith('billing'):quoted=legacy_quote(quoted)
            first=Service.request_change(db,actor,ContractChangeRequest(quote_token=quoted['quote_token'],acceptance=True,idempotency_key=uuid.uuid4()))
            second=Service.request_change(db,actor,ContractChangeRequest(quote_token=quoted['quote_token'],acceptance=True,idempotency_key=uuid.uuid4())) if mode=='sibling-requests' else first
        if mode.startswith('billing'):
            with Session(engine) as db:
                operator=get_authenticated_identity(db,{'sub':str(operator_id),'tenant_id':str(operator_tenant_id)})
                approved=Service.decide_request(db,operator,uuid.UUID(first['id']),ContractDecision(decision='APROBADA',expected_version=0,
                    idempotency_key=uuid.uuid4(),reason='Approved billing concurrency'))
                payment_id=uuid.UUID(approved['billing_payment_id'])
            if mode=='billing-capture':
                with Session(engine) as db:
                    manager=get_authenticated_identity(db,{'sub':str(admin_id),'tenant_id':str(company_id)})
                    Billing.create(db,manager,payment_id)
            if mode=='billing-backup-lock':
                ready=Event();worker_pid=[]
                def blocked_creator():
                    with Session(engine) as db:
                        manager=get_authenticated_identity(db,{'sub':str(admin_id),'tenant_id':str(company_id)})
                        worker_pid.append(db.connection().execute(select(func.pg_backend_pid())).scalar_one());ready.set()
                        return Billing.create(db,manager,payment_id)
                with ThreadPoolExecutor(max_workers=1) as workers:
                    with engine.begin() as backup:
                        backup.exec_driver_sql('LOCK TABLE '+schema+'.subscription_payments IN SHARE ROW EXCLUSIVE MODE')
                        future=workers.submit(blocked_creator);assert ready.wait(timeout=15)
                        blocked=False
                        for _ in range(100):
                            blocked=bool(backup.execute(text('SELECT cardinality(pg_blocking_pids(:pid))'),{'pid':worker_pid[0]}).scalar_one())
                            if blocked:break
                            time.sleep(.02)
                        assert blocked,'The writer must be waiting for the backup table lock'
                        # If the writer locked its tenant first, this NOWAIT fails:
                        # a real restore would deadlock waiting for that parent.
                        backup.execute(select(Tenant.id).where(Tenant.id==company_id).with_for_update(key_share=True,nowait=True))
                    assert future.result(timeout=20)['state']=='CREATED'
                assert len(provider[1]['create'])==1
                return
        barrier=Barrier(2);key=uuid.uuid4()
        def perform(index):
            with Session(engine) as db:
                is_branch=mode=='branch-race' and index==1
                commercial=is_branch or mode.startswith('billing')
                actor=get_authenticated_identity(db,{'sub':str(admin_id if commercial else operator_id),'tenant_id':str(company_id if commercial else operator_tenant_id)})
                barrier.wait(timeout=15)
                try:
                    if mode.startswith('billing'):
                        result=(Billing.create if mode=='billing-create' else Billing.capture)(db,actor,payment_id)
                        return result['state']
                    if is_branch:
                        BranchService.create(db,BranchCreate(nombre='Concurrent third',ciudad='SC',direccion='Test',activa=True),tenant_id=company_id)
                        return 201
                    request=first if index==0 else second
                    Service.decide_request(db,actor,uuid.UUID(request['id']),ContractDecision(decision='APROBADA',expected_version=0,
                        idempotency_key=key if mode=='same-key' else uuid.uuid4(),reason='Concurrent manual approval'))
                    return 200
                except HTTPException as error:return error.status_code
        with ThreadPoolExecutor(max_workers=2) as workers:codes=list(workers.map(perform,range(2)))
        with Session(engine) as db:
            manual=db.scalar(select(func.count()).select_from(SubscriptionTerm).where(SubscriptionTerm.origen=='MANUAL'))
            if mode.startswith('billing'):
                expected='CREATED' if mode=='billing-create' else 'APPLIED'
                assert codes==[expected,expected]
                assert len(provider[1]['create'])==1 and len(provider[1]['capture'])==(1 if mode=='billing-capture' else 0)
                assert db.scalar(select(func.count()).select_from(SubscriptionTerm).where(SubscriptionTerm.origen=='PAYPAL'))==(1 if mode=='billing-capture' else 0)
            elif mode=='same-key':assert codes==[200,200] and manual==1
            elif mode=='branch-race':
                # Approval checks committed usage under the same mutex. If it
                # wins first, later creation still has today's four-branch
                # entitlement; the future downgrade must not apply early.
                assert codes in ([200,201],[409,201])
                assert manual==(1 if codes[0]==200 else 0)
                assert db.scalar(select(func.count()).select_from(Branch))==3
            else:assert sorted(codes)==[200,409] and manual==1
    finally:
        with test_engine.begin() as connection:connection.execute(text('DROP SCHEMA '+schema+' CASCADE'))
