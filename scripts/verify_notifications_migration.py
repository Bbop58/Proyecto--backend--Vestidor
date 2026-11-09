"""Copy a disposable LOCAL QA database; prove additive push migration twice.

Never accepts a remote source. Creates a new derived database and preserves it
for inspection; no existing database is dropped or overwritten. No provider use.
"""
import hashlib,json,os,subprocess,sys,tempfile,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url

def main():
    source=make_url(os.environ['QA_SOURCE_DATABASE_URL'])
    if (source.host not in {'127.0.0.1','localhost'} or source.get_backend_name()!='postgresql'
        or source.query or not (source.database or '').startswith('fashionstore_test_')):
        raise RuntimeError('Only disposable localhost fashionstore_test_ sources are allowed')
    target='fashionstore_test_push_migration_'+uuid.uuid4().hex[:10]
    pg=Path(os.environ.get('QA_PG_BIN',r'C:\Program Files\PostgreSQL\16\bin'))
    env={**os.environ,'PGPASSWORD':source.password or ''}
    common=['-h',source.host,'-p',str(source.port or 5432),'-U',source.username or 'postgres']
    def run(tool,args):subprocess.run([str(pg/(tool+'.exe')),*common,*args],env=env,check=True,capture_output=True)
    with tempfile.TemporaryDirectory(prefix='fashionstore-push-migration-') as folder:
        dump=str(Path(folder)/'local-qa.dump')
        run('pg_dump',['-Fc','-f',dump,source.database])
        run('createdb',['-T','template0','-E','UTF8','--locale=C',target])
        run('pg_restore',['--no-owner','--no-acl','--exit-on-error','-d',target,dump])
    engine=create_engine(source.set(database=target))
    def snapshot(connection):
        tables=connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars()
        result={}
        for name in tables:
            if name in {'notification_devices','notification_push_deliveries'}:continue
            quoted=engine.dialect.identifier_preparer.quote(name)
            rows=connection.execute(text(f'SELECT row_to_json(t)::text FROM {quoted} t')).scalars()
            hashes=sorted(hashlib.sha256(row.encode()).hexdigest() for row in rows)
            result[name]={'count':len(hashes),'sha256':hashlib.sha256(''.join(hashes).encode()).hexdigest()}
        return result
    # Simulate an earlier populated push schema without either protection column.
    with engine.begin() as c:
        before=snapshot(c)
        c.execute(text('TRUNCATE notification_push_deliveries, notification_devices'))
        has_expiry=c.execute(text("SELECT 1 FROM information_schema.columns WHERE "
            "table_schema='public' AND table_name='notification_devices' AND column_name='session_expires_at'")).first()
        if has_expiry:c.execute(text('ALTER TABLE notification_devices DROP COLUMN session_expires_at'))
        c.execute(text('ALTER TABLE notification_devices DROP COLUMN credential_hash'))
        user=c.execute(text('SELECT id,tenant_id FROM users ORDER BY id LIMIT 1')).one()
        installation=uuid.uuid4()
        c.execute(text("""INSERT INTO notification_devices
            (id,binding_id,secret_hash,token_hash,token,platform,user_id,tenant_id,active,updated_at)
            VALUES (:id,:binding,:secret,:recipient,'qa-migration-recipient','web',:user,:tenant,true,now())"""),
            dict(id=installation,binding=uuid.uuid4(),secret='a'*64,recipient='b'*64,user=user.id,tenant=user.tenant_id))
    from app.core.notification_push_ddl import ensure_notification_push
    for _ in range(2):
        with engine.begin() as c:ensure_notification_push(c)
    from sqlalchemy.orm import Session
    from app.models.notification_push import NotificationDevice
    from app.models import Notification
    from app.services.notification_push_service import eligible
    with Session(engine) as db:
        device=db.get(NotificationDevice,installation)
        assert device.credential_hash=='revoked-before-migration' and device.session_expires_at is None
        assert not eligible(db,device,Notification(user_id=device.user_id,title='QA',message='QA'))
    with engine.connect() as c:
        after=snapshot(c);assert before==after
        assert c.execute(text('SELECT count(*) FROM notification_devices')).scalar_one()==1
    engine.dispose()
    result={'database':target,'migration_runs':2,'tables_unchanged':len(before),
        'commercial_rows_unchanged':True,'legacy_device_preserved':True,'legacy_device_eligible':False}
    output=Path(os.environ.get('TEMP',tempfile.gettempdir()))/'notifications-c1-migration.json'
    output.write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(result))

if __name__=='__main__':main()
