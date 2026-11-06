"""Explicit transactional P3 migration. Does not create payments for old terms."""
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine,text,inspect
from scripts.migrate_saas import validate_target

def migrate_connection(connection):
    schema=connection.execute(text('SELECT current_schema()')).scalar_one()
    if not all(inspect(connection).has_table(name,schema=schema) for name in
            ('subscription_terms','subscription_change_requests','subscriptions')):
        raise ValueError('Apply the SaaS contract schema before P3 billing')
    connection.execute(text("SET LOCAL lock_timeout='10s'"))
    connection.execute(text('SELECT pg_advisory_xact_lock(610134,23)'))
    connection.exec_driver_sql((Path(__file__).resolve().parents[1]/'migrations/payments_03_subscription_billing.sql').read_text(encoding='utf-8'))
    return {'schema_upgraded':True,'historical_payments_attributed':0,'provider_called':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--allow-nonlocal-database',action='store_true')
    args=parser.parse_args()
    if not args.apply:parser.error('Explicit --apply required')
    engine=None
    try:
        url=os.environ.get('DATABASE_URL','');validate_target(url,args.allow_nonlocal_database)
        engine=create_engine(url)
        with engine.begin() as connection:result=migrate_connection(connection)
        print(json.dumps(result));return 0
    except Exception as error:
        print('[subscription migration] FAILED '+type(error).__name__+'; rolled back; no values logged.');return 1
    finally:
        if engine is not None:engine.dispose()

if __name__=='__main__':raise SystemExit(main())
