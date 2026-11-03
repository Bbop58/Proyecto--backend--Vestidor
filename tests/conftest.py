import os
import secrets
os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
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
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise
        finally:
            if db_session.info.pop("scope_violation",False):
                db_session.rollback()
            db_session.info.pop("tenant_scope",None)
            db_session.info.pop("actor_id",None)

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()

@pytest.fixture(autouse=True)
def isolated_ml_artifacts(monkeypatch, tmp_path):
    from app.ml.supervised_model import DemandForecaster
    from app.ml.unsupervised_model import CustomerSegmenter
    from app.ml.ml_service import MLAnalyticsService
    for cls, name in [(DemandForecaster, "demand.joblib"), (CustomerSegmenter, "segments.joblib")]:
        original = cls.__init__
        def init(self, model_path=None, _original=original, _name=name):
            _original(self, model_path=str(tmp_path / _name))
        monkeypatch.setattr(cls, "__init__", init)
    monkeypatch.setattr(MLAnalyticsService, "_forecaster", DemandForecaster())
    monkeypatch.setattr(MLAnalyticsService, "_segmenter", CustomerSegmenter())


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
               "test_analytics_ml","test_voice_reports_excel"}
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
