-- Run on a backup/copy first. Does not repair or reinterpret existing stock.
-- An inconsistent database fails atomically and requires explicit reconciliation.
BEGIN;
LOCK TABLE inventario IN SHARE ROW EXCLUSIVE MODE;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM inventario WHERE stock_actual < 0
        OR stock_reservado < 0 OR stock_reservado > stock_actual OR stock_minimo < 0) THEN
        RAISE EXCEPTION 'Inventario inconsistente: conciliar existencias antes de aplicar fase 2';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_inventario_stock_consistente' AND conrelid = 'inventario'::regclass) THEN
        ALTER TABLE inventario ADD CONSTRAINT ck_inventario_stock_consistente
            CHECK (stock_actual >= 0 AND stock_reservado >= 0
                AND stock_reservado <= stock_actual AND stock_minimo >= 0);
    END IF;
END $$;
COMMIT;
