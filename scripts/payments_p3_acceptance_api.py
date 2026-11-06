"""Owned, empty local QA DB + real API/web, with an explicitly simulated provider."""
import copy,json,os,secrets,sys,uuid
from pathlib import Path
from threading import Thread
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
DB='postgresql://audit@127.0.0.1:55439/fashionstore_test_paypal_p3_browser_2_20261106'

def main():
    if sys.argv[1:]!=['--run-local-disposable']:raise SystemExit('Explicit local fixture flag required')
    operator_id=uuid.uuid5(uuid.NAMESPACE_URL,'fashionstore:p3:local:operator')
    os.environ.update(DATABASE_URL=DB,SECRET_KEY=secrets.token_urlsafe(48),WALLET_SIGNING_KEY=secrets.token_urlsafe(48),
        PLATFORM_ADMIN_USER_IDS=json.dumps([str(operator_id)]),PAYPAL_CLIENT_ID='synthetic-platform-p3-browser',
        PAYPAL_CLIENT_SECRET='synthetic-only-no-provider-access',PAYPAL_MODE='sandbox',PAYPAL_WEBHOOK_ID='',
        SUBSCRIPTION_WEB_URL='http://127.0.0.1:54223/empresa/suscripcion',RUN_STARTUP_MIGRATIONS='false',RUN_DEMO_SEED='false',
        GEMINI_API_KEY='',VOICE_GEMINI_API_KEY='',CHATBOT_GEMINI_API_KEY='',HF_TOKEN='')
    from sqlalchemy import inspect,select,func
    from sqlalchemy.orm import Session
    from app.database import engine
    from app.models import User,SubscriptionTerm,PaymentIntent,WalletTransaction
    from app.models.subscription_payment import SubscriptionPayment
    from scripts.reset_demo_database import reset_demo_database
    if inspect(engine).get_table_names():raise SystemExit('Refusing to overwrite the populated P3 browser fixture')
    seeded=reset_demo_database(engine,operator_id,'SaaSOnlyTest123!')
    from app.api.deps import get_authenticated_identity
    from app.services.plan_catalog_service import PlanCatalogService
    from app.schemas.plan_catalog import CatalogPlanCreate,PlanDraftCreate,PlanVersionDecision
    with Session(engine) as db:
        user=db.get(User,operator_id)
        actor=get_authenticated_identity(db,{'sub':str(user.id),'tenant_id':str(user.tenant_id)})
        plan=PlanCatalogService.create_plan(db,actor,CatalogPlanCreate(codigo='QA_P3',nombre='QA P3 Mensual',reason='Local acceptance fixture'))
        version=PlanCatalogService.create_draft(db,actor,uuid.UUID(str(plan['id'])),PlanDraftCreate(precio_mensual_bob='130.00',max_sucursales=10,
            features={'reportes_ia':True,'analitica_avanzada':True,'offline':True},reason='Local acceptance offer'))
        PlanCatalogService.publish_version(db,actor,uuid.UUID(str(version['id'])),PlanVersionDecision(expected_version=0,reason='Local acceptance publication'))
    from app.services.paypal_service import PayPalService
    from app.services.subscription_contract_service import SubscriptionContractService
    orders={};counts={'create':0,'capture':0,'refund':0}
    def check(kwargs):assert kwargs['client_id']=='synthetic-platform-p3-browser'
    def create(**kwargs):
        check(kwargs);counts['create']+=1;oid='QA-P3-'+kwargs['custom_id']
        orders[oid]={'id':oid,'status':'APPROVED','purchase_units':[{'custom_id':kwargs['custom_id'],
            'payments':{'captures':[{'id':'CAP-'+kwargs['custom_id'],'status':'COMPLETED',
            'create_time':SubscriptionContractService.now().isoformat(),
            'amount':{'currency_code':'USD','value':str(kwargs['amount_usd'])}}]}}]}
        return {'order_id':oid,'approve_url':'https://www.sandbox.paypal.com/checkoutnow?token='+oid}
    def details(oid,**kwargs):check(kwargs);return copy.deepcopy(orders[oid])
    def capture(oid,**kwargs):
        check(kwargs);counts['capture']+=1;orders[oid]['status']='COMPLETED';return {'raw_response':copy.deepcopy(orders[oid])}
    def forbidden(*args,**kwargs):raise RuntimeError('No external provider requests allowed in P3 local acceptance')
    PayPalService.create_order=create;PayPalService.get_order_details=details;PayPalService.capture_order=capture
    PayPalService._request=forbidden
    class SPA(SimpleHTTPRequestHandler):
        def do_GET(self):
            if not Path(self.translate_path(self.path.split('?')[0])).is_file():self.path='/index.html'
            super().do_GET()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',54223),partial(SPA,directory=str(ROOT/'web/dist/web-app/browser')))
    Thread(target=server.serve_forever,daemon=True).start()
    from app.main import app
    import uvicorn
    print('P3 local API 58023 / web 54223. PROVIDER SIMULATED; external requests forbidden.',flush=True)
    try:uvicorn.run(app,host='127.0.0.1',port=58023)
    finally:
        with engine.connect() as connection:
            report={'scope':'fixed local disposable P3 DB; simulated provider, no external charges','provider_calls':counts,
                'seed_repeat_changes':seeded['tables_changed_on_repeat'],'seed_checks':len(seeded['checks']),
                'payments':connection.execute(select(func.count()).select_from(SubscriptionPayment)).scalar_one(),
                'paid_terms':connection.execute(select(func.count()).select_from(SubscriptionTerm).where(SubscriptionTerm.origen=='PAYPAL')).scalar_one(),
                'commercial_intents':connection.execute(select(func.count()).select_from(PaymentIntent)).scalar_one(),
                'wallet_transactions':connection.execute(select(func.count()).select_from(WalletTransaction)).scalar_one()}
        (ROOT/'docs/auditoria_software/PAGOS_P3_BROWSER_DB.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        server.shutdown();server.server_close();engine.dispose()

if __name__=='__main__':main()
