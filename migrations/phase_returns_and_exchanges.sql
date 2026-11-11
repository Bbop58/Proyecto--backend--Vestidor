-- Additive migration: enables cash/wallet returns without replacing historical exchanges.
ALTER TABLE cambios_producto
    ADD COLUMN IF NOT EXISTS tipo_operacion VARCHAR(20) NOT NULL DEFAULT 'CAMBIO';
ALTER TABLE cambios_producto
    ADD COLUMN IF NOT EXISTS estado_liquidacion VARCHAR(24) NOT NULL DEFAULT 'CONFIRMADO';
ALTER TABLE cambios_producto
    ADD COLUMN IF NOT EXISTS referencia_reembolso VARCHAR(100);

ALTER TABLE detalles_cambio_producto
    ALTER COLUMN variante_entregada_id DROP NOT NULL;
ALTER TABLE detalles_cambio_producto
    ALTER COLUMN cantidad_entregada SET DEFAULT 0;
ALTER TABLE detalles_cambio_producto
    ALTER COLUMN precio_unitario_entregado SET DEFAULT 0;

ALTER TABLE detalles_cambio_producto
    DROP CONSTRAINT IF EXISTS ck_detalle_cambio_devolucion;
ALTER TABLE detalles_cambio_producto
    ADD CONSTRAINT ck_detalle_cambio_devolucion
    CHECK (cantidad_devuelta > 0 AND precio_unitario_devuelto >= 0);

ALTER TABLE cambios_producto
    DROP CONSTRAINT IF EXISTS ck_cambios_tipo_operacion;
ALTER TABLE cambios_producto
    ADD CONSTRAINT ck_cambios_tipo_operacion
    CHECK (tipo_operacion IN ('CAMBIO', 'DEVOLUCION'));
ALTER TABLE cambios_producto
    DROP CONSTRAINT IF EXISTS ck_cambios_estado_liquidacion;
ALTER TABLE cambios_producto
    ADD CONSTRAINT ck_cambios_estado_liquidacion
    CHECK (estado_liquidacion IN ('CONFIRMADO', 'PENDIENTE', 'REEMBOLSO_PENDIENTE', 'REVISION'));

ALTER TABLE detalles_cambio_producto
    DROP CONSTRAINT IF EXISTS ck_detalle_cambio_entrega;
ALTER TABLE detalles_cambio_producto
    ADD CONSTRAINT ck_detalle_cambio_entrega CHECK (
        (variante_entregada_id IS NULL AND cantidad_entregada = 0 AND precio_unitario_entregado = 0)
        OR (variante_entregada_id IS NOT NULL AND cantidad_entregada > 0 AND precio_unitario_entregado >= 0)
    );
