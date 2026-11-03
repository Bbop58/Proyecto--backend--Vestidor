import os
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
    session = TestingSessionLocal(bind=connection)
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

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
