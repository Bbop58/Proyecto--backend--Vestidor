"""Destructive recovery rehearsal restricted to two fixed, empty local QA DBs."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SOURCE='fashionstore_test_phase8_source'
TARGET='fashionstore_test_phase8_restored'
URL='postgresql://audit@127.0.0.1:55499/'
ADMIN_ID=uuid.uuid5(uuid.NAMESPACE_URL,'fashionstore:demo:user:admin@urbanstyle.com')


def main():
    if sys.argv[1:]!=['--run-local-disposable']:
        raise RuntimeError('Explicit local disposable rehearsal required')
    os.environ.update(DATABASE_URL=URL+SOURCE,SECRET_KEY=secrets.token_urlsafe(48),
        WALLET_SIGNING_KEY=secrets.token_urlsafe(48),PLATFORM_ADMIN_USER_IDS=json.dumps([str(ADMIN_ID)]),
        PAYPAL_CLIENT_ID='',PAYPAL_CLIENT_SECRET='',GEMINI_API_KEY='',VOICE_GEMINI_API_KEY='',
        CHATBOT_GEMINI_API_KEY='',HF_TOKEN='',RUN_STARTUP_MIGRATIONS='false',RUN_DEMO_SEED='false')
    from sqlalchemy import create_engine,inspect,select
    from sqlalchemy.orm import Session
    from app.database import Base
    import app.models
    from app.models.user import User
    from app.models.product import Product
    from app.models.inventory import Inventory
    from app.models.user_wallet import UserWallet
    from app.services.backup_service import BackupService,json_serializer
    from app.services.backup_recovery import export_snapshot,digest
    from app.services.wallet_service import WalletService
    from app.core.tenant_scope import bind_tenant
    from app.seed_audit import reconcile
    from scripts.reset_demo_database import reset_demo_database
    source=create_engine(URL+SOURCE);target=create_engine(URL+TARGET)
    for engine in [source,target]:
        if inspect(engine).get_table_names():raise RuntimeError('Refusing to overwrite a nonempty rehearsal DB')
    results=[]
    reset=reset_demo_database(source)
    results.append({'name':'fresh seed repeated','pass':not any(reset['checks'].values()),'checks':len(reset['checks'])})
    with Session(source) as db:
        actor=db.query(User).filter_by(email='admin@urbanstyle.com').one()
        assert actor.id==ADMIN_ID and actor.is_platform_admin
        actor_id=actor.id;tenant=actor.tenant_id
        other=db.query(User).filter_by(email='admin@modaexpress.com').one().tenant_id
    with Session(source) as db:
        bind_tenant(db,other,None);other_before=digest(export_snapshot(db)['data'])
    with Session(source) as db:
        bind_tenant(db,tenant,actor_id);actor=db.get(User,actor_id)
        WalletService.topup_cash_admin(db,actor,actor_id,100)
        backup=json.loads(json.dumps(BackupService.create_backup(db,actor),default=json_serializer))
        product=db.query(Product).first();original=product.precio_base;pid=product.id
        product.precio_base=9999
        inventory=db.query(Inventory).first();inventory.stock_actual=1
        db.commit()
        result=BackupService.restore_backup(db,backup,actor)
        assert result['success'] and db.get(Product,pid).precio_base==original
        wallet=db.query(UserWallet).filter_by(user_id=actor_id).one()
        assert wallet.saldo_actual==100 and WalletService.verify_wallet_integrity(wallet)
        results.append({'name':'complete tenant JSON restore with signed wallet','pass':True,'tables':len(result['restored_entities'])})
    with Session(source) as db:
        bind_tenant(db,other,None);assert digest(export_snapshot(db)['data'])==other_before
    results.append({'name':'second company unchanged','pass':True})

    def snapshot(engine):
        with engine.connect() as connection:
            return {table.name:hashlib.sha256('\n'.join(sorted(
                json.dumps(dict(row),default=json_serializer,sort_keys=True)
                for row in connection.execute(select(table)).mappings())).encode()).hexdigest()
                for table in Base.metadata.sorted_tables}
    before=snapshot(source)
    pg=Path('C:/Program Files/PostgreSQL/16/bin')
    with tempfile.TemporaryDirectory(prefix='fashionstore_phase8_backup_') as directory:
        dump=Path(directory)/'demo.dump'
        subprocess.run([str(pg/'pg_dump.exe'),'-h','127.0.0.1','-p','55499','-U','audit','-d',SOURCE,
            '--format=custom','--no-owner','--no-acl','-f',str(dump)],check=True,capture_output=True)
        subprocess.run([str(pg/'pg_restore.exe'),'-h','127.0.0.1','-p','55499','-U','audit','-d',TARGET,
            '--clean','--if-exists','--single-transaction','--exit-on-error','--no-owner','--no-acl',str(dump)],check=True,capture_output=True)
        assert snapshot(target)==before
        results.append({'name':'pg_dump pg_restore all-table fingerprint equality','pass':True,'tables':len(before),
                        'dump_sha256':hashlib.sha256(dump.read_bytes()).hexdigest()})
    with target.connect() as connection:
        checks=reconcile(connection);assert not any(checks.values())
    with Session(target) as db:
        wallet=db.query(UserWallet).filter_by(user_id=actor_id).one()
        assert WalletService.verify_wallet_integrity(wallet)
    results.append({'name':'restored DB reconciliation and wallet signature','pass':True,'checks':checks})
    reset_demo_database(source)
    with Session(source) as db:
        actor=db.query(User).filter_by(email='admin@urbanstyle.com').one()
        assert actor.id==actor_id and actor.is_platform_admin
    results.append({'name':'platform operator identity survives another fresh deploy','pass':True})
    target.dispose();source.dispose()
    output=ROOT.parent/'docs/auditoria_software/FASE_8_RECUPERACION.json'
    output.write_text(json.dumps({'results':results},indent=2),encoding='utf-8')
    print(json.dumps(results))


if __name__=='__main__':main()
