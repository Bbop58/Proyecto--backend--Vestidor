# Comprobantes F3 — implementación local, 9/11/2026

Pendiente de publicación y aceptación real. No declarar correo recibido ni PDF guardado en teléfono físico por pruebas unitarias.

Implementado: contrato y acciones compartidas web/Flutter; PDF central binario autorizado y mismo adjunto SMTP; mail manual con confirmación y UUID estable; descarte por cambio de cuenta; provisional offline con fuente local y sin correo en cola. Integrados ventas POS/listado, reservas/listado, cambios, recarga mostrador; historial propio web nuevo en `/mis-comprobantes` enlazado desde Perfil. Flutter: historial compras, detalle reserva, PayPal, billetera, caja y sincronización. Recarga y débito no se confunden.

SQLite efectivo conserva etiquetas descriptivas en items_json sin migración y sin enviarlas al servidor; comparación de replay usa solo campos comerciales. Wallet SQLite legacy conserva número de recibo: resolución autorizada por empresa y propietario antes del PDF central. No buscar en una lista truncada de 100 compras.

Resultados locales actuales:

- Backend: 55 PASS (documentos/PDF/correo y fase3 comercial); luego se añadió lectura wallet/history sin crear billetera, pendiente verificación dirigida adicional.
- Web: 227 PASS en 46 archivos; build PASS. Chrome bundle producción con API doble, 1440/390 px: 3 descargas idénticas byte a byte por pantalla, confirmación de correo explícita, cero errores JS ni overflow. Imágenes revisadas. No se enviaron mensajes en esos recorridos.
- Flutter: 155 PASS suite completa; análisis sin observaciones. PDF provisional offline con fuente empaquetada, binario con renovación401/host ajeno/cambio de cuenta; widget SMTP simulado/retry UUID/320px. APK release 1.0.4+5 en compilación.
- Railway: configuración SMTP privada aplicada con skipDeploys=true; ningún secreto en repositorios. Backend todavía no publicado con recibos. Prueba local Gmail autentica TLS, no acredita entrega desde Railway.

Falta: terminar APK, prueba adicional historial vacío, publicación conjunta D-blip-max tras checks; aceptar health/seed/Vercel; cinco correos sintéticos y replay, snapshots antes/después, confirmación de llegada por usuario, entregar APK y guía, cierre definitivo. No preparar otro AVD ni repetir pruebas de notificaciones.
