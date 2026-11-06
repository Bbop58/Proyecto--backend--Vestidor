-- Run explicitly in a reviewed maintenance transaction. Never at app startup.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS es_operadora boolean NOT NULL DEFAULT false;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS platform_revision integer NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_single_operator ON tenants (es_operadora) WHERE es_operadora = true;
CREATE TABLE IF NOT EXISTS platform_audit_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id uuid NOT NULL,
    action varchar(80) NOT NULL,
    target_type varchar(30) NOT NULL,
    target_id uuid NOT NULL,
    reason varchar(500) NOT NULL,
    before_data jsonb NOT NULL DEFAULT '{}',
    after_data jsonb NOT NULL DEFAULT '{}',
    operation_id uuid,
    request_fingerprint varchar(64),
    result_payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_platform_actor_operation UNIQUE (actor_id, operation_id)
);
CREATE INDEX IF NOT EXISTS ix_platform_audit_logs_actor_id ON platform_audit_logs(actor_id);
INSERT INTO permissions(id,code,module,method,path,description,created_at)
VALUES
    (gen_random_uuid(),'backups.download','Respaldos','GET','/api/v1/backups/download','Descargar respaldo de la empresa propia',now()),
    (gen_random_uuid(),'backups.restore','Respaldos','POST','/api/v1/backups/restore','Restaurar respaldo de la empresa propia',now())
ON CONFLICT (code) DO NOTHING;
-- Extend the protected shared base admin, never overwrite personalized roles.
INSERT INTO role_permissions(role_id,permission_id)
SELECT r.id,p.id FROM roles r CROSS JOIN permissions p
WHERE r.name='admin' AND r.tenant_id IS NULL
AND p.code IN ('backups.download','backups.restore')
ON CONFLICT DO NOTHING;
