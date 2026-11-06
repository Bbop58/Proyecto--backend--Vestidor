-- Explicit reviewed transaction on existing DBs, never automatic startup.
CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_tenant ON subscriptions(tenant_id);
CREATE TABLE IF NOT EXISTS subscription_change_requests (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
 subscription_id uuid NOT NULL REFERENCES subscriptions(id) ON DELETE RESTRICT,
 version_id uuid NOT NULL REFERENCES subscription_plan_versions(id) ON DELETE RESTRICT,
 kind varchar(12) NOT NULL,quote_payload jsonb NOT NULL,accepted_by uuid NOT NULL,
 accepted_at timestamptz NOT NULL,quote_expires_at timestamptz NOT NULL,
 estado varchar(12) NOT NULL DEFAULT 'PENDIENTE',lock_version integer NOT NULL DEFAULT 0,
 idempotency_key uuid NOT NULL,fingerprint varchar(64) NOT NULL,
 decision_key uuid,decision_fingerprint varchar(64),decided_by uuid,decided_at timestamptz,
 reason varchar(500),decision_result jsonb,
 CONSTRAINT uq_contract_request_key UNIQUE(tenant_id,idempotency_key),
 CONSTRAINT uq_contract_decision_key UNIQUE(decided_by,decision_key),
 CONSTRAINT ck_contract_request_state CHECK(estado IN ('PENDIENTE','APROBADA','RECHAZADA','CANCELADA') AND lock_version>=0),
 CONSTRAINT ck_contract_request_kind CHECK(kind IN ('ALTA','RENOVACION','CAMBIO'))
);
CREATE TABLE IF NOT EXISTS subscription_terms (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
 subscription_id uuid NOT NULL REFERENCES subscriptions(id) ON DELETE RESTRICT,
 version_id uuid REFERENCES subscription_plan_versions(id) ON DELETE RESTRICT,
 request_id uuid UNIQUE REFERENCES subscription_change_requests(id) ON DELETE RESTRICT,
 fecha_inicio date NOT NULL,fecha_fin date NOT NULL,anchor_day integer NOT NULL,
 precio_mensual_bob numeric(10,2),moneda varchar(3) NOT NULL DEFAULT 'BOB',periodicidad varchar(10) NOT NULL,
 max_sucursales integer NOT NULL,features jsonb NOT NULL,origen varchar(25) NOT NULL,
 estado varchar(12) NOT NULL DEFAULT 'CONFIRMADO',accepted_by uuid,accepted_at timestamptz,approved_by uuid,approved_at timestamptz,
 CONSTRAINT uq_term_start UNIQUE(subscription_id,fecha_inicio),
 CONSTRAINT ck_term_dates CHECK(fecha_fin>fecha_inicio AND anchor_day BETWEEN 1 AND 31 AND max_sucursales>=1),
 CONSTRAINT ck_term_billing CHECK(moneda='BOB' AND estado='CONFIRMADO' AND periodicidad IN ('MENSUAL','LEGADA')),
 CONSTRAINT ck_term_price CHECK(precio_mensual_bob IS NULL OR (precio_mensual_bob>=0 AND precio_mensual_bob!='NaN'::numeric)),
 CONSTRAINT ck_term_origin CHECK((origen='REFERENCIA_LEGADA' AND precio_mensual_bob IS NULL AND accepted_by IS NULL AND periodicidad='LEGADA') OR (origen='MANUAL' AND precio_mensual_bob IS NOT NULL AND request_id IS NOT NULL AND accepted_by IS NOT NULL AND accepted_at IS NOT NULL AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND periodicidad='MENSUAL'))
);
CREATE OR REPLACE FUNCTION guard_subscription_term() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner uuid; overlap_found boolean; accepted jsonb;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Contract terms are immutable' USING ERRCODE='23514'; END IF;
 EXECUTE 'SELECT tenant_id FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscriptions WHERE id=$1' INTO owner USING NEW.subscription_id;
 IF owner IS DISTINCT FROM NEW.tenant_id THEN RAISE EXCEPTION 'Contract owner mismatch' USING ERRCODE='23514'; END IF;
 EXECUTE 'SELECT id FROM '||quote_ident(TG_TABLE_SCHEMA)||'.tenants WHERE id=$1 FOR NO KEY UPDATE' USING owner;
 EXECUTE 'SELECT id FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscriptions WHERE id=$1 FOR NO KEY UPDATE' USING NEW.subscription_id;
 EXECUTE 'SELECT EXISTS(SELECT 1 FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscription_terms WHERE subscription_id=$1 AND fecha_inicio<$3 AND fecha_fin>$2)' INTO overlap_found USING NEW.subscription_id,NEW.fecha_inicio,NEW.fecha_fin;
 IF overlap_found THEN RAISE EXCEPTION 'Contract periods overlap' USING ERRCODE='23514'; END IF;
 IF NEW.origen='MANUAL' THEN
   EXECUTE 'SELECT to_jsonb(r) FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscription_change_requests r WHERE id=$1' INTO accepted USING NEW.request_id;
   IF (accepted->>'subscription_id')::uuid IS DISTINCT FROM NEW.subscription_id OR
      (accepted->>'version_id')::uuid IS DISTINCT FROM NEW.version_id OR
      (accepted->>'accepted_by')::uuid IS DISTINCT FROM NEW.accepted_by OR
      (accepted->>'accepted_at')::timestamptz IS DISTINCT FROM NEW.accepted_at OR
      accepted->>'estado' IS DISTINCT FROM 'PENDIENTE' OR
      (accepted->'quote_payload'->'conditions'->>'precio_mensual_bob')::numeric IS DISTINCT FROM NEW.precio_mensual_bob OR
      (accepted->'quote_payload'->'conditions'->>'max_sucursales')::integer IS DISTINCT FROM NEW.max_sucursales OR
      (accepted->'quote_payload'->'conditions'->'features') IS DISTINCT FROM NEW.features OR
      (accepted->'quote_payload'->'schedule'->>'fecha_inicio')::date IS DISTINCT FROM NEW.fecha_inicio OR
      (accepted->'quote_payload'->'schedule'->>'fecha_fin')::date IS DISTINCT FROM NEW.fecha_fin OR
      (accepted->'quote_payload'->'schedule'->>'anchor_day')::integer IS DISTINCT FROM NEW.anchor_day THEN
     RAISE EXCEPTION 'Term differs from accepted conditions' USING ERRCODE='23514';
   END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tr_subscription_term ON subscription_terms;
CREATE TRIGGER tr_subscription_term BEFORE INSERT OR UPDATE OR DELETE ON subscription_terms FOR EACH ROW EXECUTE FUNCTION guard_subscription_term();
CREATE OR REPLACE FUNCTION guard_subscription_request() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner uuid;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Contract history is retained' USING ERRCODE='23514'; END IF;
 EXECUTE 'SELECT tenant_id FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscriptions WHERE id=$1' INTO owner USING NEW.subscription_id;
 IF owner IS DISTINCT FROM NEW.tenant_id THEN RAISE EXCEPTION 'Request owner mismatch' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND (OLD.estado<>'PENDIENTE' OR NEW.estado='PENDIENTE' OR NEW.lock_version<>OLD.lock_version+1 OR
 (to_jsonb(NEW)-ARRAY['estado','lock_version','decision_key','decision_fingerprint','decided_by','decided_at','reason','decision_result']) IS DISTINCT FROM
 (to_jsonb(OLD)-ARRAY['estado','lock_version','decision_key','decision_fingerprint','decided_by','decided_at','reason','decision_result'])) THEN
 RAISE EXCEPTION 'Accepted request is immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tr_subscription_request ON subscription_change_requests;
CREATE TRIGGER tr_subscription_request BEFORE INSERT OR UPDATE OR DELETE ON subscription_change_requests FOR EACH ROW EXECUTE FUNCTION guard_subscription_request();
CREATE OR REPLACE FUNCTION guard_subscription_aggregate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE initialized boolean;
BEGIN
 EXECUTE 'SELECT EXISTS(SELECT 1 FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscription_terms WHERE subscription_id=$1)' INTO initialized USING OLD.id;
 IF initialized AND (TG_OP='DELETE' OR (NEW.tenant_id,NEW.plan_id,NEW.fecha_inicio,NEW.fecha_vencimiento) IS DISTINCT FROM (OLD.tenant_id,OLD.plan_id,OLD.fecha_inicio,OLD.fecha_vencimiento)) THEN
 RAISE EXCEPTION 'Use an accepted subscription term' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tr_subscription_aggregate ON subscriptions;
CREATE TRIGGER tr_subscription_aggregate BEFORE UPDATE OR DELETE ON subscriptions FOR EACH ROW EXECUTE FUNCTION guard_subscription_aggregate();
INSERT INTO permissions(id,code,module,method,path,description,created_at)
VALUES(gen_random_uuid(),'subscriptions.manage','Suscripciones','POST','/api/v1/subscriptions/requests','Gestionar contrato de empresa propia',now()) ON CONFLICT(code) DO NOTHING;
INSERT INTO role_permissions(role_id,permission_id) SELECT r.id,p.id FROM roles r CROSS JOIN permissions p
WHERE r.name='admin' AND r.tenant_id IS NULL AND p.code='subscriptions.manage' ON CONFLICT DO NOTHING;
-- INITIAL_TERMS
INSERT INTO subscription_terms(id,tenant_id,subscription_id,version_id,fecha_inicio,fecha_fin,anchor_day,
 precio_mensual_bob,moneda,periodicidad,max_sucursales,features,origen,estado)
SELECT gen_random_uuid(),s.tenant_id,s.id,v.id,s.fecha_inicio,s.fecha_vencimiento+1,
 EXTRACT(DAY FROM s.fecha_vencimiento+1)::integer,NULL,'BOB','LEGADA',p.max_sucursales,
 jsonb_build_object('reportes_ia',p.permite_reportes_ia,'analitica_avanzada',true,'offline',p.permite_offline),'REFERENCIA_LEGADA','CONFIRMADO'
FROM subscriptions s JOIN subscription_plans p ON p.id=s.plan_id
LEFT JOIN subscription_plan_versions v ON v.plan_id=p.id AND v.origen='REFERENCIA_LEGADA' AND v.revision=1
WHERE s.estado IN ('ACTIVA','VENCIDA','SUSPENDIDA') AND NOT EXISTS(SELECT 1 FROM subscription_terms t WHERE t.subscription_id=s.id);
