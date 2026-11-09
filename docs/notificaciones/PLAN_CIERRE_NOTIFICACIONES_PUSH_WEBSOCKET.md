# Notificaciones push y WebSocket — plan de cierre

> **Cambio autorizado posterior:** seguir NOTIFICACIONES_PUBLICACION_WEB.md para publicar/aceptar primero el candidato backend/web en HTTPS real. Android y el cierre global quedan pendientes; este cambio de orden sustituye la espera de publicación conjunta para este candidato.

> Ejecución nativa por tareas y checkboxes, sin subagentes. Aplicar writing-plans y systematic-debugging ante fallos; angular-developer para cambios Angular. No depender de skills ausentes. Este turno solo prepara el plan; se conserva la autorización previa de pruebas y publicación para ejecutarlo.

**Objetivo:** terminar, probar y publicar WebSocket y push reales en web/Android, con APK final y demostración sencilla.

**Arquitectura:** historial propio PostgreSQL, WebSocket autenticado y recuperación REST. Cola durable FCM y asociaciones instalación/usuario/empresa dentro del backend actual; se completa lo existente sin agregar servicios.

**Stack:** FastAPI/SQLAlchemy/PostgreSQL, Firebase Admin; Angular 21/Firebase Messaging; Flutter/Firebase Messaging Android.

**Especificación:** [NOTIFICACIONES_PUSH_WEBSOCKET_SPEC.md](NOTIFICACIONES_PUSH_WEBSOCKET_SPEC.md).

## Estado real

Raíz: `C:/Users/Laptop/FashionStore-recuperado`. Repos independientes backend/web/mobile; raíz sin Git. Leer `docs/auditoria_software/NOTIFICACIONES_N2_AVANCE_LOCAL.md`.

| Componente | Evidencia disponible | Falta |
|---|---|---|
| WebSocket | API/PG local ambas empresas e historial 0→1→2→0 | Android y aceptación publicada |
| Push web | FCM real Chrome ambas empresas, primer/segundo plano | Clic real y repetición publicada |
| Push Android | SDK/canal/lifecycle y pruebas con dobles | Recepción visible, tap y recuperación real |
| Backend | Completa anterior 814 PASS/4 FAIL; repetición de cinco archivos afectados 112 PASS tras corregir | Completa final y migración poblada |
| Angular | 216 pruebas/43 archivos PASS; bundle QA | Build producción y Vercel |
| Flutter | 147 PASS antes del último ajuste de timeout; analyze final limpio | Validación final, APK release y AVD |
| Publicación | Ampliación sin commit/push/env remoto | Publicación conjunta y cierre |

C1–C4 completan la ampliación original N1/N2/N3. No empezar otro módulo ni repetir partes aprobadas sin motivo.

## Restricciones globales

- Respetar todos los contratos y límites de la especificación. Push genérica, detalle autenticado; no repetir operaciones comerciales.
- GitHub D-blip-max en tres repos, sin cambiar autoría. Preservar generados desktop previamente modificados; no usar git add . indiscriminadamente.
- Admin JSON fuera de Git/salidas. Ya se recibió: no pedirlo otra vez. No modificar PayPal/Gemini/billetera.
- Predeploy backend reconstruye demo: fixtures nuevos después del último reset; congelar despliegues durante aceptación.
- Bases QA test/live separadas y UTF8. Nunca pytest destructivo contra live ni remoto.
- Terminado significa criterios aprobados y evidencia, no promesa de cero bugs universales ni recepción garantizada por Firebase/Android.

## Foco de revisión

| Riesgo | Resultado exigido | Fase |
|---|---|---|
| Refresh/FID rota mientras el canal está abierto | Reasociación sin bucles, callbacks viejos descartados | C1/C2 |
| Logout offline A y login B en misma instalación | Revocación durable A nunca desactiva B ni abre sus detalles | C1/C2 |
| Proceso cae tras aceptación FCM antes de SENT | Reintento con etiqueta estable y ninguna venta repetida | C1 |
| Permiso revocado o Wi-Fi sin backend/FCM | Sin falsa activación; recuperación sin repetir operación | C2/C3 |
| Tap antiguo, arranque frío y worker PWA coexistente | Ruta autenticada correcta, sin datos ajenos ni romper offline | C2/C4 |

## C1 — Cerrar contratos y puntos pendientes

**Archivos:** backend/app/services/notification_push_service.py; app/core/tenant_scope.py; app/core/notification_push_ddl.py; app/main.py; app/services/backup_recovery.py; web/src/app/core/services/notification-push.service.ts; mobile/lib/services/notification_push_service.dart.

**Tests:** backend/tests/test_notification_push.py, test_notifications_realtime.py, test_tenant_contract.py, test_saas_entitlements.py, test_saas_platform_boundary.py; specs push/realtime web y móvil existentes.

**Interfaces:** conservar register(db,user,payload,access_payload), unregister(db,payload), deliver_pending(factory,send,limit=20) y contratos de red de la spec. Las fases siguientes consumen esas interfaces, sin rutas paralelas.

- [x] Inventariar diff, commits base y archivos previos; confirmar infraestructura QA antes de reutilizarla.
- [x] Añadir pruebas faltantes: rollback comercial no persiste entrega; usuario/empresa suspendidos no reciben pendientes; instalación manipulada se rechaza; revocación vieja no desactiva binding nuevo; caída tras aceptación conserva ID estable. Reproducir antes de corregir cada defecto.
- [x] Probar refresh y rotación FID sin logout. Revisar Angular: actualmente retira onRegistered tras registro inicial; garantizar actualización ante rotación posterior, sin bucle. Cambiar solo la carencia demostrada por la prueba.
- [x] Revisar expiración/revocación al entregar, además de al registrar: asociación web/móvil no debe seguir elegible indefinidamente por omitir caducidad. Añadir prueba temporal independiente del reloj real.
- [x] Ejecutar migración aditiva dos veces en copia QA poblada; comparar filas comerciales, conservar datos y dejar inelegibles receptores antiguos sin hash válido. Probar backup/restore local sin receptores/secretos y con asociaciones revocadas.
- [x] Desde backend con TEST_DATABASE_URL exclusiva, ejecutar `.venv/Scripts/python.exe -m pytest tests/test_notification_push.py tests/test_notifications_realtime.py tests/test_tenant_contract.py tests/test_saas_entitlements.py tests/test_saas_platform_boundary.py -q --tb=short`. Todos PASS, sin skips por DB ausente.
- [x] Escribir docs/auditoria_software/NOTIFICACIONES_C1_CIERRE.md: hallazgos, correcciones, pruebas y pendientes.

**Salida:** aislamiento, revocación, migración y reintentos aprobados; sin afirmar aún recepción Android/remota.

## C2 — Probar Android real y completar recorridos cliente

**Archivos:** crear mobile/integration_test/notifications_journey_test.dart; extender web/e2e/notifications-live.mjs y backend/scripts/verify_notifications_live.py. Servicios push/realtime/controles existentes se corrigen solo ante reproducción.

**Interfaces:** arnés con cuentas sintéticas por empresa y APIs existentes; resultados por empresa/etapa/evento, sin FID ni tokens. Credenciales QA fuera de Git.

- [ ] API+PG propios, fixtures cliente y encargado/admin autorizado de ambas empresas; AVD propio con Google Play Services, sin tocar teléfono/AVD ajenos.
- [ ] Android abierto: reservar y preparar desde otra sesión; comprobar socket/evento y actualización sin recarga, distinguiendo polling. Cortar/reconectar red y renovar JWT: un canal activo, historial recuperado.
- [ ] Activar push con permiso; enviar evento nuevo con app en segundo plano. Capturar aviso del sistema y su ID; tocarlo realmente y verificar historial propio autenticado.
- [ ] Repetir con proceso terminado normalmente y tras reboot cuando Android permita recepción. Separar force-stop: no exigir push mientras el SO bloquea, sí recuperar historial al reabrir.
- [ ] Negativos: permiso denegado/revocado y reactivación; logout offline/reconexión; cuenta A→B; tap antiguo; backend inaccesible. Sin datos de A bajo B, pérdida SQLite ni operaciones comerciales duplicadas.
- [ ] Web: añadir clic real de notificación al arnés; goto manual no certifica tap. Verificar PWA root worker coexistente con FCM, permiso bloqueado y reconexión. Dos eventos diferentes pueden generar dos avisos: comprobar ID/etiqueta, no asumir un aviso total.
- [ ] Guardar NOTIFICACIONES_C2_CIERRE.md y NOTIFICACIONES_ACEPTACION.json con evidencias. Si AVD/proveedor falla, aislar causa; no sustituir recepción real por mocks ni marcar cierre completo.

**Salida:** WS/push demostrables en Android/web, ambas empresas, con apertura y separación de cuentas.

## C3 — Regresión y candidato publicable

**Archivos:** tests anteriores, mobile/pubspec.yaml y build Android, backend/requirements.txt, web/package.json/lock y cierres. Sin dependencias nuevas salvo necesidad probada.

**Interfaces:** fuentes definitivas y NOTIFICACIONES_ARTEFACTOS.json con hashes, comandos/resultados, versión/API de APK; sin secretos.

- [ ] Sobre fuentes finales: backend `.venv/Scripts/python.exe -m pytest -q --tb=short` con base test; web `npx ng test --watch=false`; móvil `flutter test` y `flutter analyze`. Registrar fallos y repeticiones sin sumar corridas como completa verde.
- [ ] `npm run build` hacia Railway; verificar que no queda API localhost. Revisar controles en 390px y texto grande Android sin overflow.
- [ ] Regresión focal: login/persistencia, logout offline, PWA offline, reserva/cancelación y billetera/cola preservadas. Comparar snapshots en recorridos que no deban escribir.
- [ ] Revisar diff/dependencias/logs y separación entre Firebase público/Admin privado; no incluir generados desktop previos ni secretos.
- [ ] Preparar release Android 1.0.4+5 si sigue libre; si cambió lo publicado, elegir siguiente versionCode sin rebajarlo. Construir contra Railway e instalar actualización conservando datos QA.
- [ ] Guardar NOTIFICACIONES_C3_CIERRE.md y artefactos; copiar plan/spec/cierres pertinentes a backend/docs/notificaciones, web/docs/notificaciones y mobile/docs/notificaciones para sobrevivir fuera de la raíz sin Git.

**Salida:** candidatos locales listos; no afirmar que lo publicado ya contiene cambios.

## C4 — Publicar, aceptar y entregar

**Servicios/archivos:** tres repos, variables backend Railway, Vercel y APK; NOTIFICACIONES_C4_CIERRE.md y docs/notificaciones/COMO_PROBAR.md por repo.

**Interfaces:** endpoints actuales, API Railway HTTPS/WSS, revisión desplegada identificable y APK con API/versionCode correctos.

- [ ] Verificar D-blip-max/acceso/remotos/diff; commits selectivos y push autorizados. Si falta acceso, informar antes de elegir otra cuenta.
- [ ] Backend Railway: NOTIFICATION_PUSH_ENABLED=true, FIREBASE_PROJECT_ID=fashionstore-14410, FIREBASE_ADMIN_JSON desde archivo privado y NOTIFICATION_WEB_URL=https://proyecto-frontend-vestidor-ten.vercel.app/dashboard?notifications=1. No imprimir valores privados ni copiarlos al worker de backups.
- [ ] Coordinar configuración/push minimizando redespliegues; revisar predeploy/migración/seed, logs y /health/ready. No confundir health o socket con recepción FCM.
- [ ] Esperar Vercel/Railway correctos; crear fixtures tras último reset y congelar despliegues. Ejecutar verificador API remoto con host permitido y `node e2e/notifications-live.mjs --remote`; repetir Android contra Railway en ambas empresas.
- [ ] Revisar entrega/evento, clic, logout/cambio de cuenta y cola sin exponer receptores; ninguna compra/cobro duplicada. No usar datos viejos de una DB reseteada.
- [ ] Verificar APK final con fuentes/API correctas e instalar en AVD. Copiar a Downloads como FashionStore-Notificaciones-1.0.4+5.apk (o versión vigente elegida), calcular SHA256 y entregar enlace absoluto.
- [ ] Manual breve: app abierta WS; app en segundo plano push; cliente y acción de encargado que dispara evento. Aclarar permiso/Internet/force-stop.
- [ ] Publicar cierres/matriz/evidencias finales. Si push documental provoca reset, repetir salud y recorrido mínimo con fixtures nuevos antes de cerrar.
- [ ] Revocar sesiones QA y cerrar Chrome/AVD/API/PG propios. Retirar temporales sensibles de prueba, conservando Admin privado operativo y archivos del usuario.

**Contingencia:** si FCM causa incidente, NOTIFICATION_PUSH_ENABLED=false pausa worker; conservar historial/cola. Modo degradado no cuenta como cierre completo. No borrar/restaurar DB por un fallo de transporte.

## Criterios de terminado

- [ ] C1–C4 cerradas con evidencia y pruebas finales pertinentes aprobadas.
- [ ] WS y push reales en web/Android y ambas empresas; tap y aislamiento de cuentas comprobados.
- [ ] Backend/web/mobile publicados; commits/despliegues identificados, sin cambios funcionales olvidados.
- [ ] APK descargable, versión/API/SHA256 y manual listos.
- [ ] Límites AVD/teléfono físico/proveedor explícitos y cero pendientes obligatorios disfrazados de aprobación.

Autorrevisión: contratos, revocación offline, transacciones, aislamiento, migración/backup, PWA, Android, pruebas reales y publicación cubiertos. El plan actualiza las referencias obsoletas DELETE/lease del original, sin borrar evidencia histórica. Este turno no ejecuta fases, tests, commits, push, cambios remotos ni nuevas builds.
