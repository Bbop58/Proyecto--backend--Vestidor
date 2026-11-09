# Comprobante general PDF y correo

Fecha: 9/11/2026. Estado: especificación, sin implementación.

## Resultado solicitado

Una plantilla sencilla y coherente para web y Flutter, con dos acciones manuales: **Descargar PDF** y **Enviar al correo**. Reutilizar las operaciones ya registradas; generar o enviar un documento nunca cobra, reserva, descuenta stock ni acredita billetera.

## Operaciones y significado

| Origen | Documento y estado |
|---|---|
| Venta presencial o compra digital COMPLETADA | Nota de venta; pagada con el método real: efectivo, PayPal, billetera, tarjeta o QR ya admitido por el sistema |
| Venta PENDIENTE_PAGO | Nota de operación pendiente; nunca «pagado» |
| Venta cancelada | Nota marcada CANCELADA, sin ocultar su estado |
| Reserva | Constancia de reserva con importe estimado, vencimiento y estado actual; «No acredita pago» |
| Recarga confirmada en billetera, PayPal o efectivo | Comprobante de recarga por el importe efectivamente acreditado |
| Pago con billetera | La nota de la venta; no otra venta/documento duplicado por el débito |
| Compra/venta/reserva offline todavía sin confirmar | Comprobante PROVISIONAL con UUID local y «Pendiente de sincronización»; no número central inventado |
| Offline conciliado | Nota central del mismo resultado, con recibo definitivo; el PDF provisional anterior no cambia retroactivamente |
| Operación rechazada o en conflicto | Mostrar el estado real; no comprobante de pago confirmado ni regenerar la operación |

Cambios y devoluciones que ya tienen nota reutilizan la misma plantilla, indicando prendas devueltas/entregadas y diferencia real, sin interpretarla como nueva compra completa. No incluir suscripciones SaaS ni convertir esto en facturación fiscal.

## Plantilla única

Una hoja A4 para casos normales; más páginas solo si hay muchas prendas. Empresa/sucursal reales, título según origen, número existente o UUID provisional, fecha Bolivia, estado destacado, cliente si existe, detalle de prendas/talla/color/cantidad/precio/subtotal, descuento/envío/total ya registrados y método/referencia de pago. En efectivo mostrar recibido/cambio cuando consten. Recarga: una línea por recarga; no exponer saldo completo de la billetera, firmas, nonce, tokens ni secretos.

Nota comercial sencilla, no factura fiscal. No deducir IVA 13%, no sumar impuestos nuevos, no inventar NIT, dirección, cliente ni referencia PayPal. Si un dato no existe, omitirlo o indicar «No registrado». Los importes se toman de la transacción guardada con Decimal/centavos; no de precios actuales del catálogo ni de valores enviados por la UI. Respetar nombres con acentos, textos largos y paginación. Fuente Unicode local con licencia; sin imágenes/QR ni peticiones externas al renderizar.

Mismo contrato `ReceiptDocument(version=1)` y mismo diseño. Backend renderiza PDF canónico (descarga web/móvil y adjunto). Los únicos renderizadores locales son para provisionales offline web/Flutter, con las mismas etiquetas y campos, usando instantáneas existentes. No imponer una librería idéntica entre Python/Dart/TypeScript ni mantener plantillas comerciales distintas.

## Acceso y datos

Cliente solo sus propias operaciones en su empresa; personal con permiso actual de ventas/reservas/billetera y alcance de sucursal vigente; superadmin sin acceso comercial. Comprobar autorización ANTES de cargar detalles/renderizar/enviar. Documento ajeno responde 404. Ninguna URL pública permanente ni PDF almacenado en el bucket para esta versión.

Reutilizar `ventas`, `reservas`, `wallet_transactions`, cambios, sus detalles y UUID/recibo de sincronización. No otra tabla de comprobantes ni numerador. Una tabla operacional pequeña `receipt_email_deliveries` solo para controlar envíos y repeticiones, excluida del backup comercial. Migración aditiva repetible; seeder no genera PDFs/correos ni credenciales ni filas de envío.

## Correo simple

Envío manual de un documento por petición; modal con un destinatario y confirmación. Predeterminar correo del cliente si existe; permitir corregirlo para ventas de mostrador. Mostrar destinatario completo antes de enviar. El servidor obtiene el documento autorizado por tipo/id; nunca acepta PDF/importe/empresa arbitrarios del navegador.

Un transporte SMTP autenticado compartido para el backend, con TLS, remitente propio autorizado y nombre visible FashionStore · empresa. No suplantar correos de las empresas. Gmail puede usarse con contraseña de aplicación; OAuth del complemento Gmail NO sustituye el correo saliente del software. No pedir contraseña normal ni poner claves en Git. Configuración privada posterior: `RECEIPT_EMAIL_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`; usar 587/STARTTLS o 465/TLS explícito. Sin configuración: PDF funciona, enviar muestra «Correo no configurado».

Registro de envío con usuario/empresa/origen/id/destinatario/huellaPDF/UUID petición/estado/fecha. Misma clave de operación devuelve el resultado previo; doble clic no envía dos veces. Límite servidor 5 intentos por usuario en 10 minutos y 60 segundos entre envíos del mismo documento/destinatario. Enviar por hilo con timeout 15 s; no crear worker/cron/servicio nuevo. Estados PENDING, ACCEPTED, FAILED, UNKNOWN: aceptación SMTP no garantiza recepción final. Si hay timeout/crash tras posible aceptación, registrar UNKNOWN y no reintentar automáticamente; permitir reenvío manual explícito tras el límite. Si falla correo, la compra/recarga/reserva permanece intacta.

Sin Internet: descargar provisional local; botón correo deshabilitado con explicación. No cola offline adicional de correos. Al sincronizar, usuario descarga/envía la nota central manualmente. Provisionales exclusivamente locales no se envían desde backend como confirmados.

## Pruebas reales autorizadas

Destinatarios de prueba aportados por el usuario: browser78965@gmail.com, dylancossioaguilera@gmail.com, dylan10g4briel20cossioaguilera@gmail.com, puchisin7@gmail.com y doncojon9@gmail.com. No son credenciales ni se incluyen en seed/código de producción. Usar solo documentos sintéticos para estas pruebas. Enviar uno por dirección, con tipo/empresa identificable; usuario confirma recepción y adjunto legible. No repetir cinco veces el mismo envío para «asegurar» entrega. Los correos destino no configuran por sí solos el remitente SMTP.

## Criterio de cierre

PDF y correo utilizan el mismo documento, correctamente autorizado, para efectivo/PayPal/billetera/reserva/recarga/cambio y provisional offline. No duplican operaciones ni correo por doble clic, funcionan en ambas empresas y conservan estado/centavos. Pruebas PDF con extracción de texto y páginas renderizadas; descarga real web/Android; cinco entregas reales confirmadas por el usuario. Sin remitente SMTP o sin aceptación real, se documenta pendiente; no fingir envío con mailto ni confundir aceptación del proveedor con llegada al buzón.
