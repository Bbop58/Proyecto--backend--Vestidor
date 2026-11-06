"""Inspect only the fixed, disposable P2 fixture after browser acceptance."""
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))

def main():
    if sys.argv[1:]!=['--local-fixture-only']:
        raise SystemExit('Explicit local fixture flag required')
    from dotenv import dotenv_values
    values=dotenv_values(Path(os.environ['LOCALAPPDATA'])/'FashionStore/private/railway-payments-p2.env')
    os.environ['DATABASE_URL']='postgresql://audit@127.0.0.1:55439/fashionstore_test_paypal_p2_browser_20261106'
    os.environ['PAYMENT_CREDENTIALS_KEY']=values['PAYMENT_CREDENTIALS_KEY']
    import secrets
    os.environ['SECRET_KEY']=secrets.token_urlsafe(48)
    from app.database import engine
    from app.services.company_paypal_seed import seed_company_paypal
    from app.models.company_paypal_credential import CompanyPayPalCredential as Credential
    from sqlalchemy import select,text
    with engine.begin() as connection:
        before=[dict(r) for r in connection.execute(select(Credential.__table__)).mappings()]
        seed_company_paypal(connection,json.loads(values['DEMO_TENANT_PAYPAL_CONFIG']))
        after=[dict(r) for r in connection.execute(select(Credential.__table__)).mappings()]
        assert before==after
        current=connection.execute(text('SELECT DISTINCT ON (tenant_id) enabled FROM company_paypal_credentials ORDER BY tenant_id,revision DESC')).scalars().all()
        assert len(current)==2 and current==[False,False]
        counts={name:connection.execute(text(f'SELECT count(*) FROM {name}')).scalar_one()
                for name in ('payment_intents','wallet_transactions')}
        assert not any(counts.values())
    report={'scope':'local P2 fixture only','seed_preserves_browser_disable':True,
            'credential_revisions':len(after),'companies_disabled':2,'counts':counts}
    (ROOT/'docs/auditoria_software/PAGOS_P2_POST_BROWSER.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    engine.dispose()
    print('P2 local fixture: disabled configurations preserved; no payment effects.')

if __name__=='__main__':main()
