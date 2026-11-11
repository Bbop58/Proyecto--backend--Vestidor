# Devoluciones y cambios de prendas: cierre local

**Fecha:** 10/11/2026

**Estado:** publicado en backend y web el 11/11/2026. El cierre local quedó registrado antes del despliegue; esta sección añade la verificación de publicación.

## Comportamiento acordado

- Un cambio devuelve una o más unidades de la venta original y, si corresponde, entrega otras prendas. Una devolución no exige seleccionar una prenda de reemplazo.
- El precio usado se cotiza en el servidor. Si el valor entregado es mayor, se cobra la diferencia en efectivo o PayPal; si es menor, se devuelve en efectivo, a la billetera del cliente identificado o a PayPal cuando la venta original fue pagada por PayPal. Si los valores son iguales, no se cobra ni devuelve diferencia.
- La devolución PayPal se solicita contra la captura PayPal original. No deposita a una cuenta arbitraria mediante un enlace de inicio de sesión.
- Una solicitud PayPal pendiente o de resultado incierto reserva las unidades para impedir una segunda devolución. El reintento conserva la misma clave idempotente; solo un rechazo definitivo libera la solicitud para resolverla con otro medio.
- Se valida empresa, venta completada, sucursal, plazo exacto de 30 días, cantidades aún disponibles, estado de la prenda, inventario de reemplazo, cotización vigente y permisos. Un defecto de fábrica se registra como merma, no como existencia vendible.
- La operación registrada conserva el comprobante de cambio/devolución en PDF. Los reintentos no duplican movimientos, acreditaciones ni comprobantes.

## Verificación ejecutada

- Backend: `tests/test_exchange_corrections.py` y `tests/test_exchanges.py`: **18 aprobadas**. Incluye cambio y devolución sin reemplazo, importes iguales/diferentes, efectivo y billetera, cliente anónimo, límite de unidades y plazo, aislamiento de empresa, defecto/merma, comprobante, repetición idempotente, concurrencia y estados de reembolso PayPal (pendiente, timeout/reintento, tope, rechazo y confirmación).
- Web: prueba enfocada de `product-exchange.component.spec.ts`: **7 aprobadas**.
- Compilación Python y `git diff --check`: correctos. La compilación de producción web también terminó correctamente; conserva advertencias existentes de presupuesto del bundle y dependencias CommonJS.
- Las pruebas usaron una base PostgreSQL local desechable. Al terminar quedó con **0 tablas y 0 filas estimadas**; no se cambió la base demo.
- PayPal fue simulado en pruebas; no se hizo un cobro ni reembolso real sandbox o producción.

## Publicación verificada

- Backend: commit `95f983b` publicado en `main`; Railway deployment `2e831efc-a599-4d92-8596-7c67a861a800` terminó en `SUCCESS`. `/health/ready` respondió `200`, con base alcanzable, esquema compatible y commit `95f983b77c6750f5f5fa27ead37dcc51f3377858`.
- Web: commit `53a3000` publicado en `main`; Vercel sirvió el bundle `main-HAPR56ZW.js` con HTTP `200`. Su SHA-256 remoto coincide con el artefacto local: `334288265317d47235e20a7595c12a7197e4a1e1e6726ebf073f7f8684223690`.
- El predeploy configurado reconstruyó el esquema desde los modelos y ejecutó el reset/reseed de demo acordado: **28 controles aprobados**, seed repetido sin diferencias. Por eso no se ejecutó manualmente la migración sobre una base previa.
- La API pública de empresas respondió `200`. Las pruebas locales detalladas arriba cubren el flujo; no se hizo una devolución real en PayPal sandbox o producción. PayPal sigue validado con dobles de prueba, no con una transacción externa.
- No se modificó ni publicó Mobile; el cambio es de backend y web.

## Prueba manual rápida

1. En Web, como cajero autorizado, abre **Ventas → Cambios**, busca el recibo y selecciona una unidad devuelta.
2. Elige **Cambio** o **Devolución**, define condición y motivo, y cotiza antes de confirmar.
3. Prueba una devolución en efectivo; otra a billetera con cliente identificado; y, solo para una venta PayPal, la devolución PayPal. Para cambio, compara un reemplazo con precio mayor, igual y menor.
4. Comprueba el estado del comprobante/PDF, el stock y el saldo de billetera cuando aplique. Repite la misma solicitud y verifica que no haya un segundo registro.
