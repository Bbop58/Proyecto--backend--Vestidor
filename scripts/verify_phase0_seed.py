"""Run only on an EMPTY disposable local fashionstore_test_ database."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from sqlalchemy.engine import make_url

url = os.environ.get("TEST_DATABASE_URL", "")
parsed = make_url(url)
if (parsed.get_backend_name() != "postgresql" or parsed.query
    or parsed.host not in {"127.0.0.1", "localhost", "::1"}
    or not (parsed.database or "").startswith("fashionstore_test_")):
    raise RuntimeError("Use an empty disposable local fashionstore_test_ database")
os.environ["DATABASE_URL"] = url
os.environ["RUN_STARTUP_MIGRATIONS"] = "false"
os.environ["RUN_DEMO_SEED"] = "false"
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, select
from app.database import Base, engine
import app.models
from app.seed_database import seed_all
from app.seed_audit import reconcile


def snapshot():
    with engine.connect() as c:
        hashes = {}
        counts = {}
        for table in Base.metadata.sorted_tables:
            rows = sorted(json.dumps(dict(r),sort_keys=True,default=str)
                          for r in c.execute(select(table)).mappings())
            hashes[table.name] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
            counts[table.name] = len(rows)
        return hashes,counts


if inspect(engine).get_table_names():
    raise RuntimeError("Refusing a nonempty database; create another disposable database")
Base.metadata.create_all(engine)
with contextlib.redirect_stdout(io.StringIO()):
    seed_all()
before,counts = snapshot()
with engine.connect() as c:
    checks = reconcile(c)
with contextlib.redirect_stdout(io.StringIO()):
    seed_all()
after,_ = snapshot()
result = {"counts":counts,"reconciliation":checks,
          "tables_changed_on_repeat":[name for name in before if before[name]!=after[name]],
          "wallet_note":"Seed has no wallets; positive cash top-up coverage is in test_seed_phase0.py"}
target = Path(sys.argv[1])
target.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
assert not any(checks.values()),checks
assert before == after,"Seed changed existing data"
print("PASS: initial seed reconciled; second execution identical across all tables")
