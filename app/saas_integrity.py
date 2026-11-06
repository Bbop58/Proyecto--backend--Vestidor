"""Read-only structural and aggregate SaaS verification. No customer data returned."""
import uuid
from sqlalchemy import text

TRIGGERS={('subscription_payments','tr_subscription_payment'),('company_paypal_credentials','tr_company_paypal_immutable'),('subscription_plan_versions','tr_protect_plan_version'),('subscription_plans','tr_protect_legacy_plan_conditions'),
 ('subscription_terms','tr_subscription_term'),('subscription_change_requests','tr_subscription_request'),('subscriptions','tr_subscription_aggregate')}
TABLE_COLUMNS={'tenants':{'es_operadora','platform_revision'},'subscription_plans':{'catalog_managed'},
 'subscription_plan_versions':{'id','permite_analitica_avanzada','lock_version'},'subscription_terms':{'id','features','accepted_at'},
 'subscription_change_requests':{'id','idempotency_key','decision_result'},'platform_audit_logs':{'id','request_fingerprint'}}


def schema_problems(connection):
    columns={(r[0],r[1]) for r in connection.execute(text('SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=current_schema()'))}
    triggers={(r[0],r[1]) for r in connection.execute(text("SELECT c.relname,t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal AND t.tgenabled IN ('O','A')"))}
    return {'missing_columns':sum((name,col) not in columns for name,fields in TABLE_COLUMNS.items() for col in fields),
            'missing_guards':len(TRIGGERS-triggers)}


CHECKS={
 'operator_context':"""SELECT count(*) FROM users u JOIN tenants t ON t.id=u.tenant_id LEFT JOIN roles r ON r.id=u.role_id
 WHERE (t.es_operadora OR r.name='superadmin_plataforma') AND (NOT t.es_operadora OR r.name IS DISTINCT FROM 'superadmin_plataforma' OR r.tenant_id IS DISTINCT FROM t.id)""",
 'operator_commercial_permissions':"""SELECT count(*) FROM role_permissions p JOIN roles r ON r.id=p.role_id WHERE r.name='superadmin_plataforma'""",
 'commercial_contract_missing':"""SELECT count(*) FROM subscriptions s JOIN tenants t ON t.id=s.tenant_id WHERE NOT t.es_operadora AND NOT EXISTS(SELECT 1 FROM subscription_terms st WHERE st.subscription_id=s.id)""",
 'contract_tenant_reference':"""SELECT count(*) FROM subscription_terms t JOIN subscriptions s ON s.id=t.subscription_id WHERE t.tenant_id<>s.tenant_id""",
 'request_tenant_reference':"""SELECT count(*) FROM subscription_change_requests r JOIN subscriptions s ON s.id=r.subscription_id WHERE r.tenant_id<>s.tenant_id""",
 'contract_overlap':"""SELECT count(*) FROM subscription_terms a JOIN subscription_terms b ON a.subscription_id=b.subscription_id AND a.id<b.id AND a.fecha_inicio<b.fecha_fin AND b.fecha_inicio<a.fecha_fin""",
 'legacy_price_claim':"""SELECT count(*) FROM subscription_terms WHERE origen='REFERENCIA_LEGADA' AND (precio_mensual_bob IS NOT NULL OR accepted_at IS NOT NULL OR approved_at IS NOT NULL)""",
 'unknown_contract_features':"""SELECT count(*) FROM subscription_terms WHERE NOT (features ?& ARRAY['reportes_ia','analitica_avanzada','offline']) OR features-ARRAY['reportes_ia','analitica_avanzada','offline']<>'{}'::jsonb""",
}


def verify_counts(connection,operator_ids=()):
    structural=schema_problems(connection)
    if any(structural.values()):return structural
    result={name:connection.execute(text(sql)).scalar_one() for name,sql in CHECKS.items()}
    ids=[str(uuid.UUID(str(value))) for value in operator_ids]
    result['configured_operator_invalid']=connection.execute(text("""SELECT count(*) FROM unnest(CAST(:ids AS uuid[])) AS wanted(id)
 LEFT JOIN users u ON u.id=wanted.id LEFT JOIN tenants t ON t.id=u.tenant_id LEFT JOIN roles r ON r.id=u.role_id
 WHERE u.id IS NULL OR NOT u.is_active OR t.id IS NULL OR NOT t.activo OR NOT t.es_operadora OR r.name IS DISTINCT FROM 'superadmin_plataforma' OR r.tenant_id IS DISTINCT FROM t.id"""),{'ids':ids}).scalar_one()
    return result
