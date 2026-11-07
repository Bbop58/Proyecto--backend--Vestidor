"""Explicit, transactional ML metadata upgrade. No startup migration or demo seed."""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, inspect, text
from scripts.migrate_saas import validate_target


def migrate_connection(connection):
    if not connection.in_transaction(): raise ValueError('Explicit transaction required')
    from app.models import AnalyticsModel
    schema = connection.scalar(text('SELECT current_schema()'))
    inspector = inspect(connection)
    if not all(inspector.has_table(name, schema=schema) for name in ('tenants','roles','permissions','role_permissions')):
        raise ValueError('Existing business schema required')
    connection.execute(text("SET LOCAL lock_timeout='10s'"))
    connection.execute(text('SELECT pg_advisory_xact_lock(610134,8)'))
    AnalyticsModel.__table__.create(connection.execution_options(schema_translate_map={None:schema}), checkfirst=True)
    connection.execute(text("""INSERT INTO permissions(id,code,module,method,path,description,created_at)
        VALUES ('c04d2105-c1da-4fe6-9f07-69e94d75be49','analytics.train','Analítica','POST',
        '/api/v1/analytics/train','Actualizar análisis de empresa propia',CURRENT_TIMESTAMP)
        ON CONFLICT(code) DO NOTHING"""))
    connection.execute(text("""INSERT INTO role_permissions(role_id,permission_id)
        SELECT r.id,p.id FROM roles r CROSS JOIN permissions p LEFT JOIN tenants t ON t.id=r.tenant_id
        WHERE r.name='admin' AND (r.tenant_id IS NULL OR NOT t.es_operadora)
        AND p.code='analytics.train' ON CONFLICT DO NOTHING"""))
    return {'tables':['analytics_models'], 'seed_executed':False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--allow-nonlocal-database', action='store_true')
    args = parser.parse_args(argv)
    if not args.apply: parser.error('--apply required after review and backup')
    engine = None
    try:
        url = os.environ.get('DATABASE_URL',''); validate_target(url,args.allow_nonlocal_database)
        engine = create_engine(url,connect_args={'connect_timeout':15})
        with engine.begin() as connection: print(json.dumps(migrate_connection(connection)))
        return 0
    except Exception as error:
        print('Analytics migration failed: '+type(error).__name__+'; transaction rolled back',file=sys.stderr)
        return 1
    finally:
        if engine is not None: engine.dispose()


if __name__ == '__main__': sys.exit(main())
