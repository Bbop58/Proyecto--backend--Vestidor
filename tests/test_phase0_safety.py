import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize("url", [
    "postgresql://audit@production.invalid/fashionstore_test_wrong",
    "postgresql://audit@127.0.0.1/business",
    "sqlite:///fashionstore_test_wrong",
    "postgresql://audit@127.0.0.1/fashionstore_test_wrong?host=production.invalid",
])
def test_test_database_guard_rejects_unsafe_targets(url):
    env = {**os.environ, "TEST_DATABASE_URL":url}
    result = subprocess.run([sys.executable,"-c", "import runpy; runpy.run_path('tests/conftest.py')"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True,text=True,timeout=20)
    assert result.returncode != 0
    assert "Use a disposable local" in result.stderr


def test_startup_does_not_touch_database_or_seed_by_default(monkeypatch):
    from app import main
    def forbidden(*args, **kwargs):
        pytest.fail("Default startup attempted schema mutation")
    monkeypatch.setattr(main.settings,"RUN_STARTUP_MIGRATIONS",False)
    monkeypatch.setattr(main.settings,"RUN_DEMO_SEED",False)
    monkeypatch.setattr(main.Base.metadata,"create_all",forbidden)
    monkeypatch.setattr(main.engine,"begin",forbidden)
    main.on_startup()
