-- Phase 3: run on a backup/copy first. Does not certify/re-sign legacy wallets.
BEGIN;
LOCK TABLE user_wallets, wallet_transactions IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM user_wallets WHERE NOT(saldo_actual BETWEEN 0 AND 99999999.99) OR NOT(saldo_retenido BETWEEN 0 AND saldo_actual)
    OR nonce_secuencia<0 OR moneda<>'BOB')
 OR EXISTS(SELECT 1 FROM wallet_transactions WHERE NOT(saldo_anterior BETWEEN 0 AND 99999999.99) OR NOT(saldo_posterior BETWEEN 0 AND 99999999.99)
    OR NOT(monto > -100000000 AND monto < 100000000) OR saldo_posterior<>saldo_anterior+monto) THEN
   RAISE EXCEPTION 'Conciliar billeteras y ledger antes de aplicar fase 3';
 END IF;
END $$;
ALTER TYPE payment_method ADD VALUE IF NOT EXISTS 'BILLETERA';

CREATE TABLE IF NOT EXISTS payment_intents (
	id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	cliente_id UUID, 
	sucursal_id UUID, 
	purpose VARCHAR(10) NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	provider_order_id VARCHAR(100), 
	capture_id VARCHAR(100), 
	refund_id VARCHAR(100), 
	amount_bob NUMERIC(10, 2) NOT NULL, 
	amount_usd NUMERIC(10, 2) NOT NULL, 
	exchange_rate NUMERIC(14, 6) NOT NULL, 
	merchant_key VARCHAR(64) NOT NULL, 
	provider_mode VARCHAR(10) NOT NULL, 
	payload JSONB NOT NULL, 
	sale_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_payment_positive CHECK (amount_bob > 0 AND amount_bob < 100000000 AND amount_usd > 0 AND amount_usd < 100000000 AND exchange_rate > 0 AND exchange_rate < 100000000), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(cliente_id) REFERENCES users (id), 
	FOREIGN KEY(sucursal_id) REFERENCES sucursales (id), 
	UNIQUE (provider_order_id), 
	UNIQUE (capture_id), 
	UNIQUE (sale_id), 
	FOREIGN KEY(sale_id) REFERENCES ventas (id)
)

;
CREATE INDEX IF NOT EXISTS ix_payment_intents_tenant_id ON payment_intents (tenant_id);
CREATE INDEX IF NOT EXISTS ix_payment_intents_user_id ON payment_intents (user_id);
ALTER TABLE wallet_transactions ADD COLUMN IF NOT EXISTS payment_intent_id UUID;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='wallet_transactions'::regclass AND conname='fk_wallet_payment_intent') THEN
   ALTER TABLE wallet_transactions ADD CONSTRAINT fk_wallet_payment_intent FOREIGN KEY(payment_intent_id) REFERENCES payment_intents(id);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='wallet_transactions'::regclass AND conname='uq_wallet_payment_intent') THEN
   ALTER TABLE wallet_transactions ADD CONSTRAINT uq_wallet_payment_intent UNIQUE(payment_intent_id);
 END IF;
 ALTER TABLE user_wallets DROP CONSTRAINT IF EXISTS ck_wallet_balance;
 ALTER TABLE user_wallets ADD CONSTRAINT ck_wallet_balance CHECK(saldo_actual BETWEEN 0 AND 99999999.99 AND saldo_retenido BETWEEN 0 AND saldo_actual AND nonce_secuencia>=0 AND moneda='BOB');
 ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS ck_wallet_ledger;
 ALTER TABLE wallet_transactions ADD CONSTRAINT ck_wallet_ledger CHECK(saldo_anterior BETWEEN 0 AND 99999999.99 AND saldo_posterior BETWEEN 0 AND 99999999.99 AND monto > -100000000 AND monto < 100000000 AND saldo_posterior=saldo_anterior+monto);
 ALTER TABLE payment_intents DROP CONSTRAINT IF EXISTS ck_payment_positive;
 ALTER TABLE payment_intents ADD CONSTRAINT ck_payment_positive CHECK(amount_bob>0 AND amount_bob<100000000 AND amount_usd>0 AND amount_usd<100000000 AND exchange_rate>0 AND exchange_rate<100000000);
END $$;
COMMIT;

-- PostgreSQL requires a commit before using a newly added enum label.
-- Reclassify only receipts with an exact owner/company/amount ledger match.
-- This does not certify legacy funds or re-sign any wallet.
BEGIN;
UPDATE ventas AS sale SET metodo_pago='BILLETERA'
WHERE sale.metodo_pago='PAYPAL' AND EXISTS (
 SELECT 1 FROM wallet_transactions AS tx JOIN user_wallets AS wallet ON wallet.id=tx.wallet_id
 WHERE tx.tipo IN ('PAGO_ORDEN_OFFLINE','PAGO_ORDEN_ONLINE')
 AND tx.referencia_externa=sale.numero_recibo AND tx.tenant_id=sale.tenant_id
 AND wallet.tenant_id=sale.tenant_id AND wallet.user_id=sale.cliente_id AND -tx.monto=sale.monto_total
);
COMMIT;
