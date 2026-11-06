from pathlib import Path
from app.core.plan_catalog_ddl import _execute_in_catalog_schema

MIGRATION=Path(__file__).resolve().parents[2]/'migrations/saas_03_subscription_terms.sql'


def install_contract_schema(target,connection,**kwargs):
    _execute_in_catalog_schema(connection,target,MIGRATION.read_text(encoding='utf-8'))
    from app.models.subscription_payment import install_billing
    install_billing(target,connection)


def ensure_legacy_terms(connection):
    from app.models.subscription_term import SubscriptionTerm
    sql=MIGRATION.read_text(encoding='utf-8').split('-- INITIAL_TERMS\n',1)[1]
    _execute_in_catalog_schema(connection,SubscriptionTerm.__table__,sql)
