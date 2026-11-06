-- Explicit P3 upgrade; no startup migration or synthetic payment attribution.
CREATE TABLE IF NOT EXISTS subscription_payments (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), request_id uuid NOT NULL UNIQUE REFERENCES subscription_change_requests(id),
 tenant_id uuid NOT NULL REFERENCES tenants(id),subscription_id uuid NOT NULL REFERENCES subscriptions(id),
 version_id uuid NOT NULL REFERENCES subscription_plan_versions(id), approved_by uuid NOT NULL,
 approved_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 amount_bob numeric(10,2) NOT NULL,amount_usd numeric(10,2) NOT NULL,exchange_rate numeric(14,6) NOT NULL,
 merchant_key varchar(64) NOT NULL,provider_mode varchar(10) NOT NULL,state varchar(24) NOT NULL DEFAULT 'READY',
 provider_order_id varchar(100) UNIQUE,capture_id varchar(100) UNIQUE,captured_at timestamptz,refund_id varchar(100) UNIQUE,approve_url varchar(1000),
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT ck_subscription_payment_state CHECK(state IN ('READY','CREATING','CREATED','CAPTURING','CAPTURED','APPLIED','REFUND_PENDING','REFUNDED','REVIEW','EXPIRED')),
 CONSTRAINT ck_subscription_payment_amount CHECK(amount_bob>0 AND amount_bob<100000000 AND amount_usd>0 AND amount_usd<100000000 AND exchange_rate>0 AND exchange_rate<100000000 AND provider_mode IN ('sandbox','live') AND expires_at>approved_at)
);
CREATE INDEX IF NOT EXISTS ix_subscription_payments_tenant_id ON subscription_payments(tenant_id);
CREATE TABLE IF NOT EXISTS subscription_payment_events (
 event_id varchar(128) PRIMARY KEY,fingerprint varchar(64) NOT NULL,payment_id uuid NOT NULL REFERENCES subscription_payments(id),
 event_type varchar(80) NOT NULL,processed boolean NOT NULL DEFAULT false,created_at timestamptz NOT NULL DEFAULT now()
);
-- GUARDS
CREATE OR REPLACE FUNCTION guard_subscription_payment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE accepted jsonb; found_term boolean;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Billing history is retained' USING ERRCODE='23514'; END IF;
 EXECUTE 'SELECT to_jsonb(r) FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscription_change_requests r WHERE id=$1' INTO accepted USING NEW.request_id;
 IF accepted IS NULL OR accepted->>'estado'<>'APROBADA' OR
    (accepted->>'tenant_id')::uuid IS DISTINCT FROM NEW.tenant_id OR
    (accepted->>'subscription_id')::uuid IS DISTINCT FROM NEW.subscription_id OR
    (accepted->>'version_id')::uuid IS DISTINCT FROM NEW.version_id OR
    (accepted->>'decided_by')::uuid IS DISTINCT FROM NEW.approved_by OR
    (accepted->'quote_payload'->'conditions'->>'precio_mensual_bob')::numeric IS DISTINCT FROM NEW.amount_bob OR
    accepted->'quote_payload'->>'billing_mode' IS DISTINCT FROM 'PAYPAL' THEN
   RAISE EXCEPTION 'Billing requires accepted and approved platform offer' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' AND (NEW.state<>'READY' OR NEW.provider_order_id IS NOT NULL OR NEW.capture_id IS NOT NULL OR NEW.refund_id IS NOT NULL) THEN
   RAISE EXCEPTION 'Billing must begin without provider effects' USING ERRCODE='23514';
 END IF;
 IF TG_OP='UPDATE' THEN
   IF (to_jsonb(NEW)-ARRAY['state','provider_order_id','capture_id','captured_at','refund_id','approve_url','updated_at']) IS DISTINCT FROM
      (to_jsonb(OLD)-ARRAY['state','provider_order_id','capture_id','captured_at','refund_id','approve_url','updated_at']) OR
      (OLD.provider_order_id IS NOT NULL AND OLD.provider_order_id IS DISTINCT FROM NEW.provider_order_id) OR
      (OLD.capture_id IS NOT NULL AND OLD.capture_id IS DISTINCT FROM NEW.capture_id) OR
      (OLD.captured_at IS NOT NULL AND OLD.captured_at IS DISTINCT FROM NEW.captured_at) OR
      (OLD.refund_id IS NOT NULL AND OLD.refund_id IS DISTINCT FROM NEW.refund_id) OR
      NOT ((OLD.state=NEW.state) OR (OLD.state='READY' AND NEW.state IN ('CREATING','EXPIRED')) OR
       (OLD.state='CREATING' AND NEW.state IN ('CREATED','EXPIRED','REVIEW')) OR
       (OLD.state='CREATED' AND NEW.state IN ('CAPTURING','EXPIRED','REVIEW')) OR
       (OLD.state='CAPTURING' AND NEW.state IN ('CAPTURED','EXPIRED','REVIEW')) OR
       (OLD.state='CAPTURED' AND NEW.state IN ('APPLIED','REFUND_PENDING','REVIEW')) OR
       (OLD.state='REFUND_PENDING' AND NEW.state IN ('REFUNDED','REVIEW')) OR
       (OLD.state='APPLIED' AND NEW.state='REVIEW')) THEN
     RAISE EXCEPTION 'Billing transition or identity invalid' USING ERRCODE='23514';
   END IF;
 END IF;
 IF NEW.state IN ('CREATED','CAPTURING','CAPTURED','APPLIED','REFUND_PENDING','REFUNDED') AND NEW.provider_order_id IS NULL THEN
   RAISE EXCEPTION 'Provider order required' USING ERRCODE='23514';
 END IF;
 IF NEW.state IN ('CAPTURED','APPLIED','REFUND_PENDING','REFUNDED') AND (NEW.capture_id IS NULL OR NEW.captured_at IS NULL) THEN
   RAISE EXCEPTION 'Capture evidence required' USING ERRCODE='23514';
 END IF;
 IF NEW.state='REFUNDED' AND NEW.refund_id IS NULL THEN RAISE EXCEPTION 'Refund evidence required' USING ERRCODE='23514'; END IF;
 IF NEW.state='APPLIED' THEN
   EXECUTE 'SELECT EXISTS(SELECT 1 FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscription_terms WHERE request_id=$1 AND origen=''PAYPAL'')' INTO found_term USING NEW.request_id;
   IF NOT found_term THEN RAISE EXCEPTION 'Verified billing requires its immutable term' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tr_subscription_payment ON subscription_payments;
CREATE TRIGGER tr_subscription_payment BEFORE INSERT OR UPDATE OR DELETE ON subscription_payments FOR EACH ROW EXECUTE FUNCTION guard_subscription_payment();
-- TERM_GUARD
ALTER TABLE IF EXISTS subscription_terms DROP CONSTRAINT IF EXISTS ck_term_origin;
ALTER TABLE IF EXISTS subscription_terms ADD CONSTRAINT ck_term_origin CHECK(
 (origen='REFERENCIA_LEGADA' AND precio_mensual_bob IS NULL AND accepted_by IS NULL AND periodicidad='LEGADA') OR
 (origen IN ('MANUAL','PAYPAL') AND precio_mensual_bob IS NOT NULL AND request_id IS NOT NULL AND accepted_by IS NOT NULL AND accepted_at IS NOT NULL AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND periodicidad='MENSUAL'));

CREATE OR REPLACE FUNCTION guard_subscription_term() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner uuid; overlap_found boolean; accepted jsonb; paid boolean;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Contract terms are immutable' USING ERRCODE='23514'; END IF;
 EXECUTE 'SELECT tenant_id FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscriptions WHERE id=$1' INTO owner USING NEW.subscription_id;
 IF owner IS DISTINCT FROM NEW.tenant_id THEN RAISE EXCEPTION 'Contract owner mismatch' USING ERRCODE='23514'; END IF;
 EXECUTE 'SELECT id FROM '||quote_ident(TG_TABLE_SCHEMA)||'.tenants WHERE id=$1 FOR NO KEY UPDATE' USING owner;
 EXECUTE 'SELECT id FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscriptions WHERE id=$1 FOR NO KEY UPDATE' USING NEW.subscription_id;
 EXECUTE 'SELECT EXISTS(SELECT 1 FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscription_terms WHERE subscription_id=$1 AND fecha_inicio<$3 AND fecha_fin>$2)' INTO overlap_found USING NEW.subscription_id,NEW.fecha_inicio,NEW.fecha_fin;
 IF overlap_found THEN RAISE EXCEPTION 'Contract periods overlap' USING ERRCODE='23514'; END IF;
 IF NEW.origen IN ('MANUAL','PAYPAL') THEN
   EXECUTE 'SELECT to_jsonb(r) FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscription_change_requests r WHERE id=$1' INTO accepted USING NEW.request_id;
   IF (accepted->>'subscription_id')::uuid IS DISTINCT FROM NEW.subscription_id OR
      (accepted->>'version_id')::uuid IS DISTINCT FROM NEW.version_id OR
      (accepted->>'accepted_by')::uuid IS DISTINCT FROM NEW.accepted_by OR
      (accepted->>'accepted_at')::timestamptz IS DISTINCT FROM NEW.accepted_at OR
      (NEW.origen='MANUAL' AND accepted->>'estado' IS DISTINCT FROM 'PENDIENTE') OR
      (NEW.origen='PAYPAL' AND accepted->>'estado' IS DISTINCT FROM 'APROBADA') OR
      (accepted->'quote_payload'->'conditions'->>'precio_mensual_bob')::numeric IS DISTINCT FROM NEW.precio_mensual_bob OR
      (accepted->'quote_payload'->'conditions'->>'max_sucursales')::integer IS DISTINCT FROM NEW.max_sucursales OR
      (accepted->'quote_payload'->'conditions'->'features') IS DISTINCT FROM NEW.features OR
      (accepted->'quote_payload'->'schedule'->>'fecha_inicio')::date IS DISTINCT FROM NEW.fecha_inicio OR
      (accepted->'quote_payload'->'schedule'->>'fecha_fin')::date IS DISTINCT FROM NEW.fecha_fin OR
      (accepted->'quote_payload'->'schedule'->>'anchor_day')::integer IS DISTINCT FROM NEW.anchor_day THEN
     RAISE EXCEPTION 'Term differs from accepted conditions' USING ERRCODE='23514';
   END IF;
 END IF;
 IF NEW.origen='PAYPAL' THEN
   EXECUTE 'SELECT EXISTS(SELECT 1 FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscription_payments WHERE request_id=$1 AND tenant_id=$2 AND subscription_id=$3 AND version_id=$4 AND state=''CAPTURED'' AND capture_id IS NOT NULL AND amount_bob=$5 AND approved_by=$6)' INTO paid USING NEW.request_id,NEW.tenant_id,NEW.subscription_id,NEW.version_id,NEW.precio_mensual_bob,NEW.approved_by;
   IF NOT paid THEN RAISE EXCEPTION 'PayPal term requires verified capture' USING ERRCODE='23514'; END IF;
 ELSIF NEW.origen='MANUAL' AND accepted->'quote_payload'->>'billing_mode'='PAYPAL' AND NEW.precio_mensual_bob>0 THEN
   RAISE EXCEPTION 'Paid offer cannot bypass billing' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
