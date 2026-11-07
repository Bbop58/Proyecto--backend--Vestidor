import os
import secrets
os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["WALLET_SIGNING_KEY"] = secrets.token_urlsafe(48)
from sqlalchemy.engine import make_url
_test_url = os.environ.get("TEST_DATABASE_URL", "")
if _test_url:
    parsed = make_url(_test_url)
    if parsed.get_backend_name() != "postgresql" or parsed.query or not (parsed.database or "").startswith("fashionstore_test_") or parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Use a disposable local fashionstore_test_ database")
    os.environ["DATABASE_URL"] = _test_url
else:
    # Never load the project's real database URL, even during collection.
    os.environ["DATABASE_URL"] = "postgresql://invalid:invalid@127.0.0.1:1/fashionstore_test_unconfigured"
os.environ["RUN_STARTUP_MIGRATIONS"] = "false"
os.environ["RUN_DEMO_SEED"] = "false"
# Do not let a developer's .env CORS override determine API regressions.
os.environ["BACKEND_CORS_ORIGINS"] = '["http://localhost:4200","https://proyecto-frontend-vestidor-ten.vercel.app"]'
import pytest
from typing import Generator
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.main import app as fastapi_app
from app.database import Base, get_db
from app.config import settings
import app.models



# Test database URI
TEST_DATABASE_URL = _test_url or "postgresql://invalid:invalid@127.0.0.1:1/fashionstore_test_unconfigured"

test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    if not _test_url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable local fashionstore_test_ database")
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection,join_transaction_mode="create_savepoint")
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session: Session,request) -> Generator[TestClient, None, None]:
    def override_get_db():
        try:
            # Explicit contractual setup for historical HTTP suites, which
            # predate S3. Never run in product or in SaaS/entitlement tests.
            module=request.module.__name__.split('.')[-1]
            if (not module.startswith('test_saas_') and request.node.name!='test_subscription_query_does_not_provision_a_free_plan'
                    and not db_session.info.get('historical_contract_ready')):
                from app.models import Tenant,Subscription,SubscriptionPlan
                from app.core.plan_catalog_ddl import ensure_legacy_versions
                from app.core.subscription_contract_ddl import ensure_legacy_terms
                from datetime import date,timedelta
                from sqlalchemy import select
                connection=db_session.connection()
                companies=connection.execute(select(Tenant.id).where(Tenant.es_operadora.is_(False))).scalars().all()
                for company_id in companies:
                    if not connection.execute(select(Subscription.id).where(Subscription.tenant_id==company_id)).first():
                        plan=SubscriptionPlan(codigo='HTTP_FIXTURE_'+str(company_id).replace('-',''),nombre='Explicit HTTP test contract',
                            max_sucursales=20,precio_mensual_bob=0,permite_reportes_ia=True,permite_offline=True)
                        db_session.add(plan);db_session.flush()
                        db_session.add(Subscription(tenant_id=company_id,plan_id=plan.id,estado='ACTIVA',
                            fecha_inicio=date.today(),fecha_vencimiento=date.today()+timedelta(days=30)))
                db_session.flush();ensure_legacy_versions(connection);ensure_legacy_terms(connection)
                db_session.commit();db_session.info['historical_contract_ready']=True
            yield db_session
        except Exception:
            db_session.rollback()
            raise
        finally:
            if db_session.info.pop("scope_violation",False):
                db_session.rollback()
            db_session.info.pop("tenant_scope",None)
            db_session.info.pop("actor_id",None)
            db_session.info.pop("platform_actor_id",None)

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()

@pytest.fixture
def commercial_tenant(db_session):
    from app.models.tenant import Tenant
    tenant = Tenant(nombre="Empresa de pruebas",razon_social="Pruebas",nit_rut="audit-test",
                    slug="commercial-test",activo=True)
    db_session.add(tenant)
    db_session.commit()
    return tenant


@pytest.fixture(autouse=True)
def explicit_company_for_historical_tests(request,monkeypatch):
    # These suites predate mandatory company selection. Only their declared
    # registration fixtures are adapted; tenant-contract tests remain untouched.
    modules = {"test_auth","test_users","test_roles","test_phase1","test_phase2",
               "test_phase3","test_phase4","test_phase5","test_exchanges",
               "test_voice_reports_excel"}
    if request.module.__name__.split(".")[-1] not in modules:
        yield
        return
    tenant = request.getfixturevalue("commercial_tenant")
    db = request.getfixturevalue("db_session")
    from app.models.subscription_plan import SubscriptionPlan
    from app.models.subscription import Subscription
    from datetime import date,timedelta
    plan=SubscriptionPlan(codigo="TEST_PRO",nombre="Test Pro",max_sucursales=20,
        permite_reportes_ia=True,permite_offline=True,precio_mensual_bob=0,activo=True)
    db.add(plan);db.flush()
    db.add(Subscription(tenant_id=tenant.id,plan_id=plan.id,estado="ACTIVA",
        fecha_inicio=date.today(),fecha_vencimiento=date.today()+timedelta(days=30)))
    db.flush()
    from app.core.plan_catalog_ddl import ensure_legacy_versions
    from app.core.subscription_contract_ddl import ensure_legacy_terms
    ensure_legacy_versions(db.connection());ensure_legacy_terms(db.connection())
    db.commit()
    from app.models.season import Season
    from app.seed_permissions import SEASONS_SEED
    for data in SEASONS_SEED:
        db.add(Season(**data,tenant_id=tenant.id))
    db.commit()
    for name,value in vars(request.module).items():
        if name.isupper() and isinstance(value,dict) and {'email','password','full_name'}<=value.keys():
            monkeypatch.setitem(value,'tenant_id',str(tenant.id))
    yield
