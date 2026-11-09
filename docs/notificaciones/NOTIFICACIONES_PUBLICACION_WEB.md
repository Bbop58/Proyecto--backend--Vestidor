# Candidato publicado web — ajuste de ejecución autorizado

9/11/2026. El usuario solicitó push y prueba en el dominio real tras el bloqueo del control de navegador en about:blank. Esta instrucción modifica el orden del plan C1–C4: publicar candidato **backend/web** comprobado para aceptar HTTPS/WSS/FCM reales; conservar Android abierto y sin APK definitiva.

No desactivar seguridad del navegador publicado ni usar certificado local para certificar Vercel. El arnés --remote --system-tap mantiene la URL real y minimiza su propio navegador; elimina el about:blank durante el control de UI. Si el control vuelve a rechazar, detener ese control y registrar qué faltó, sin eludir el rechazo.

## Pasos

- [x] Confirmar remotos main/base y preferencia Git D-blip-max.
- [x] Respaldo privado de ambas empresas fuera de Git antes del reset autorizado.
- [x] Variables Firebase backend establecidas sin redeploy; no imprimir Admin JSON.
- [x] Web completa final 220 pruebas PASS, incluida corrección de respuesta tardía contra asociación nueva.
- [x] Backend completa final 826 PASS/0 FAIL/0 SKIP (1121,01 s), build web hacia Railway aprobado. Avisos existentes de on_event/CommonJS y presupuesto inicial 647,25 kB; no se ocultaron ampliando presupuesto.
- [ ] Commit selectivo backend/web y push con D-blip-max; móvil no se publica como terminado.
- [ ] Verificar despliegues, health, seed y rutas nuevas.
- [ ] Crear fixtures tras reset; probar WS/FCM/clic reales ambas empresas en Vercel/Railway.
- [ ] Guardar commits/despliegues/resultados y límites, cerrar servicios/sesiones QA propios.

## Android sigue abierto

El recorrido nativo confirmó sesión y actualización WS antes de habilitar FCM. El registro FCM no se completó: primero mensaje genérico y luego timeout del arnés. Se añadió flag requerido firebase_messaging_installation_id_enabled=true verificado en manifest fusionado; la repetición todavía no alcanzó aceptación de registro. No atribuir toda la falla a System UI ANR ni afirmar recepción Android. Próximo diagnóstico debe distinguir request initialize/register, stream y timeout, con códigos/etapas sanitizados.

Los scripts Android de aceptación nuevos son experimentales hasta ejecutarse. No generar ni entregar APK final de notificaciones ni cerrar C2/C3/C4 globales por aprobar solo web.
