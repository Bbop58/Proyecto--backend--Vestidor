"""S5 real HTTP QA on one disposable local database; refuses existing tables."""
import os,secrets,sys
from pathlib import Path
from datetime import timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
DB='postgresql://audit@127.0.0.1:55439/fashionstore_test_saas_s5_browser_20261106'
os.environ.update(DATABASE_URL=DB,SECRET_KEY=secrets.token_urlsafe(48),WALLET_SIGNING_KEY=secrets.token_urlsafe(48),
 RUN_STARTUP_MIGRATIONS='false',RUN_DEMO_SEED='false',GEMINI_API_KEY='',VOICE_GEMINI_API_KEY='',CHATBOT_GEMINI_API_KEY='',HF_TOKEN='')

def main():
 from sqlalchemy import inspect
 from app.database import Base,engine,SessionLocal
 import app.models
 from app.models import Tenant,Role,User,Branch,SubscriptionPlan,Subscription
 from app.seed_permissions import seed
 from app.core.security import get_password_hash
 from app.core.billing_calendar import business_today
 from app.core.plan_catalog_ddl import ensure_legacy_versions
 from app.core.subscription_contract_ddl import ensure_legacy_terms
 from app.config import settings
 if inspect(engine).get_table_names():raise SystemExit('Refusing to replace existing S5 browser data; use a newly empty local fixture database.')
 Base.metadata.create_all(engine)
 with SessionLocal() as db:
  seed(db,include_seasons=False)
  companies={}
  for key,name,technical in [('operator','Operadora QA',True),('a','Fashion QA Norte',False),('b','Fashion QA Sur',False)]:
   t=Tenant(nombre=name,razon_social=name,nit_rut='QA-'+key,slug='qa-'+key,activo=True,es_operadora=technical)
   db.add(t);db.flush();companies[key]=t
  admin=db.query(Role).filter_by(name='admin').one();customer=db.query(Role).filter_by(name='cliente').one()
  reserved=Role(name='superadmin_plataforma',tenant_id=companies['operator'].id);reserved.permissions=list(admin.permissions);db.add(reserved);db.flush()
  hashed=get_password_hash('S5-Local-Only123!')
  for key,company,role in [('operator','operator',reserved),('admin','a',admin),('other','b',admin),('customer','a',customer)]:
   user=User(email=key+'@s5-test.example.com',full_name='QA '+key,tenant_id=companies[company].id,role_id=role.id,
    hashed_password=hashed,is_active=True,is_verified=True)
   db.add(user);db.flush()
   if key=='operator':settings.PLATFORM_ADMIN_USER_IDS=[str(user.id)]
  plan=SubscriptionPlan(codigo='QA_BASE',nombre='Referencia QA',max_sucursales=4,precio_mensual_bob=100,permite_reportes_ia=True,permite_offline=True)
  db.add(plan);db.flush()
  for key in ['a','b']:
   db.add(Subscription(tenant_id=companies[key].id,plan_id=plan.id,estado='ACTIVA',
    fecha_inicio=business_today()-timedelta(days=3),fecha_vencimiento=business_today()+timedelta(days=15)))
  db.add(Branch(tenant_id=companies['a'].id,nombre='Sucursal QA',ciudad='Tarija',direccion='Dirección sintética',activa=True))
  db.flush();ensure_legacy_versions(db.connection());ensure_legacy_terms(db.connection());db.commit()
 from app.main import app
 import uvicorn
 print('S5 local synthetic fixture ready; platform/companies/contracts; providers unused.',flush=True)
 uvicorn.run(app,host='127.0.0.1',port=58015)

if __name__=='__main__':main()
