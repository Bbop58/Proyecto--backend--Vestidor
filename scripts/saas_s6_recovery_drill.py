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
SOURCE='fashionstore_test_saas_s6_source_20261106'
TARGET='fashionstore_test_saas_s6_restored_20261106'
URL='postgresql://audit@127.0.0.1:55439/'
OPERATOR_ID=uuid.uuid5(uuid.NAMESPACE_URL,'fashionstore:qa:operator:s6:recovery')


def main():
    if sys.argv[1:]!=['--run-local-disposable']:
        raise RuntimeError('Explicit local disposable rehearsal required')
    os.environ.update(DATABASE_URL=URL+SOURCE,SECRET_KEY=secrets.token_urlsafe(48),
        WALLET_SIGNING_KEY=secrets.token_urlsafe(48),PLATFORM_ADMIN_USER_IDS=json.dumps([str(OPERATOR_ID)]),
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
    operator_password=secrets.token_urlsafe(32)
    reset=reset_demo_database(source,OPERATOR_ID,operator_password)
    results.append({'name':'fresh seed repeated','pass':not any(reset['checks'].values()),'checks':len(reset['checks'])})
    with Session(source) as db:
        actor=db.query(User).filter_by(email='admin@urbanstyle.com').one()
        assert not actor.is_platform_admin and not actor.tenant.es_operadora
        actor_id=actor.id;tenant=actor.tenant_id
        other=db.query(User).filter_by(email='admin@modaexpress.com').one().tenant_id
    # Make both platform and company histories nonempty through authorized services.
    from app.api.deps import get_authenticated_identity
    from app.schemas.plan_catalog import CatalogPlanCreate,PlanDraftCreate,PlanVersionDecision
    from app.schemas.subscription_contract import ContractChangeRequest,ContractDecision
    from app.services.plan_catalog_service import PlanCatalogService as Catalog
    from app.services.subscription_contract_service import SubscriptionContractService as Contracts
    from app.saas_integrity import verify_counts
    with Session(source) as db:
        operator=db.get(User,OPERATOR_ID)
        operator=get_authenticated_identity(db,{'sub':str(operator.id),'tenant_id':str(operator.tenant_id)})
        assert operator.is_platform_admin and not operator.role.permissions
        try:BackupService.create_backup(db,operator)
        except Exception as error:
            assert getattr(error,'status_code',None)==403
        else:raise AssertionError('Platform must not export company data')
        plan=Catalog.create_plan(db,operator,CatalogPlanCreate(codigo='S6_DRILL',nombre='S6 local only',reason='Local recovery rehearsal'))
        version=Catalog.create_draft(db,operator,plan['id'],PlanDraftCreate(precio_mensual_bob=333,max_sucursales=5,
            features={'reportes_ia':True,'analitica_avanzada':True,'offline':True},reason='Local recovery rehearsal'))
        Catalog.publish_version(db,operator,uuid.UUID(version['id']),PlanVersionDecision(expected_version=version['lock_version'],reason='Local recovery rehearsal'))
    with Session(source) as db:
        actor=db.get(User,actor_id)
        quoted=Contracts.quote(db,actor,uuid.UUID(version['id']),'RENOVACION')
        request=Contracts.request_change(db,actor,ContractChangeRequest(quote_token=quoted['quote_token'],acceptance=True,idempotency_key=uuid.uuid4()))
    with Session(source) as db:
        operator=db.get(User,OPERATOR_ID)
        operator=get_authenticated_identity(db,{'sub':str(operator.id),'tenant_id':str(operator.tenant_id)})
        approved=Contracts.decide_request(db,operator,uuid.UUID(request['id']),ContractDecision(decision='APROBADA',expected_version=0,idempotency_key=uuid.uuid4(),reason='Local recovery rehearsal'))
        assert approved['payment_status']=='NO_VERIFICADO'
    results.append({'name':'dedicated platform; manual accepted future contract; no money capture','pass':True})
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
    with tempfile.TemporaryDirectory(prefix='fashionstore_saas_s6_backup_') as directory:
        dump=Path(directory)/'demo.dump'
        subprocess.run([str(pg/'pg_dump.exe'),'-h','127.0.0.1','-p','55439','-U','audit','-d',SOURCE,
            '--format=custom','--no-owner','--no-acl','-f',str(dump)],check=True,capture_output=True)
        subprocess.run([str(pg/'pg_restore.exe'),'-h','127.0.0.1','-p','55439','-U','audit','-d',TARGET,
            '--clean','--if-exists','--single-transaction','--exit-on-error','--no-owner','--no-acl',str(dump)],check=True,capture_output=True)
        assert snapshot(target)==before
        results.append({'name':'pg_dump pg_restore all-table fingerprint equality','pass':True,'tables':len(before),
                        'dump_sha256':hashlib.sha256(dump.read_bytes()).hexdigest()})
    with target.connect() as connection:
        checks=reconcile(connection);assert not any(checks.values())
        verification=verify_counts(connection,[OPERATOR_ID]);assert not any(verification.values())
    with Session(target) as db:
        wallet=db.query(UserWallet).filter_by(user_id=actor_id).one()
        assert WalletService.verify_wallet_integrity(wallet)
    results.append({'name':'restored DB reconciliation and wallet signature','pass':True,'checks':checks})
    reset_demo_database(source,OPERATOR_ID,operator_password)
    with Session(source) as db:
        actor=db.query(User).filter_by(email='admin@urbanstyle.com').one()
        assert actor.id==actor_id and not actor.is_platform_admin
        operator=db.get(User,OPERATOR_ID);assert operator.is_platform_admin and operator.tenant_id!=actor.tenant_id
    results.append({'name':'platform operator identity survives another fresh deploy','pass':True})
    target.dispose();source.dispose()
    output=ROOT.parent/'docs/auditoria_software/SAAS_S6_RECUPERACION.json'
    output.write_text(json.dumps({'results':results},indent=2),encoding='utf-8')
    print(json.dumps(results))


if __name__=='__main__':main()
