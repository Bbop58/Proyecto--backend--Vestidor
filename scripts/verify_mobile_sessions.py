"""SM4 owned local QA API. Never accepts hosted URLs or production database names."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))


def configure(url):
    from sqlalchemy.engine import make_url
    parsed=make_url(url)
    if (parsed.get_backend_name()!='postgresql' or parsed.host not in {'127.0.0.1','localhost','::1'}
            or parsed.database!='fashionstore_test_sm4_app' or parsed.port!=55443 or parsed.query):
        raise RuntimeError('Only the owned disposable LOCAL SM4 application database is allowed')
    private=Path(os.environ['LOCALAPPDATA'])/'FashionStore/private/session-sm4'
    private.mkdir(parents=True,exist_ok=True)
    keys_file=private/'local-keys.json'
    if not keys_file.exists():
        keys_file.write_text(json.dumps({key:secrets.token_urlsafe(48) for key in ['SECRET_KEY','WALLET_SIGNING_KEY']}))
    keys=json.loads(keys_file.read_text())
    os.environ.update(DATABASE_URL=url,RUN_STARTUP_MIGRATIONS='false',RUN_DEMO_SEED='false',
        GEMINI_API_KEY='',VOICE_GEMINI_API_KEY='',CHATBOT_GEMINI_API_KEY='',HF_TOKEN='',VTON_API_URL='',
        PAYPAL_CLIENT_ID='',PAYPAL_CLIENT_SECRET='',PAYPAL_WEBHOOK_ID='',BACKUP_S3_ENDPOINT='',BACKUP_S3_BUCKET='',
        PLATFORM_ADMIN_USER_IDS='[]',**keys)
    return private


def prepare(private):
    import uuid
    from sqlalchemy import select,inspect
    from sqlalchemy.orm import Session
    from app.database import engine
    from app.models import Tenant,User,Role,Category,Product,ProductVariant,Inventory,Branch
    from app.core.security import get_password_hash
    from app.services.wallet_service import WalletService
    from scripts.reset_demo_database import reset_demo_database
    if inspect(engine).get_table_names(): raise RuntimeError('QA preparation requires a fresh empty owned database')
    with contextlib.redirect_stdout(io.StringIO()): result=reset_demo_database(engine)
    assert not result['tables_changed_on_repeat'] and not any(result['checks'].values())
    accounts={}
    with Session(engine) as db:
        role=db.scalar(select(Role).where(Role.name=='cliente'))
        for slug in ['urbanstyle','modaexpress']:
            company=db.scalar(select(Tenant).where(Tenant.slug==slug))
            admin=db.scalar(select(User).where(User.tenant_id==company.id,User.email=='admin@'+slug+'.com'))
            password='Sm4'+secrets.token_hex(8)+'!'
            user=User(email='sm4-'+slug+'@example.com',full_name='QA SM4 '+slug,tenant_id=company.id,role_id=role.id,
                hashed_password=get_password_hash(password),is_active=True,is_verified=True)
            db.add(user);db.flush()
            category=db.scalar(select(Category).where(Category.tenant_id==company.id))
            product=Product(tenant_id=company.id,nombre='QA SM4 Prenda Sesion',precio_base=10,categoria_id=category.id,activo=True)
            db.add(product);db.flush()
            variant=ProductVariant(producto_id=product.id,sku='SM4-'+slug.upper(),talla='M',color='Azul',precio_extra=0,activo=True)
            db.add(variant);db.flush()
            branches=db.scalars(select(Branch).where(Branch.tenant_id==company.id,Branch.activa.is_(True))).all()
            for branch in branches: db.add(Inventory(tenant_id=company.id,sucursal_id=branch.id,variante_id=variant.id,stock_actual=40,stock_reservado=0,activo=True))
            db.commit()
            WalletService.topup_cash_admin(db,admin,user.id,100,client_operation_id=uuid.uuid4());db.commit()
            accounts[slug]={'tenant_id':str(company.id),'user_id':str(user.id),'email':user.email,'password':password,
                'variant_id':str(variant.id),'product_id':str(product.id),'branch_id':str(branches[0].id)}
    (private/'local-accounts.json').write_text(json.dumps(accounts,indent=2))
    report={'database':'fashionstore_test_sm4_app','local_only':True,'seed_checks':len(result['checks']),
        'seed_repeat_unchanged':True,'companies':2,'qa_clients':2,'provider_calls':0,'private_directory':str(private)}
    (ROOT/'docs/auditoria_software/SESION_MOVIL_SM4_LOCAL_PREPARADO.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url',required=True)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--serve',action='store_true')
    args=parser.parse_args()
    private=configure(args.database_url)
    if args.prepare: prepare(private)
    if args.serve:
        import uvicorn
        uvicorn.run('app.main:app',host='127.0.0.1',port=58012)


if __name__=='__main__':main()
