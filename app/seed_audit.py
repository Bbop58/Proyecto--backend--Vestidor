"""Read-only reconciliation of demo data; returns counts, never customer data."""
from sqlalchemy import text


CHECKS = {
    "mobile_session_owner": """SELECT count(*) FROM mobile_sessions s JOIN users u ON u.id=s.user_id
        JOIN tenants t ON t.id=s.tenant_id WHERE s.revoked_at IS NULL AND
        (s.tenant_id IS DISTINCT FROM u.tenant_id OR NOT u.is_active OR t.es_operadora)""",
    "backup_policy_coverage": """SELECT count(*) FROM tenants t
        WHERE (NOT t.es_operadora AND NOT EXISTS
            (SELECT 1 FROM backup_policies p WHERE p.tenant_id=t.id))
        OR (t.es_operadora AND EXISTS
            (SELECT 1 FROM backup_policies p WHERE p.tenant_id=t.id))""",
    "sale_total_vs_details": """SELECT count(*) FROM ventas v WHERE
        v.monto_total <> COALESCE((SELECT sum(subtotal) FROM detalles_venta d WHERE d.venta_id=v.id),0)
        - v.descuento + COALESCE(v.costo_envio,0)""",
    "sale_subtotal": """SELECT count(*) FROM ventas v WHERE v.monto_subtotal IS DISTINCT FROM
        (SELECT sum(subtotal) FROM detalles_venta d WHERE d.venta_id=v.id)""",
    "detail_arithmetic": """SELECT count(*) FROM detalles_venta WHERE subtotal <> cantidad*precio_unitario OR cantidad<=0""",
    "sale_tax_and_cash": """SELECT count(*) FROM ventas WHERE
        monto_total <> monto_neto+impuesto_iva OR monto_recibido-monto_total <> cambio OR cambio<0""",
    "sale_missing_stock_movement": """SELECT count(*) FROM (
        SELECT v.numero_recibo,d.variante_id,v.sucursal_id,sum(d.cantidad) qty
        FROM ventas v JOIN detalles_venta d ON d.venta_id=v.id WHERE v.estado='COMPLETADA'
        GROUP BY v.numero_recibo,d.variante_id,v.sucursal_id) s WHERE qty <>
        COALESCE((SELECT sum(m.cantidad) FROM movimientos_inventario m JOIN inventario i ON i.id=m.inventario_id
        WHERE m.referencia=s.numero_recibo AND i.variante_id=s.variante_id AND i.sucursal_id=s.sucursal_id
        AND m.tipo='SALIDA_VENTA'),0)""",
    "stock_ledger_balance": """SELECT count(*) FROM inventario i WHERE i.stock_actual <>
        COALESCE((SELECT sum(m.stock_despues-m.stock_antes) FROM movimientos_inventario m
        WHERE m.inventario_id=i.id AND m.tipo NOT IN ('SALIDA_RESERVA','LIBERACION_RESERVA')),0)""",
    "stock_negative_or_overreserved": """SELECT count(*) FROM inventario
        WHERE stock_actual<0 OR stock_reservado<0 OR stock_reservado>stock_actual""",
    "movement_quantity": """SELECT count(*) FROM movimientos_inventario WHERE
        tipo IN ('ENTRADA','DEVOLUCION','SALIDA_VENTA') AND
        stock_despues-stock_antes <> CASE WHEN tipo='SALIDA_VENTA' THEN -cantidad ELSE cantidad END""",
    "reserved_stock": """SELECT count(*) FROM inventario i WHERE stock_reservado <>
        COALESCE((SELECT sum(d.cantidad) FROM reservas r JOIN detalles_reserva d ON d.reserva_id=r.id
        WHERE r.sucursal_id=i.sucursal_id AND d.variante_id=i.variante_id AND r.activo
        AND r.estado IN ('PENDIENTE','PREPARADA')),0)""",
    "reservation_total": """SELECT count(*) FROM reservas r WHERE total_estimado <>
        COALESCE((SELECT sum(subtotal) FROM detalles_reserva d WHERE d.reserva_id=r.id),0)""",
    "expired_active_reservations": """SELECT count(*) FROM reservas WHERE activo AND
        estado IN ('PENDIENTE','PREPARADA') AND fecha_expiracion < CURRENT_TIMESTAMP""",
    "sale_tenant": """SELECT count(*) FROM ventas v JOIN sucursales b ON b.id=v.sucursal_id
        LEFT JOIN users u ON u.id=v.cliente_id LEFT JOIN users c ON c.id=v.cajero_id
        WHERE v.tenant_id IS DISTINCT FROM b.tenant_id OR
        (u.id IS NOT NULL AND u.tenant_id IS DISTINCT FROM v.tenant_id) OR
        (c.id IS NOT NULL AND c.tenant_id IS DISTINCT FROM v.tenant_id)""",
    "inventory_tenant": """SELECT count(*) FROM inventario i JOIN sucursales b ON b.id=i.sucursal_id
        JOIN variantes_producto v ON v.id=i.variante_id JOIN productos p ON p.id=v.producto_id
        WHERE i.tenant_id IS DISTINCT FROM b.tenant_id OR i.tenant_id IS DISTINCT FROM p.tenant_id""",
    "movement_tenant_and_actor": """SELECT count(*) FROM movimientos_inventario m
        JOIN inventario i ON i.id=m.inventario_id LEFT JOIN users u ON u.id=m.usuario_id
        WHERE m.tenant_id IS DISTINCT FROM i.tenant_id OR
        (u.id IS NOT NULL AND m.tenant_id IS DISTINCT FROM u.tenant_id)""",
    "sale_variant_tenant": """SELECT count(*) FROM detalles_venta d JOIN ventas s ON s.id=d.venta_id
        JOIN variantes_producto v ON v.id=d.variante_id JOIN productos p ON p.id=v.producto_id
        WHERE s.tenant_id IS DISTINCT FROM p.tenant_id""",
    "reservation_variant_tenant": """SELECT count(*) FROM detalles_reserva d JOIN reservas r ON r.id=d.reserva_id
        JOIN variantes_producto v ON v.id=d.variante_id JOIN productos p ON p.id=v.producto_id
        WHERE r.tenant_id IS DISTINCT FROM p.tenant_id""",
    "reservation_tenant": """SELECT count(*) FROM reservas r JOIN sucursales b ON b.id=r.sucursal_id
        JOIN users u ON u.id=r.cliente_id WHERE r.tenant_id IS DISTINCT FROM b.tenant_id
        OR r.tenant_id IS DISTINCT FROM u.tenant_id""",
    "wallet_balance": """SELECT count(*) FROM user_wallets w WHERE saldo_actual <>
        COALESCE((SELECT sum(saldo_posterior-saldo_anterior) FROM wallet_transactions t WHERE t.wallet_id=w.id),0)
        OR saldo_actual<0 OR saldo_retenido<0 OR saldo_retenido>saldo_actual""",
}


def reconcile(connection):
    from app.saas_integrity import CHECKS as SAAS_CHECKS
    return {name: connection.execute(text(sql)).scalar_one()
            for name,sql in {**CHECKS,**{'saas_'+k:v for k,v in SAAS_CHECKS.items()}}.items()}
