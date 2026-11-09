# Comprobantes — F1 terminada localmente

9/11/2026. Raíz real: `C:/Users/Laptop/FashionStore-recuperado`. Solo backend; sin commit/push/deploy, cambios remotos, nuevo seeder, migración, correo ni cambios de UI/APK.

## Resultado

Contrato común `ReceiptDocument` v1 y plantilla A4 Unicode para nota de venta, constancia de reserva, recarga de billetera y cambio/devolución. Dos rutas autenticadas: `GET /api/v1/receipts/{kind}/{id}` y `GET .../pdf`, kinds sale/reservation/wallet_topup/exchange. Descarga privada sin caché; nombre de archivo sanitizado.

Se leen importes históricos con Decimal y se entregan como strings de dos decimales. No se usan precios actuales ni se deduce IVA. La plantilla nueva no inventa NIT, domicilio ni referencia de pago. El formato legacy se conserva sin cambios para no alterar consumidores existentes.

Reserva siempre «NO ACREDITA PAGO»; venta pendiente/cancelada nunca PAGADO; recarga solamente si es un crédito conciliado de tipo admitido. El débito de una compra wallet no emite otra recarga. El cambio refleja la diferencia real y las prendas devueltas/entregadas. Recibido/cambio en efectivo solo se incluyen para EFECTIVO. Fechas Bolivia, tablas multipágina, texto escapado y rechazo explícito de glifos no admitidos por la fuente.

Cliente accede solo a sus operaciones; personal utiliza los permisos actuales reservas.list/ventas.list dentro de la empresa. Superadmin rechazado antes de consultar datos comerciales. Se verifica la empresa de cabecera, sucursal y producto relacionado. El modelo actual de User no tiene asignación de sucursal: no se inventó una nueva restricción por empleado. Las etiquetas de producto se obtienen del catálogo relacionado porque las tablas históricas no guardan copia de nombres; los precios y cantidades sí vienen del detalle histórico.

## Pruebas y evidencia

- Primera ejecución RED: import del servicio inexistente; reproducido antes de implementar.
- Primera dirigida: 31 PASS antes de añadir pruebas adicionales de relaciones/acceso.
- Regresión final de cinco archivos: 108 PASS/1 fallo en un test nuevo de corrupción de relación. El test conservaba en identidad ORM la variante anterior al modificar la FK; se corrigió expirando esa relación. Otra prueba anterior se corrigió conservando el UUID antes de expirar la entidad para no medir sus propias consultas ORM.
- Dirigida definitiva sobre fuentes actuales: **34 PASS**, cero skips, 12,71 s; seis avisos FastAPI existentes. Log privado `%TEMP%/receipts-f1-directed-accepted.log`.
- Los **75 casos anteriores** de test_tenant_sales, test_exchanges y test_payment_invariants aprobaron en la regresión de cinco archivos. No afirmar una única corrida completa de 109 verdes: los 34 nuevos quedaron aprobados en la dirigida posterior.
- HTTP real con TestClient/API y PostgreSQL local exclusivo: autenticación, JSON/PDF, cabeceras y 404 de otra empresa. Lectura repetida sin INSERT/UPDATE/DELETE. No conexiones a PayPal ni correos.
- Tres muestras PDF: venta y reserva de una página; 90 prendas en seis páginas. Extracción de texto: todas las filas una vez, números/totales/acentos/estado. Renderizadas páginas inicial/final; revisión visual sin cortes. Verificación geométrica: 535 spans dentro de página/márgenes. Muestras privadas en `%TEMP%/fashionstore-receipts-f1`.
- Misma entrada genera bytes idénticos, reutilizables como descarga y adjunto.
- Compilación sintáctica y git diff --check aprobadas.

## Dependencias

ReportLab 5.1.0 añadido a requirements.txt (Python >=3.9,<4, compatible con Python3.11 del despliegue). pypdf7 de validación en requirements-dev.txt. Fuente NotoSans regular y licencia OFL copiadas de los assets locales ya existentes del proyecto; sin red al generar PDF. PyMuPDF instalado solo en entorno QA para renderizado, no en requirements de producción.

## Continuación

F2 pendiente: envío manual SMTP/idempotencia y configuración privada de remitente. Los cinco correos del usuario son destinatarios autorizados, no credenciales ni remitente configurado. Ningún correo enviado.

F3 pendiente: conectar Descargar PDF/Enviar al correo en web y Flutter, provisionales offline reales desde SQLite/snapshots y descarga/guardado nativo. El renderizador admite la etiqueta provisional y está probado, pero todavía no está integrado a esas operaciones offline.

No publicar F1 aisladamente: el plan prevé publicación conjunta al acabar integración. No afirmar que Railway/Vercel/APK incluyen estos cambios. Backend local sobre d81f6e8; web/móvil publicados intactos. La base disposable fashionstore_test_receipts_f1 fue la única utilizada por pytest; no se tocó la base live QA ni remota. PostgreSQL QA propio se detiene al cierre.
