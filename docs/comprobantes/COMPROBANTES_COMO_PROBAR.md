# Cómo probar los comprobantes

Web: Perfil → Mis comprobantes. Cada compra, reserva y recarga confirmada ofrece Descargar PDF y Enviar al correo. El personal también encuentra estas acciones al abrir una venta, reserva, cambio o la última recarga de mostrador.

Móvil: Mis compras → abrir comprobante; Mis reservas → tocar la reserva; Mi billetera → historial de recargas; caja móvil y Sincronización Offline → operación local. Una compra con PayPal o billetera usa su nota de venta, no otro cobro.

**PDF:** abre Guardar/Compartir de Android para elegir dónde conservar el archivo. En web se descarga directamente. La nota es comercial, no factura fiscal. Reserva indica NO ACREDITA PAGO; offline pendiente indica PROVISIONAL y conserva el UUID local. Al sincronizar se descarga el comprobante central, sin crear otra operación.

**Correo:** pulsa Enviar al correo, revisa o cambia el destinatario y confirma. Sin conexión o con provisional, no se habilita envío ni se guarda cola de correos. Si se pierde la respuesta, vuelve a intentar con el mismo destinatario para conservar el UUID y evitar duplicados. «Aceptado para envío» significa que SMTP lo aceptó; revisa Recibidos/Spam para confirmar llegada.

Esta guía describe la implementación. La aceptación del despliegue y APK se registra separadamente en el cierre F3; no supone que ya esté publicada.
