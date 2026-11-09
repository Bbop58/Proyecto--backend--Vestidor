# Cómo demostrar notificaciones en la web

Web: https://proyecto-frontend-vestidor-ten.vercel.app/

## WebSocket: aplicación abierta

1. Iniciar sesión como cliente de UrbanStyle o ModaExpress y abrir la campana.
2. Comprobar el indicador **En vivo**.
3. Registrar una reserva. Desde otra sesión de personal autorizado de esa empresa, preparar la reserva.
4. El historial del cliente debe actualizarse sin recargar la página. El evento queda almacenado aunque se cierre la ventana.

## Push: aviso del sistema

1. En la campana, elegir **Activar avisos del sistema** y permitir notificaciones en el navegador.
2. Esperar **Avisos del sistema activados**. No cerrar sesión.
3. Minimizar el navegador. Preparar una nueva reserva desde la otra sesión.
4. Debe llegar un aviso genérico FashionStore. Tocarlo abre las notificaciones del usuario autenticado.

Registrar y preparar son eventos diferentes y pueden generar dos avisos. La comprobación de duplicados se realiza por identificador del evento, no contando todos los avisos como uno.

## Límites y privacidad

- Push requiere permiso y conectividad; no garantiza llegada instantánea si el navegador o el sistema operativo lo restringen.
- En primer plano se actualiza el historial sin crear otro aviso local del mismo evento.
- No se muestran importes, nombres de clientes ni detalles comerciales en el aviso del sistema. Los detalles requieren sesión y empresa correctas.
- Al cerrar sesión se retira la asociación. Si el cierre fue offline, la revocación pendiente se intenta al reconectar; los detalles siguen protegidos por autenticación.
- Esta entrega candidata corresponde a backend y web. La recepción push y el tap en Android aún no están aceptados; no utilizar este manual como certificación de la APK.
