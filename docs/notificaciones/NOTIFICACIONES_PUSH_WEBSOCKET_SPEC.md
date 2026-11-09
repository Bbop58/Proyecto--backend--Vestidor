# Notificaciones push y WebSocket — especificación de cierre

9/11/2026, Bolivia. Raíz vigente: `C:/Users/Laptop/FashionStore-recuperado`.

## Resultado esperado

Cliente UrbanStyle/ModaExpress en web y Android: con app abierta ve su historial actualizarse mediante WebSocket; en segundo plano recibe un aviso FCM del sistema. El clic abre Notificaciones con sesión válida. Demostración principal: cliente reserva y encargado prepara. No efectuar cobros para probar el transporte.

Historial PostgreSQL, FastAPI actual, Angular y Flutter; sin Redis ni otro servicio Railway. El monitor WS consulta cada segundo. El trabajador push reclama filas con `FOR UPDATE SKIP LOCKED`; no existe un lease independiente.

## Contratos obligatorios

- `WS /api/v1/notifications/ws`: primer frame `{token}` antes de 5 segundos, sin JWT en URL. `notifications.changed` con revision/latest_id/unread_count; detalles por REST propio. Validar origen, identidad, caducidad/revocación y cierres 4401/4403.
- `PUT /api/v1/notifications/push/devices`: usuario vigente, instalación+secreto, token/FID, plataforma y refresh web cuando corresponde. No devolver secretos.
- `POST /api/v1/notifications/push/unregister`: capacidad installation_id+installation_secret+binding_id, sin exigir JWT vigente para revocación durable tras logout offline. Respuesta uniforme; una revocación antigua no desactiva una asociación nueva.
- Dispositivos/entregas son estado operativo excluido del backup comercial. Restore revoca asociaciones y sesiones de la empresa; no importa receptores antiguos.
- Cola única por notificación/dispositivo, reintentos limitados y receptor inválido desactivado. Caída después de aceptación FCM y antes del commit puede causar reenvío: ID/etiqueta estable. No prometer entrega exactamente una vez ni recepción garantizada.
- Ninguna llamada Firebase dentro de la transacción comercial. Fallar entrega no repite ni revierte venta/reserva.
- Primer plano: refrescar sin otro aviso local del evento. Segundo plano: aviso genérico sin importes/nombres/datos privados; detalles autenticados.
- Logout, cambio de cuenta/empresa, contraseña, suspensión y revocación impiden futuras entregas no autorizadas y callbacks privados tardíos. Retirada offline durable y reintentada al reconectar.
- Permiso y conectividad necesarios. Android force-stop no equivale a segundo plano: recuperar historial al reabrir, sin garantizar recepción mientras el SO bloquee. No garantizar retirar avisos ya entregados en otro dispositivo.
- Firebase fashionstore-14410; Android com.si2proyecto.mobile. Admin JSON solo privado en backend; configuración cliente/VAPID públicas.
- Mantener PWA, sesión persistente, SQLite, colas comerciales, billetera y avisos locales de conectividad. No certificar WS mediante polling ni push real mediante mocks.

## Aceptación

Ambas empresas pasan recorrido local y publicado. Android demuestra recepción visible FCM y apertura por tap. Casos negativos prueban aislamiento/logout/recuperación. Publicación identificada por commit/despliegue; APK por versión/API/SHA256. Cualquier pendiente obligatorio impide declarar cierre completo; límites de proveedor/AVD/teléfono físico quedan explícitos.
