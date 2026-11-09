# Comprobante general PDF y correo — plan de implementación

> **Estado 9/11/2026: F1 terminada localmente.** 34 pruebas nuevas PASS y 75 de regresión comercial PASS en corridas identificadas; PDFs renderizados/revisados. Ver docs/auditoria_software/COMPROBANTES_F1_CIERRE.md. F2/F3 pendientes. Sin publicación aislada ni correo enviado.

> **Actualización posterior: F2 terminada localmente.** 56 pruebas de integración PASS y 19 correo definitivas PASS (corridas superpuestas); seeder doble idéntico/28 controles/0 envíos, migración aditiva repetida. SMTP Gmail autenticado desde la computadora con credencial privada fuera de Git, sin enviar mensajes. Ver COMPROBANTES_F2_CIERRE.md. Configuración y conectividad Railway/entrega real, botones y APK permanecen en F3; no publicar ni fingir llegada al buzón antes de ello.

> Para ejecución: tareas secuenciales con checkboxes, sin subagentes. Aplicar writing-plans y systematic-debugging ante fallos; angular-developer para Angular. Las subskills superpowers no están disponibles y no se presuponen. Ubicación acorde a la documentación existente del proyecto.

**Objetivo:** descargar o enviar una nota sencilla por operación en web y móvil, con una plantilla y estados honestos.

**Arquitectura:** lectores autorizados sobre operaciones existentes → contrato común → PDF backend canónico. Renderizado local solo para provisionales offline. Un envío SMTP manual e idempotente, sin servicios nuevos y sin alterar la operación comercial.

**Stack:** FastAPI/SQLAlchemy/PostgreSQL; ReportLab con fuente Unicode local; Angular/jsPDF ya existente; Flutter `pdf` y `printing` para generar/guardar/compartir el provisional y guardar el PDF central. SMTP estándar Python con TLS.

**Especificación:** [COMPROBANTES_PDF_CORREO_SPEC.md](COMPROBANTES_PDF_CORREO_SPEC.md).

## Restricciones globales

- Leer la especificación completa. Una plantilla; botones Descargar PDF/Enviar al correo; sin automatizar correos ni modificar pagos/stock/billetera/reservas.
- Reserva no acredita pago, offline no confirmado es PROVISIONAL, PayPal requiere estado central confirmado. No impuestos calculados/factura fiscal.
- Raíz real C:/Users/Laptop/FashionStore-recuperado; repos independientes. GitHub D-blip-max. Preservar cambios desktop existentes.
- Sin Admin JSON/contraseñas/tokens/correos de prueba en seed o código. No usar el complemento Gmail como transporte del producto.
- Autorizaciones anteriores se conservan; esta petición solo arma el plan, no implementa ni envía correos.
- Publicar al final en una sola tanda backend/web/mobile; el backend resetea demo: recrear fixtures luego, no probar sobre IDs anteriores. No repetir aceptación extensa de notificaciones ni preparar más emuladores.

## Revisión prioritaria

1. Documento de otra empresa/cliente: 404 y cero envío (F1/F2).
2. PayPal aprobado pero aún no capturado, reserva y UUID offline: nunca imprimir PAGADO sin confirmación (F1/F3).
3. Precio catálogo distinto, descuentos/envío, centavos y cambio: importes históricos exactos, sin IVA inventado (F1).
4. Doble clic, pérdida de respuesta SMTP y cambio de usuario durante descarga: sin correos automáticos duplicados ni datos de otra sesión (F2/F3).
5. Cliente sin correo, SMTP ausente, red caída, varias páginas y acentos: mensaje claro, PDF legible y operación comercial intacta (F1/F2/F3).

## F1 — Documento común y descarga PDF

**Archivos backend:** crear `app/schemas/receipt.py`, `app/services/receipt_document_service.py`, `app/services/receipt_pdf_service.py`, `app/api/v1/receipts.py`, `tests/test_receipt_documents.py`, `tests/test_receipt_pdf.py`; registrar router en `app/api/v1/router.py`. Reutilizar `app/services/receipt_service.py`, rutas existentes sales/exchanges y lectores de reservas/wallet; conservar contratos legacy.

**Interfaces:** `get_receipt_document(db, actor, kind, entity_id) -> ReceiptDocument`, `render_receipt_pdf(document) -> bytes`; kinds `sale|reservation|wallet_topup|exchange`. `GET /api/v1/receipts/{kind}/{id}` JSON y `GET .../pdf` application/pdf, nombre sanitizado, Cache-Control private/no-store. No nueva numeración. Contrato v1 con strings decimales BOB y fechas ISO con zona; etiquetas de estado comerciales explícitas.

- [x] Crear pruebas que fallen para venta con cada método, reserva sin pago, recarga confirmada, débito wallet ligado a venta y cambio con diferencia acreditada. Cubrir 404 ajeno, superadmin denegado y alcance sucursal conforme permisos actuales.
- [x] Implementar lectores reutilizando valores guardados; suprimir el fallback IVA13% y datos empresariales inventados en el documento nuevo. No recalcular ni corregir importes históricos guardados. Faltante esencial → error claro, no PDF ficticio.
- [x] Renderizar plantilla A4 con fuente local/tabla y saltos; pruebas extraen número/estado/totales/talla/color y comprueban PDF multipágina. Renderizar muestras de dos empresas y revisar que no se corte texto.
- [x] Verificar documentos de PENDIENTE_PAGO, CANCELADA, reserva vencida/cancelada, recarga fallida no acreditada y código provisional. GET no escribe tablas comerciales ni llama PayPal.
- [x] Ejecutar `.venv/Scripts/python.exe -m pytest tests/test_receipt_documents.py tests/test_receipt_pdf.py -q --tb=short` en DB QA exclusiva. Cerrar F1 en `docs/auditoria_software/COMPROBANTES_F1_CIERRE.md`; sin despliegue aislado.

**Salida:** PDF canónico autorizado; no UI ni correo declarados listos.

## F2 — Correo manual sencillo

**Archivos backend:** crear `app/services/receipt_email_service.py`, `app/models/receipt_email_delivery.py`, `app/core/receipt_email_ddl.py`, `tests/test_receipt_email.py`; extender schemas/receipts/router/config, registro de modelos y mecanismo de migración explícito existente. Revisar exclusiones de backup y auditor del reset demo/seed sin crear datos de envío.

**Interfaces:** `send_receipt_email(db, actor, kind, entity_id, recipient, operation_id) -> ReceiptEmailResult`; `POST .../email` con `{recipient, operation_id}`. `GET /api/v1/receipts/capabilities` devuelve email_enabled sin secretos, ruta fija antes de dinámicas. F2 usa exactamente `get_receipt_document` y `render_receipt_pdf` de F1.

- [x] Pruebas fallan primero: mismo UUID/doble clic concurrente → un envío; actor ajeno → cero conexión SMTP; límite 5/10min y 60s documento/destinatario; clave reutilizada con payload distinto →409.
- [x] Añadir tabla operacional y DDL aditiva repetible: unique empresa+actor+operation_id; huella/destinatario/estado. Dos migraciones sobre esquema QA poblado, seed repetido sin cambios comerciales/filas de correo. Excluir tabla de backup empresarial para no restaurar/reemitir envíos.
- [x] Implementar SMTP TLS con timeout15s y adjunto generado en servidor, sin aceptar HTML/cabeceras/adjuntos del cliente. Destinatario validado; from configurado, no del usuario. Ejecutar fuera del event loop con hilo; no worker nuevo.
- [x] Probar errores antes/después de aceptación: FAILED/UNKNOWN claros; no retry automático ni modificación de la venta. UUID repetido devuelve estado existente; PENDING antiguo queda UNKNOWN antes de cualquier reenvío manual explícito. Estado ACCEPTED se comunica «Correo aceptado para envío», no recepción garantizada.
- [x] Remitente Gmail seleccionado y credencial fuera de Git; conexión TLS/autenticación local PASS, sin correo enviado. Configuración y conectividad Railway se verifican al publicar en F3 para no provocar un reset anticipado.
- [x] Ejecutar las pruebas de correo/documentos/backup y escribir `COMPROBANTES_F2_CIERRE.md` con qué falta para entrega real. No mandar aún a los cinco destinatarios ni desplegar aislado.

**Salida:** envío manual idempotente y configuración verificable; la entrega real se acepta en F3.

## F3 — Botones, offline y aceptación/publicación

**Archivos web:** crear `src/app/core/models/receipt.model.ts`, `core/services/receipt-document.service.ts`, `shared/receipt-actions/receipt-actions.component.ts` y specs; conectar `features/sales/pos`, `sales/sale-list`, `sales/wallet-admin`, `reservations/reservation-list`, `reservations/reservation-form`, `sales/product-exchange` y el checkout/historial de cliente que realmente usa el router. Inventariar estas últimas rutas antes de tocar sus pantallas; no reconstruir checkout.

**Archivos móvil:** crear `lib/models/receipt_document.dart`, `lib/services/receipt_document_service.dart`, `lib/widgets/receipt_actions.dart`, `test/receipt_document_test.dart`, `test/receipt_actions_test.dart`; integrar `screens/home/purchase_history_screen.dart`, `shop/cart_screen.dart`, `sales/cash_sale_screen.dart`, `reservations/reservations_screen.dart`, `reservations/reservation_detail_screen.dart`, `wallet/wallet_screen.dart` y consulta de operación sincronizada. Reutilizar historial wallet para cada recarga, no solo el último modal.

**Interfaces UI:** `ReceiptRef(kind,id)` para central y `ReceiptDocument` provisional local. Acción PDF central descarga bytes F1; provisional genera local a partir del registro durable existente. El modal de correo usa F2, predetermina destinatario conocido, muestra Confirmar envío, busy y estado; UUID se conserva durante el mismo intento/reintento de red. Cambiar de cuenta cancela/descarta resultados tardíos.

- [ ] Pruebas UI que fallen: todos los puntos de entrada muestran las dos acciones; doble clic no duplica POST, error de correo no borra compra, switching cancela descarga/envío tardío y offline bloquea correo con explicación.
- [ ] Implementar componente/widgets compartidos sin duplicar lógica por método de pago. Solo después de operación guardada; al volver a historial las acciones siguen disponibles. Mostrador sin cliente puede introducir correo destino explícito.
- [ ] Generar provisional sin red de efectivo y wallet (y reserva offline existente) desde sus snapshots/UUID, usando el mismo contrato/estilo. Revisar qué nombres empresariales faltan en la instantánea: añadir metadatos aditivos al payload JSON local sin migrar SQLite ni alterar firmas. No llamar servidor ni reutilizar estado de otra empresa; al sincronizar enlazar id/recibo central del mismo UUID.
- [ ] Asegurar exportación nativa real con `pdf`/`printing`; Android ofrece Guardar como PDF/compartir mediante sistema y web descarga archivo. Validar disponibilidad de nombres/fuentes offline empaquetados, sin buscar logo/URL externa.
- [ ] Probar PDF web y Android: efectivo online/offline, PayPal capturado, compra wallet online/offline, reserva y recarga. Reutilizar pruebas de pago existentes, mocks de proveedor en regresión; como máximo las operaciones sandbox nuevas necesarias para una aceptación real, nunca cobros reales.
- [ ] Ejecutar regresión pertinente backend/web/móvil, build web y una APK release con versionCode mayor al instalado. No compilar debug e inventar después que se entregó release. Sin repetir suites después de cambios solo documentales.
- [ ] Publicación conjunta autorizada con D-blip-max, respaldos/configuración SMTP privada, predeploy demo esperado y aceptación sobre fixtures posteriores al reset. Verificar health/commit/Vercel; no redesplegar durante prueba de correo.
- [ ] Enviar **un correo sintético a cada una de las cinco direcciones autorizadas** en la especificación, distribuyendo venta/reserva/recarga entre ambas empresas. Registrar tipo/empresa/dirección/resultado sin cuerpos privados; usuario confirma llegada y PDF legible. Los correos quedan en notas QA privadas, no en código/seed; no necesita prestarnos acceso a sus buzones.
- [ ] Comparar datos del adjunto con la descarga del mismo estado; repetir la misma petición de envío confirma idempotencia sin otro mensaje. Descargar/enviar no crea venta/recarga ni mueve stock. Repetir snapshot comercial antes/después de la operación documental.
- [ ] Entregar APK y `COMPROBANTES_COMO_PROBAR.md`; cerrar `COMPROBANTES_F3_CIERRE.md` y `COMPROBANTES_ARTEFACTOS.json` con comandos/resultados/hash/limitaciones. Copiar plan/spec/cierres a docs/comprobantes de cada repo; no iniciar otra ampliación.

**Salida:** una plantilla usable desde todas las operaciones acordadas, descarga real y correo realmente recibido. Sin configuración de remitente o sin prueba de llegada, el cierre de correo permanece pendiente aunque PDF esté listo.

## Decisiones que mantienen esto pequeño

- Tres fases; un contrato y un formato. Sin editor de plantillas, panel de correos, envíos masivos, adjuntos arbitrarios, colas offline de email, cron ni microservicios.
- Sin tabla nueva de comprobantes; solo registro operacional de correo. Los seeders solo verifican esquema y coherencia, no mandan mensajes.
- Sin nueva integración PayPal ni Firebase: se lee su resultado confirmado. Sin retocar el módulo de notificaciones.
- Un remitente SMTP para toda la plataforma. Las cinco cuentas aportadas son destinatarios, no hace falta iniciar sesión en ellas.

Autorrevisión: cobertura ventas/reservas/wallet/PayPal/offline/cambio; autorización por usuario/empresa/sucursal; una sola fuente monetaria; idempotencia de correo y estados inciertos; fallos independientes; publicación/QA real cubiertos por F1–F3. Solo documentos creados en esta planificación; nada implementado ni enviado todavía.
