-- Explicit transaction only on existing databases; no startup migration.
ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS catalog_managed boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS subscription_plan_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 plan_id uuid NOT NULL REFERENCES subscription_plans(id) ON DELETE RESTRICT,
 revision integer NOT NULL,
 estado varchar(20) NOT NULL DEFAULT 'BORRADOR',
 lock_version integer NOT NULL DEFAULT 0,
 precio_mensual_bob numeric(10,2) NOT NULL,
 moneda varchar(3) NOT NULL DEFAULT 'BOB',
 periodicidad varchar(10) NOT NULL DEFAULT 'MENSUAL',
 max_sucursales integer NOT NULL,
 permite_reportes_ia boolean NOT NULL,
 permite_analitica_avanzada boolean NOT NULL,
 permite_offline boolean NOT NULL,
 descripcion text,
 origen varchar(25) NOT NULL DEFAULT 'PLATAFORMA',
 published_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_plan_version_revision UNIQUE(plan_id,revision),
 CONSTRAINT ck_plan_version_limits CHECK(revision>0 AND lock_version>=0 AND max_sucursales>=1),
 CONSTRAINT ck_plan_version_price CHECK(precio_mensual_bob>=0 AND precio_mensual_bob != 'NaN'::numeric),
 CONSTRAINT ck_plan_version_billing CHECK(moneda='BOB' AND periodicidad='MENSUAL'),
 CONSTRAINT ck_plan_version_state CHECK(estado IN ('BORRADOR','PUBLICADA','RETIRADA')),
 CONSTRAINT ck_plan_version_origin CHECK(origen IN ('PLATAFORMA','REFERENCIA_LEGADA')),
 CONSTRAINT ck_plan_version_publication CHECK((estado='BORRADOR' AND published_at IS NULL) OR (estado!='BORRADOR' AND published_at IS NOT NULL))
);

CREATE OR REPLACE FUNCTION protect_plan_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   RAISE EXCEPTION 'Plan versions are retained' USING ERRCODE='23514';
 END IF;
 IF NEW.id<>OLD.id OR NEW.plan_id<>OLD.plan_id OR NEW.revision<>OLD.revision OR NEW.origen<>OLD.origen OR NEW.created_at<>OLD.created_at THEN
   RAISE EXCEPTION 'Plan version identity is immutable' USING ERRCODE='23514';
 END IF;
 IF NEW.lock_version<>OLD.lock_version+1 THEN
   RAISE EXCEPTION 'Version counter must advance once' USING ERRCODE='23514';
 END IF;
 IF OLD.estado<>'BORRADOR' AND
    ((to_jsonb(NEW)-'estado'-'lock_version') IS DISTINCT FROM (to_jsonb(OLD)-'estado'-'lock_version')
     OR (OLD.estado='RETIRADA' AND NEW.estado<>'RETIRADA')
     OR (OLD.estado='PUBLICADA' AND NEW.estado NOT IN ('PUBLICADA','RETIRADA'))) THEN
   RAISE EXCEPTION 'Published conditions are immutable' USING ERRCODE='23514';
 END IF;
 IF OLD.estado='BORRADOR' AND NEW.estado='RETIRADA' THEN
   RAISE EXCEPTION 'Publish a draft before retiring' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tr_protect_plan_version ON subscription_plan_versions;
CREATE TRIGGER tr_protect_plan_version BEFORE UPDATE OR DELETE ON subscription_plan_versions FOR EACH ROW EXECUTE FUNCTION protect_plan_version();

CREATE OR REPLACE FUNCTION protect_legacy_plan_conditions() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE has_versions boolean;
BEGIN
 EXECUTE 'SELECT EXISTS(SELECT 1 FROM '||quote_ident(TG_TABLE_SCHEMA)||'.subscription_plan_versions WHERE plan_id=$1)' INTO has_versions USING OLD.id;
 IF (has_versions OR OLD.catalog_managed) AND (NEW.id,NEW.codigo,NEW.catalog_managed,NEW.max_sucursales,NEW.precio_mensual_bob,NEW.permite_reportes_ia,NEW.permite_offline)
    IS DISTINCT FROM (OLD.id,OLD.codigo,OLD.catalog_managed,OLD.max_sucursales,OLD.precio_mensual_bob,OLD.permite_reportes_ia,OLD.permite_offline) THEN
   RAISE EXCEPTION 'Legacy terms are frozen; publish a new version' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tr_protect_legacy_plan_conditions ON subscription_plans;
CREATE TRIGGER tr_protect_legacy_plan_conditions BEFORE UPDATE ON subscription_plans FOR EACH ROW EXECUTE FUNCTION protect_legacy_plan_conditions();

-- INITIAL_REFERENCE
-- No accepted historic price is invented. Previous analytics access remains true.
INSERT INTO subscription_plan_versions(id,plan_id,revision,estado,lock_version,precio_mensual_bob,moneda,periodicidad,
 max_sucursales,permite_reportes_ia,permite_analitica_avanzada,permite_offline,descripcion,origen,published_at,created_at)
SELECT gen_random_uuid(),p.id,1,'PUBLICADA',0,p.precio_mensual_bob,'BOB','MENSUAL',p.max_sucursales,
 p.permite_reportes_ia,true,p.permite_offline,p.descripcion,'REFERENCIA_LEGADA',now(),now()
FROM subscription_plans p WHERE NOT p.catalog_managed AND NOT EXISTS(SELECT 1 FROM subscription_plan_versions v WHERE v.plan_id=p.id)
ON CONFLICT (plan_id,revision) DO NOTHING;
