-- Explicit P2 maintenance; no provider calls or automatic startup execution.
CREATE TABLE IF NOT EXISTS company_paypal_credentials (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
 revision integer NOT NULL CHECK(revision>0), enabled boolean NOT NULL,
 client_id varchar(255), mode varchar(10), merchant_key varchar(64), secret_ciphertext text,
 verified_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_company_paypal_revision UNIQUE(tenant_id,revision),
 CONSTRAINT ck_company_paypal_shape CHECK(
  (enabled AND client_id IS NOT NULL AND mode IS NOT NULL AND mode IN ('sandbox','live') AND merchant_key IS NOT NULL AND secret_ciphertext IS NOT NULL)
  OR (NOT enabled AND client_id IS NULL AND mode IS NULL AND merchant_key IS NULL AND secret_ciphertext IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_company_paypal_credentials_tenant_id ON company_paypal_credentials(tenant_id);
CREATE OR REPLACE FUNCTION protect_company_paypal_revision() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'PayPal credential revisions are immutable'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS tr_company_paypal_immutable ON company_paypal_credentials;
CREATE TRIGGER tr_company_paypal_immutable BEFORE UPDATE OR DELETE ON company_paypal_credentials
 FOR EACH ROW EXECUTE FUNCTION protect_company_paypal_revision();
-- Permission grant (only after the full schema exists).
INSERT INTO permissions(id,code,module,method,path)
 SELECT '9dc1e2ce-2847-58a6-9b6f-8b2ee7fd852f'::uuid,'payments.configure','Pagos empresariales','PUT','/company/paypal'
 WHERE NOT EXISTS(SELECT 1 FROM permissions WHERE code='payments.configure');
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r CROSS JOIN permissions p
 WHERE r.tenant_id IS NULL AND r.name='admin' AND p.code='payments.configure'
 AND NOT EXISTS(SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);
