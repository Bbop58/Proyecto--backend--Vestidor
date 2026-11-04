"""Create only an empty dedicated local browser-test database. No production or reseeding."""
import os,sys
from pathlib import Path
from datetime import date,timedelta
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
url=make_url(os.environ.get('DATABASE_URL',''))
if url.host not in ('127.0.0.1','localhost') or (url.database,url.port) not in {
    ('fashionstore_test_phase5_browser',55439), ('fashionstore_test_phase7_browser',55499)
} or url.query:
    raise RuntimeError('Dedicated local phase5 browser database required')
from app.database import engine,Base,SessionLocal
import app.models
from app.seed_permissions import seed
from app.models.tenant import Tenant
from app.models.subscription import Subscription
from app.models.subscription_plan import SubscriptionPlan
from app.models.user import User
from app.models.role import Role
from app.models.category import Category
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.branch import Branch
from app.models.inventory import Inventory
from app.core.security import get_password_hash
if inspect(engine).get_table_names():raise RuntimeError('Database must be empty; never overwrite existing test data')
Base.metadata.create_all(engine)
with SessionLocal() as db:
 seed(db)
 tenant=Tenant(nombre='FashionStore QA',razon_social='FashionStore QA',nit_rut='PHASE5',slug='fashionstore-qa',activo=True)
 plan=SubscriptionPlan(codigo='QA',nombre='Plan QA',max_sucursales=20,precio_mensual_bob=100,permite_offline=True,permite_reportes_ia=True,activo=True)
 db.add_all([tenant,plan]);db.flush()
 db.add(Subscription(tenant_id=tenant.id,plan_id=plan.id,estado='ACTIVA',fecha_vencimiento=date.today()+timedelta(days=365)))
 for kind in ('admin','cliente'):
  db.add(User(email=f'phase5-{kind}@example.com',full_name=f'QA {kind}',tenant_id=tenant.id,
   role_id=db.query(Role).filter_by(name=kind).one().id,hashed_password=get_password_hash('Phase5-Local-Only!'),is_active=True))
 category=Category(nombre='Camisas QA',tenant_id=tenant.id,activa=True)
 branch=Branch(nombre='Sucursal QA',tenant_id=tenant.id,direccion='Calle QA',ciudad='Santa Cruz',activa=True)
 db.add_all([category,branch]);db.flush()
 product=Product(nombre='Camisa QA',tenant_id=tenant.id,categoria_id=category.id,precio_base=100,precio_oferta=70,en_oferta=True,activo=True)
 db.add(product);db.flush()
 variant=ProductVariant(producto_id=product.id,talla='M',color='Azul',sku='QA-M-AZUL',precio_extra=5,activo=True)
 db.add(variant);db.flush()
 db.add(Inventory(tenant_id=tenant.id,sucursal_id=branch.id,variante_id=variant.id,stock_actual=100,stock_reservado=0,activo=True))
 db.commit()
print('Dedicated browser fixture prepared; no commercial database accessed.')
