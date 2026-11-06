"""Owned local P2 acceptance: fixed empty QA DB, private seed, static built web."""
import json
import os
from pathlib import Path
import secrets
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
DB='postgresql://audit@127.0.0.1:55439/fashionstore_test_paypal_p2_browser_20261106'


def main():
    if sys.argv[1:]!=['--run-local-disposable']:
        raise SystemExit('Explicit local disposable fixture flag required')
    from dotenv import dotenv_values
    private=Path(os.environ['LOCALAPPDATA'])/'FashionStore/private/railway-payments-p2.env'
    values=dotenv_values(private)
    os.environ.update(DATABASE_URL=DB,SECRET_KEY=secrets.token_urlsafe(48),WALLET_SIGNING_KEY=secrets.token_urlsafe(48),
        PAYMENT_CREDENTIALS_KEY=values['PAYMENT_CREDENTIALS_KEY'],PLATFORM_ADMIN_USER_IDS='[]',
        RUN_STARTUP_MIGRATIONS='false',RUN_DEMO_SEED='false',PAYPAL_CLIENT_ID='platform-local-qa',PAYPAL_CLIENT_SECRET='',
        PAYPAL_MODE='sandbox',GEMINI_API_KEY='',VOICE_GEMINI_API_KEY='',CHATBOT_GEMINI_API_KEY='',HF_TOKEN='',
        BACKEND_CORS_ORIGINS='["http://127.0.0.1:54222"]')
    from sqlalchemy import inspect,select,func
    from app.database import engine
    from scripts.reset_demo_database import reset_demo_database
    from app.models.company_paypal_credential import CompanyPayPalCredential as Credential
    from app.models.tenant import Tenant
    if inspect(engine).get_table_names(): raise SystemExit('Refusing to overwrite existing P2 browser fixture')
    result=reset_demo_database(engine,tenant_paypal_config=json.loads(values['DEMO_TENANT_PAYPAL_CONFIG']))
    with engine.connect() as connection:
        count=connection.execute(select(func.count()).select_from(Credential)).scalar_one()
        legacy=connection.execute(select(func.count()).select_from(Tenant).where(Tenant.paypal_client_secret.is_not(None))).scalar_one()
    assert count==2 and legacy==0 and not any(result['checks'].values())
    report={'source':'real private sandbox configuration, local disposable DB only','profiles':count,
            'legacy_secrets_remaining':legacy,'seed_repeat_changes':result['tables_changed_on_repeat'],
            'reconciliation_checks':len(result['checks']),'provider_called_by_seed':False}
    (ROOT/'docs/auditoria_software/PAGOS_P2_SEED_PRIVADO.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    class SPA(SimpleHTTPRequestHandler):
        def do_GET(self):
            target=self.translate_path(self.path.split('?')[0])
            if not Path(target).is_file(): self.path='/index.html'
            super().do_GET()
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',54222),partial(SPA,directory=str(ROOT/'web/dist/web-app/browser')))
    Thread(target=server.serve_forever,daemon=True).start()
    from app.main import app
    import uvicorn
    print('Local P2 fixture ready; private values omitted; web 54222 / API 58022.',flush=True)
    try: uvicorn.run(app,host='127.0.0.1',port=58022)
    finally: server.shutdown();server.server_close();engine.dispose()


if __name__=='__main__':main()
