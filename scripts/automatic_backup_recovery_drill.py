"""Explicit, destructive rehearsal in two fixed local disposable PostgreSQL DBs.

Production worker subprocesses + production boto3 over local HTTPS + HTTP API
download + restore into a cloned QA database. No real storage/payment provider.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE = 'fashionstore_test_backups_b3_source_20261106'
TARGET = 'fashionstore_test_backups_b3_restored_20261106'
URL = 'postgresql://audit@127.0.0.1:55439/'
PG = Path(os.environ.get('QA_POSTGRES_BIN', 'C:/Program Files/PostgreSQL/16/bin'))


def canonical(data):
    # Audit has a legitimate restore/download entry, so compare it separately.
    return {key: sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
            for key, rows in data.items() if key != 'audit_logs'}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['--run-local-disposable']:
        raise ValueError('Explicit --run-local-disposable required')
    from scripts.support_backup_rehearsal import LocalS3
    results = []
    with tempfile.TemporaryDirectory(prefix='fashionstore-backup-b3-') as temporary, LocalS3(temporary) as s3:
        os.environ.update(DATABASE_URL=URL + SOURCE, SECRET_KEY=secrets.token_urlsafe(48),
            WALLET_SIGNING_KEY=secrets.token_urlsafe(48), RUN_STARTUP_MIGRATIONS='false', RUN_DEMO_SEED='false',
            PLATFORM_ADMIN_USER_IDS='[]', PAYPAL_CLIENT_ID='', PAYPAL_CLIENT_SECRET='',
            GEMINI_API_KEY='', VOICE_GEMINI_API_KEY='', CHATBOT_GEMINI_API_KEY='', HF_TOKEN='',
            DEMO_TENANT_PAYPAL_CONFIG='{}', BACKUP_S3_ENDPOINT=s3.endpoint, BACKUP_S3_REGION='auto',
            BACKUP_S3_ADDRESSING_STYLE='path',
            BACKUP_S3_BUCKET='qa-private-backups', BACKUP_S3_ACCESS_KEY_ID='QA_LOCAL_ACCESS',
            BACKUP_S3_SECRET_ACCESS_KEY=secrets.token_urlsafe(40), BACKUP_ENVIRONMENT='qa-recovery',
            AWS_CA_BUNDLE=str(s3.ca_file), AWS_REQUEST_CHECKSUM_CALCULATION='when_required',
            AWS_RESPONSE_CHECKSUM_VALIDATION='when_required')
        from sqlalchemy import create_engine, select, text
        from sqlalchemy.orm import Session
        from app.config import settings
        from app.models import BackupPolicy, BackupCopy, User, UserWallet, Product, Inventory
        from app.services.wallet_service import WalletService
        from app.services.backup_service import BackupService
        from app.services.backup_recovery import export_snapshot
        from app.services.automatic_backup_service import update_policy
        from app.schemas.backup import AutomaticBackupUpdate
        from app.core.tenant_scope import bind_tenant
        from scripts.reset_demo_database import reset_demo_database
        from scripts.migrate_saas import validate_target
        engines = [create_engine(URL + name) for name in (SOURCE, TARGET)]
        try:
            for engine, name in zip(engines, (SOURCE, TARGET)):
                validate_target(str(engine.url))
                assert engine.url.host == '127.0.0.1' and engine.url.database == name
                with engine.begin() as connection:
                    connection.execute(text('DROP SCHEMA public CASCADE'))
                    connection.execute(text('CREATE SCHEMA public'))
            source, target = engines
            with contextlib.redirect_stdout(io.StringIO()):
                seeded = reset_demo_database(source)
            assert len(seeded['checks']) == 27 and not any(seeded['checks'].values())
            results.append({'name': 'fresh local + seed repeated without differences', 'pass': True, 'checks': 27})
            with Session(source) as db:
                actor = db.scalar(select(User).where(User.email == 'admin@urbanstyle.com'))
                other = db.scalar(select(User).where(User.email == 'admin@modaexpress.com'))
                actor_id, tenant_id, other_id, other_tenant = actor.id, actor.tenant_id, other.id, other.tenant_id
                WalletService.topup_cash_admin(db, actor, actor.id, 25)
                # A local clock fixture only; no public time override or remote date edit.
                update_policy(db, actor, AutomaticBackupUpdate(enabled=True, interval_hours=1, revision=0),
                    datetime.now(timezone.utc) - timedelta(hours=1, seconds=1))
            dump = Path(temporary) / 'baseline.dump'
            for command in (
                [str(PG / 'pg_dump.exe'), '--format=custom', '--file=' + str(dump), URL + SOURCE],
                [str(PG / 'pg_restore.exe'), '--exit-on-error', '--single-transaction', '--dbname=' + URL + TARGET, str(dump)],
            ):
                completed = subprocess.run(command, capture_output=True, timeout=120)
                if completed.returncode:
                    raise RuntimeError('Local PostgreSQL baseline clone failed')
            results.append({'name': 'QA target provisioned with compatible central references', 'pass': True})

            def cycle():
                completed = subprocess.run([sys.executable, str(ROOT / 'scripts/run_automatic_backups.py')],
                    cwd=ROOT, env=dict(os.environ), capture_output=True, text=True, encoding='utf-8', timeout=250)
                if completed.returncode:
                    raise RuntimeError('Worker process failed; private output not included')
                return json.loads(completed.stdout.strip().splitlines()[-1])

            s3.fail_next_put = True
            first = cycle()
            assert first['failed'] == 1 and not s3.objects
            results.append({'name': 'real worker process survives transient HTTPS storage failure', 'pass': True})
            s3.hold_put = True
            with ThreadPoolExecutor(max_workers=2) as pool:
                tasks = [pool.submit(cycle), pool.submit(cycle)]
                try:
                    assert s3.started.wait(40), 'Local upload was never reached'
                    wait(tasks, timeout=10, return_when=FIRST_COMPLETED)
                finally:
                    s3.release.set()
                outcomes = [task.result(timeout=60) for task in tasks]
            s3.hold_put = False
            assert sum(result['success'] for result in outcomes) == 1 and len(s3.objects) == 1
            assert cycle()['success'] == 0
            results.append({'name': 'two actual worker processes; one copy; restart without duplicates', 'pass': True})

            from fastapi.testclient import TestClient
            from app.main import app
            with TestClient(app) as client:
                def headers(email, company):
                    response = client.post('/api/v1/auth/login', json={'email': email,
                        'password': 'Password123!', 'tenant_id': str(company)})
                    assert response.status_code == 200
                    return {'Authorization': 'Bearer ' + response.json()['access_token']}
                a_headers = headers('admin@urbanstyle.com', tenant_id)
                b_headers = headers('admin@modaexpress.com', other_tenant)
                listing = client.get('/api/v1/backups/automatic/copies', headers=a_headers)
                assert listing.status_code == 200 and len(listing.json()) == 1
                identifier = listing.json()[0]['id']
                path = f'/api/v1/backups/automatic/copies/{identifier}/download'
                assert client.get(path, headers=b_headers).status_code == 404
                response = client.get(path, headers=a_headers)
                assert response.status_code == 200
                payload = response.json()
                assert payload['tenant_id'] == str(tenant_id) and payload['version'] == '4.6.0'
                assert 'object_key' not in listing.text and 'secret_ciphertext' not in response.text
            results.append({'name': 'actual API authentication/download; foreign company denied', 'pass': True})

            with Session(target) as db:
                bind_tenant(db, other_tenant, other_id)
                other_before = canonical(export_snapshot(db)['data'])
            with Session(target) as db:
                bind_tenant(db, tenant_id, actor_id)
                actor = db.get(User, actor_id)
                product = db.scalar(select(Product).limit(1))
                inventory = db.scalar(select(Inventory).limit(1))
                product.precio_base += 1
                inventory.stock_actual += 2
                wallet = db.scalar(select(UserWallet).where(UserWallet.user_id == actor_id))
                wallet.saldo_actual += 10  # Simulated damaged data, not an authorized payment.
                db.commit()
                assert BackupService.restore_backup(db, payload, actor)['success']
                actual = json.loads(json.dumps(export_snapshot(db)['data'], default=__import__('app.services.backup_recovery', fromlist=['serialize']).serialize))
                assert canonical(actual) == canonical(payload['data'])
                wallet = db.scalar(select(UserWallet).where(UserWallet.user_id == actor_id))
                assert wallet.saldo_actual == 25 and WalletService.verify_wallet_integrity(wallet)
                assert db.scalar(select(BackupPolicy.enabled).where(BackupPolicy.tenant_id == tenant_id))
                assert not db.scalars(select(BackupCopy)).all()  # Operational catalog never imported from JSON.
            with Session(target) as db:
                bind_tenant(db, other_tenant, other_id)
                assert canonical(export_snapshot(db)['data']) == other_before
            results.append({'name': 'full tenant graph restored; signed wallet; company B unchanged; policy preserved',
                'pass': True, 'data_sections': len(payload['data'])})

            # Broken uploads and signatures must not partially change the repaired target.
            import copy
            from fastapi import HTTPException
            from app.services.backup_recovery import digest
            for kind in ('foreign_company', 'corrupt', 'old_format', 'wrong_wallet_key'):
                with Session(target) as db:
                    bind_tenant(db, tenant_id, actor_id); actor = db.get(User, actor_id)
                    baseline = canonical(json.loads(json.dumps(export_snapshot(db)['data'], default=__import__('app.services.backup_recovery', fromlist=['serialize']).serialize)))
                    db.rollback()
                    candidate = copy.deepcopy(payload)
                    original_key = settings.WALLET_SIGNING_KEY
                    if kind == 'foreign_company': candidate['tenant_id'] = str(other_tenant)
                    elif kind == 'corrupt': candidate['data']['productos'][0]['nombre'] = 'Corrupted fixture'
                    elif kind == 'old_format': candidate['version'] = '4.0.0'
                    else: settings.WALLET_SIGNING_KEY = secrets.token_urlsafe(48)
                    try:
                        try: BackupService.restore_backup(db, candidate, actor)
                        except HTTPException as error: assert error.status_code in (400, 403, 409)
                        else: raise AssertionError('Unsafe restore unexpectedly accepted')
                    finally:
                        settings.WALLET_SIGNING_KEY = original_key
                    after = json.loads(json.dumps(export_snapshot(db)['data'], default=__import__('app.services.backup_recovery', fromlist=['serialize']).serialize))
                    assert canonical(after) == baseline
                results.append({'name': 'restore rejected atomically: ' + kind, 'pass': True})
        finally:
            for engine in engines: engine.dispose()
            from app.database import engine as application_engine
            application_engine.dispose()
    return {'scope': 'local disposable PostgreSQL; local HTTPS S3 double; production worker and boto3',
            'external_provider_verified': False, 'results': results}


if __name__ == '__main__':
    report_path = Path(os.environ.get('QA_BACKUP_REPORT', ROOT.parent / 'docs/auditoria_software/BACKUPS_B3_RECOVERY.json'))
    try:
        report = main()
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
    except Exception as error:
        # Do not print tracebacks containing credentials, JWTs, provider bodies or data.
        report_path.write_text(json.dumps({'status': 'FAILED', 'error_type': type(error).__name__}), encoding='utf-8')
        print('Local recovery rehearsal failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
