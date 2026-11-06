"""Serve the already accepted local P3 fixture; no reset, seed or provider network."""
import json,os,secrets,sys,uuid
from pathlib import Path
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from functools import partial
from threading import Thread
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))

def main():
    if sys.argv[1:]!=['--serve-existing-local-qa']:raise SystemExit('Explicit existing QA fixture flag required')
    operator_id=uuid.uuid5(uuid.NAMESPACE_URL,'fashionstore:p3:local:operator')
    os.environ.update(DATABASE_URL='postgresql://audit@127.0.0.1:55439/fashionstore_test_paypal_p3_browser_2_20261106',
        SECRET_KEY=secrets.token_urlsafe(48),WALLET_SIGNING_KEY=secrets.token_urlsafe(48),PLATFORM_ADMIN_USER_IDS=json.dumps([str(operator_id)]),
        PAYPAL_CLIENT_ID='synthetic-platform-p3-browser',PAYPAL_CLIENT_SECRET='synthetic-only-no-provider-access',PAYPAL_MODE='sandbox',
        PAYPAL_WEBHOOK_ID='',RUN_STARTUP_MIGRATIONS='false',RUN_DEMO_SEED='false',GEMINI_API_KEY='',VOICE_GEMINI_API_KEY='',CHATBOT_GEMINI_API_KEY='',HF_TOKEN='')
    from app.database import engine
    from app.models import User,SubscriptionPlan,SubscriptionTerm
    from app.models.subscription_payment import SubscriptionPayment
    from sqlalchemy import select,func
    with engine.connect() as connection:
        assert connection.execute(select(User.id).where(User.id==operator_id)).first()
        assert connection.execute(select(SubscriptionPlan.id).where(SubscriptionPlan.codigo=='QA_P3')).first()
        assert connection.execute(select(func.count()).select_from(SubscriptionPayment).where(SubscriptionPayment.state=='APPLIED')).scalar_one()==1
    from app.services.paypal_service import PayPalService
    def forbidden(*a,**kw):raise RuntimeError('Provider network prohibited in read-only P3 acceptance')
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
    print('Serving existing local P3 fixture; no seed/reset/provider network.',flush=True)
    try:uvicorn.run(app,host='127.0.0.1',port=58023)
    finally:
        with engine.connect() as connection:
            payments=connection.execute(select(func.count()).select_from(SubscriptionPayment)).scalar_one()
            terms=connection.execute(select(func.count()).select_from(SubscriptionTerm).where(SubscriptionTerm.origen=='PAYPAL')).scalar_one()
        (ROOT/'docs/auditoria_software/PAGOS_P3_BROWSER_PERFIL_DB.json').write_text(json.dumps(
            {'scope':'existing local fixture, no reset/seed or provider network','payments':payments,'paid_terms':terms},indent=2),encoding='utf-8')
        server.shutdown();server.server_close();engine.dispose()

if __name__=='__main__':main()
