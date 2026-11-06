"""Explicit transactional SaaS upgrade. No app/settings import, seed, startup or reset."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from sqlalchemy import create_engine,inspect,text
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[1]
MIGRATIONS=tuple(ROOT/'migrations'/name for name in (
 'saas_01_platform_scope.sql','saas_02_contract_catalog.sql','saas_03_subscription_terms.sql'))


def validate_target(url,allow_nonlocal=False):
    parsed=make_url(url)
    if parsed.get_backend_name()!='postgresql' or parsed.query or not parsed.database or parsed.database in {'postgres','template0','template1'}:
        raise ValueError('Explicit PostgreSQL application database without alternate connection parameters required')
    local=parsed.host in {'127.0.0.1','localhost','::1'} and parsed.database.startswith('fashionstore_test_')
    if not local and not allow_nonlocal:raise ValueError('Nonlocal maintenance requires explicit authorization and --allow-nonlocal-database')
    return parsed


def migrate_connection(connection):
    if not connection.in_transaction():raise ValueError('An explicit owning transaction is required')
    schema=connection.execute(text('SELECT current_schema()')).scalar_one()
    required={'tenants':{'id'},'roles':{'id','tenant_id'},'users':{'id','tenant_id','role_id'},
              'permissions':{'id','code'},'role_permissions':{'role_id','permission_id'},
              'subscriptions':{'id','tenant_id','plan_id'},'subscription_plans':{'id','codigo'}}
    inspector=inspect(connection)
    for table,columns in required.items():
        if not inspector.has_table(table,schema=schema) or not columns<={c['name'] for c in inspector.get_columns(table,schema=schema)}:
            raise ValueError('Missing legacy prerequisite; migrate role tenancy first, no seed was executed')
    connection.execute(text("SET LOCAL lock_timeout='10s'"))
    connection.execute(text("SET LOCAL statement_timeout='120s'"))
    connection.execute(text('SELECT pg_advisory_xact_lock(610134,6)'))
    applied=[]
    for path in MIGRATIONS:
        data=path.read_bytes();connection.exec_driver_sql(data.decode('utf-8'))
        applied.append({'name':path.name,'sha256':hashlib.sha256(data).hexdigest()})
    # A repeated historical SaaS upgrade must not replace installed P3 guards
    # with the older MANUAL-only term function.
    if inspect(connection).has_table('subscription_payments',schema=schema):
        # Explicit maintenance already selected its catalog schema/search_path.
        # Do not force public when the caller is maintaining a separate schema.
        sql=(ROOT/'migrations/payments_03_subscription_billing.sql').read_text(encoding='utf-8')
        connection.exec_driver_sql(sql.split('-- GUARDS\n',1)[1])
    return {'schema_generation':'saas-3','migrations':applied,'seed_executed':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Apply reviewed SQL in one transaction')
    parser.add_argument('--allow-nonlocal-database',action='store_true')
    args=parser.parse_args(argv)
    if not args.apply:parser.error('--apply is required; review SQL and create a backup before maintenance')
    engine=None
    try:
        url=os.environ.get('DATABASE_URL','');validate_target(url,args.allow_nonlocal_database)
        engine=create_engine(url,connect_args={'connect_timeout':15})
        with engine.begin() as connection:result=migrate_connection(connection)
        print(json.dumps(result));return 0
    except Exception as error:
        print('[saas migration] FAILED: '+type(error).__name__+'; transaction rolled back; no seed/reset executed.')
        return 1
    finally:
        if engine is not None:engine.dispose()

if __name__=='__main__':raise SystemExit(main())
