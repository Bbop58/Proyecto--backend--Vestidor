"""Read-only SaaS maintenance verification; aggregate counters, never identities/secrets."""
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine
from scripts.migrate_saas import validate_target
from app.saas_integrity import verify_counts

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--allow-nonlocal-database',action='store_true')
    args=parser.parse_args(argv);engine=None
    try:
        url=os.environ.get('DATABASE_URL','');validate_target(url,args.allow_nonlocal_database)
        ids=json.loads(os.environ.get('PLATFORM_ADMIN_USER_IDS','[]'))
        if not isinstance(ids,list):raise ValueError('Server grants must be a JSON list')
        engine=create_engine(url,connect_args={'connect_timeout':15})
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql('SET TRANSACTION READ ONLY')
                checks=verify_counts(connection,ids)
        print(json.dumps({'read_only':True,'operator_grants_configured':bool(ids),'checks':checks,'ok':not any(checks.values())}))
        return 1 if any(checks.values()) else 0
    except Exception as error:print('[saas verification] FAILED: '+type(error).__name__+'; no data was changed.');return 2
    finally:
        if engine is not None:engine.dispose()

if __name__=='__main__':raise SystemExit(main())
