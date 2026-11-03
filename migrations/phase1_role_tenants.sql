-- Explicit upgrade only. Back up and inspect accounts before production rollout.
BEGIN;
ALTER TABLE roles ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
ALTER TABLE roles DROP CONSTRAINT IF EXISTS roles_name_key;
DROP INDEX IF EXISTS ix_roles_name;
CREATE INDEX ix_roles_name ON roles(name);
CREATE INDEX IF NOT EXISTS ix_roles_tenant_id ON roles(tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_roles_tenant_name ON roles(tenant_id,name);
CREATE UNIQUE INDEX IF NOT EXISTS uq_roles_global_name ON roles(name) WHERE tenant_id IS NULL;
-- Assign custom roles only where existing users establish one unambiguous owner.
UPDATE roles r SET tenant_id=x.tenant_id FROM (
 SELECT role_id,(array_agg(DISTINCT tenant_id))[1] AS tenant_id FROM users
 WHERE tenant_id IS NOT NULL GROUP BY role_id HAVING count(DISTINCT tenant_id)=1
) x WHERE r.id=x.role_id AND r.tenant_id IS NULL AND r.name NOT IN ('admin','encargado','cajero','cliente');
COMMIT;
-- Unassigned accounts and ambiguous legacy custom roles remain quarantined;
-- never attach them to the first company. Resolve their ownership explicitly.
