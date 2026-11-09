# Comprobantes — F2 terminada localmente

9/11/2026. Solo backend; no commit/push/deploy, cambios en Railway, nueva APK ni correo enviado a destinatarios reales. F1 continúa local y F3 pendiente.

## Implementado

- `POST /api/v1/receipts/{kind}/{id}/email`: destinatario y UUID de operación; documento autorizado y PDF generados por los mismos servicios de F1, sin adjunto/importe arbitrario del cliente.
- `GET /api/v1/receipts/capabilities`: informa si hay configuración de correo sin exponer secretos. La descarga PDF funciona aunque el correo esté desactivado.
- SMTP autenticado con TLS obligatorio, puerto 587/STARTTLS o 465/TLS, timeout de socket 15 s. Ruta FastAPI síncrona ejecutada en su pool de hilos; sin worker, cron ni servicio adicional.
- Registro operacional `receipt_email_deliveries` para idempotencia: UUID por empresa/actor, destinatario, huella de solicitud/PDF y estado. No otra tabla de comprobantes ni numeración comercial.
- Doble clic concurrente con dos conexiones reales: una reserva durable PENDING y un solo envío; la otra petición devuelve el estado existente. El bloqueo corto de PostgreSQL se libera antes de SMTP.
- Límites: 5 intentos por usuario en 10 minutos; 60 s entre reenvíos del mismo documento/destinatario. UUID repetido no consume otro envío; UUID con otro payload devuelve 409.
- Estados PENDING/ACCEPTED/FAILED/UNKNOWN. Fallo antes de aceptación: FAILED; incertidumbre después de posible aceptación: UNKNOWN; sin reintentos automáticos. PENDING antiguo de más de dos minutos se convierte en UNKNOWN. QUIT fallido después de aceptación no genera otro intento.
- El resultado explica que ACCEPTED significa aceptación del servidor de correo, no entrega comprobada al buzón. El fallo de correo no anula ni vuelve a registrar la venta.

## Datos y restauración

DDL aditiva explícita `scripts/migrate_receipt_email.py`, repetible; el reset demo ya crea la tabla por metadata. Campo tenant_id integrado al aislamiento genérico existente. Marcador `__operational_metadata__=True` excluye envíos de los respaldos comerciales.

El actor conserva su UUID sin FK de borrado en cascada: reemplazar usuarios al restaurar datos comerciales no borra los recibos de envío ya aceptados y no habilita duplicados por reintento. La autorización siempre se vuelve a comprobar sobre el usuario actual y el documento de su empresa. El registro sí pertenece a la empresa por FK.

## Pruebas

- RED inicial por modelo/servicio ausentes; antes de implementar.
- Integración F1+correo+backup contractual: **56 PASS**, 42,45 s, cero skips. Antes de las tres pruebas finales adicionales del transporte SMTP.
- Correo definitivo: **19 PASS**, 14,66 s, cero skips; incluye esas tres adicionales. Estas corridas se superponen: no sumar como una suite única de 75.
- Cobertura: idempotencia/replay, concurrencia real, límites, otra empresa sin SMTP, desactivación, manipulación de cabeceras, cambio de payload, fallo/reinicio incierto, preservación del registro ante reemplazo de usuario, TLS ambos puertos, adjunto exacto sin contraseña, QUIT tras aceptación y rutas HTTP.
- Migración ejecutada dos veces sobre esquema QA propio con datos: las otras tablas permanecen idénticas.
- Base exclusiva de seed `fashionstore_test_receipts_f2_seed`: fresh autorizado **solo local**, seed repetido idéntico y **28 controles PASS**; 1155 ventas/64 usuarios sin operador opcional y **0 receipt_email_deliveries**. No correos ni llamadas a proveedor durante el seed.
- `git diff --check` y compilación sintáctica aprobados. Avisos FastAPI existentes sin ampliar alcance.

## Gmail privado y aceptación pendiente

El usuario eligió su cuenta Gmail y proporcionó una contraseña de aplicación. Se guardó exclusivamente fuera del proyecto en `LOCALAPPDATA/FashionStore/receipt-email/smtp.pending.json`, sin copiar el secreto a estos documentos/código/seed/logs.

Prueba real desde esta computadora: conexión STARTTLS y autenticación Gmail **PASS**, sin enviar mensaje. No prueba conectividad saliente desde Railway ni llegada al buzón. No se aplicaron variables remotas para evitar un reset antes de completar F3.

F3 debe leer la configuración privada y aplicar las variables SMTP en Railway al publicar; comprobar conectividad real del hosting. Luego integrar botones web/móvil y enviar un documento sintético a cada uno de los cinco destinatarios autorizados, con confirmación del usuario. Si Railway impide el transporte SMTP, registrar ese bloqueo y resolverlo; nunca declarar enviados correos que solo pasaron un doble de prueba.

No requiere acceso a los buzones de los destinatarios ni el complemento Gmail. F2 no certifica correo recibido, UI integrada ni versión publicada. PostgreSQL QA propio se detiene al cierre; las bases de prueba se conservan.
