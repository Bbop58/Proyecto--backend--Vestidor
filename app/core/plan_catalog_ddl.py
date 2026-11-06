"""Single SQL source for explicit upgrades and fresh-schema DDL hooks."""
from pathlib import Path
from sqlalchemy import text

MIGRATION=Path(__file__).resolve().parents[2]/'migrations/saas_02_contract_catalog.sql'


def _execute_in_catalog_schema(connection,table,sql):
    schema=connection.schema_for_object(table) or 'public'
    previous=connection.execute(text("SELECT current_setting('search_path')")).scalar_one()
    path=connection.dialect.identifier_preparer.quote_schema(schema)+', public'
    connection.execute(text("SELECT set_config('search_path',:path,true)"),{'path':path})
    connection.exec_driver_sql(sql)
    # On SQL failure PostgreSQL requires transaction rollback; don't mask the
    # original error by querying the aborted transaction. SET LOCAL rolls back.
    connection.execute(text("SELECT set_config('search_path',:path,true)"),{'path':previous})


def install_catalog_schema(target,connection,**kwargs):
    if connection.dialect.name=='postgresql':
        _execute_in_catalog_schema(connection,target,MIGRATION.read_text(encoding='utf-8'))


def ensure_legacy_versions(connection):
    from app.models.subscription_plan_version import SubscriptionPlanVersion
    sql=MIGRATION.read_text(encoding='utf-8').split('-- INITIAL_REFERENCE\n',1)[1]
    _execute_in_catalog_schema(connection,SubscriptionPlanVersion.__table__,sql)
