"""Explicit atomic P2 upgrade. Legacy plaintext is encrypted, never logged."""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text
from scripts.migrate_saas import validate_target


def migrate_connection(connection):
    from app.services.company_paypal_service import CompanyPayPalService as Service
    from app.models.tenant import Tenant
    from sqlalchemy import select
    if not connection.in_transaction(): raise ValueError('Owning transaction required')
    connection.execute(text("SET LOCAL lock_timeout='10s'"))
    connection.execute(text('SELECT pg_advisory_xact_lock(610134,22)'))
    connection.exec_driver_sql((Path(__file__).resolve().parents[1] / 'migrations/payments_02_company_credentials.sql').read_text(encoding='utf-8'))
    migrated = 0
    for row in connection.execute(select(Tenant.__table__).where(
            (Tenant.paypal_client_id.is_not(None)) | (Tenant.paypal_client_secret.is_not(None)))).mappings().all():
        if Service.latest(connection, row['id']): raise ValueError('Legacy and protected configurations require explicit reconciliation')
        if row['es_operadora']: raise ValueError('Platform must not have commercial credentials')
        Service.append(connection, row['id'], client_id=row['paypal_client_id'], secret=row['paypal_client_secret'], mode=row['paypal_mode'])
        migrated += 1
    return {'legacy_companies_encrypted':migrated, 'provider_called':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--allow-nonlocal-database',action='store_true')
    args=parser.parse_args()
    if not args.apply: parser.error('Explicit --apply required')
    engine=None
    try:
        url=os.environ.get('DATABASE_URL','');validate_target(url,args.allow_nonlocal_database)
        engine=create_engine(url)
        with engine.begin() as connection: result=migrate_connection(connection)
        print(json.dumps(result));return 0
    except Exception as error:
        print('[payment migration] FAILED '+type(error).__name__+'; transaction rolled back; no values logged.');return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
