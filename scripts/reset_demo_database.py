"""Fresh + seed for the explicitly disposable Railway demo, not production."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _snapshot(connection, metadata):
    result = {}
    for table in metadata.sorted_tables:
        rows = sorted(json.dumps(dict(row), sort_keys=True, default=str)
                      for row in connection.execute(select(table)).mappings())
        result[table.name] = hashlib.sha256('\n'.join(rows).encode()).hexdigest()
    return result


def reset_demo_database(engine, operator_user_id=None, operator_password=None, tenant_paypal_config=None):
    if engine.dialect.name != 'postgresql':
        raise ValueError('Demo reset requires PostgreSQL')
    if operator_user_id is not None:
        import uuid
        operator_user_id=uuid.UUID(str(operator_user_id))
        if not isinstance(operator_password,str) or len(operator_password)<12:
            raise ValueError('An explicit private demo operator password is required before reset')
    from app.services.company_paypal_seed import validate_seed_config,seed_company_paypal
    tenant_paypal_config=validate_seed_config(tenant_paypal_config)
    from app.database import Base
    import app.models  # Register all current tables, enum types and constraints.
    from app import seed_database
    from app.seed_audit import reconcile

    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        connection.execute(text("SET LOCAL statement_timeout = '120s'"))
        connection.execute(text('SELECT pg_advisory_xact_lock(610134, 2)'))
        # Transactional PostgreSQL DDL: a seed/audit failure restores the old
        # schema and its data, including tables absent from today's metadata.
        connection.execute(text('DROP SCHEMA IF EXISTS public CASCADE'))
        connection.execute(text('CREATE SCHEMA public'))
        connection.execute(text('SET LOCAL search_path TO public'))
        Base.metadata.create_all(connection)

        def seed_once():
            with Session(bind=connection, autoflush=False,
                         join_transaction_mode='create_savepoint') as db:
                # Keep verbose demo output and SQL exception parameters out of logs.
                with contextlib.redirect_stdout(io.StringIO()):
                    seed_database.seed_all(db=db)
                    seed_company_paypal(db.connection(),tenant_paypal_config)
                    if operator_user_id is not None:
                        seed_database.seed_demo_operator(db,operator_user_id,operator_password)
                db.commit()  # Releases savepoint; only engine.begin commits the reset.

        seed_once()
        before = _snapshot(connection, Base.metadata)
        checks = reconcile(connection)
        if any(checks.values()):
            raise RuntimeError('Seed reconciliation failed; reset rolled back')
        seed_once()
        after = _snapshot(connection, Base.metadata)
        changed = [name for name in before if before[name] != after[name]]
        if changed:
            raise RuntimeError('Repeated seed changed existing data; reset rolled back')
        checks = reconcile(connection)
        if any(checks.values()):
            raise RuntimeError('Repeated seed reconciliation failed; reset rolled back')
        counts = {table.name: connection.execute(
            select(func.count()).select_from(table)
        ).scalar_one() for table in Base.metadata.sorted_tables}
    return {'checks': checks, 'tables_changed_on_repeat': changed, 'counts': counts}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if os.environ.get('BACKUP_WORKER_ONLY', '').strip().lower() in {'true', '1', 'yes'}:
        print('[demo reset] Refusing reset in a backup worker service; nothing was changed.', file=sys.stderr)
        return 2
    if argv != ['--confirm-disposable-demo']:
        print('[demo reset] Refusing reset without --confirm-disposable-demo.', file=sys.stderr)
        return 2
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        print('[demo reset] DATABASE_URL is required; nothing was changed.', file=sys.stderr)
        return 2
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    engine = None
    try:
        parsed = make_url(url)
        if parsed.get_backend_name() != 'postgresql' or not parsed.database or parsed.database in {'postgres','template0','template1'}:
            raise ValueError('Use the explicitly disposable application database')
        # No startup migrations or background seeder may race this command.
        os.environ['RUN_STARTUP_MIGRATIONS'] = 'false'
        os.environ['RUN_DEMO_SEED'] = 'false'
        engine = create_engine(url, pool_pre_ping=True, connect_args={'connect_timeout':15})
        result = reset_demo_database(engine,
            operator_user_id=os.environ.get('DEMO_OPERATOR_USER_ID') or None,
            operator_password=os.environ.get('DEMO_OPERATOR_PASSWORD'),
            tenant_paypal_config=json.loads(os.environ.get('DEMO_TENANT_PAYPAL_CONFIG','{}')))
    except Exception as error:
        print(f'[demo reset] FAILED ({type(error).__name__}); deployment must stop. '
              'Database transaction rolled back if started.', file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print(f"[demo reset] OK: fresh schema; seed repeated unchanged; {len(result['checks'])} checks passed.")
    print('[demo reset] Counts: ' + json.dumps(result['counts'], sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
