# C1 — Contratos y seguridad de notificaciones cerrados localmente

9/11/2026, Bolivia. Raíz `C:/Users/Laptop/FashionStore-recuperado`. Implementación sin publicar: C2–C4 siguen pendientes.

## Hallazgos y correcciones

1. **Caducidad web omitida al enviar.** El registro validaba refresh al crearse y su blacklist al entregar, pero no conservaba su vencimiento. Reproducción: avanzar reloj más allá de exp todavía invocaba al proveedor. Se agregó `session_expires_at`, capturada del refresh firmado; una entrega web sin fecha válida o vencida se omite. La migración agrega la columna de forma aditiva. La sesión móvil persistente mantiene su contrato revocable, sin convertirla en sesión web de siete días.
2. **Rotación de registro Firebase web no observada.** El adaptador eliminaba el observador tras obtener el primer receptor. Reproducciones independientes del adaptador SDK y del servicio demostraron ausencia de actualización. Ahora conserva observador de registro mientras escucha mensajes, ignora el mismo identificador, vuelve a registrar al cambiar y cancela ambos observadores al salir. Guardas de propietario/generación descartan callbacks anteriores. Refresh de sesión también renueva asociación.

## Verificación

| Prueba | Resultado |
|---|---|
| Reproducción backend inicial | 1 FAIL caducidad /15 PASS |
| Reproducción web servicio | 1 FAIL rotación /4 PASS |
| Reproducción web adaptador SDK | 1 FAIL rotación |
| Backend final, cinco archivos del plan | **120 PASS**, 0 FAIL/0 SKIP; 64,27 s |
| Web final, dos archivos push | **6 PASS**, 0 FAIL |
| Angular producción hacia Railway | **Build PASS**; 647,25 kB inicial, aviso presupuesto 500 kB y CommonJS |
| Migración aditiva repetida | **2 ejecuciones**, **42 tablas ajenas a dispositivos/entregas idénticas** por conteos y SHA256 de filas |

Incluye suspensión de usuario/empresa, contraseña, revocación web y móvil, instalación manipulada, logout viejo frente a nueva asociación, cambio de empresa, dos workers y registro concurrente, rollback sin entrega, backup sin receptores/secretos y restore con revocación. Reintentos transitorios terminan tras seis intentos.

La caída simulada después de aceptación FCM demuestra la semántica real: puede reenviar el mismo ID antes de persistir SENT; no crea otra notificación ni operación comercial. No afirmar entrega exactamente una vez. Firebase fue simulado en estas pruebas; la recepción web real de la fase anterior no se reejecutó en C1.

La prueba móvil de contrato confirma entrega con sesión persistente vigente incluso a 30 días y bloqueo por revocación. Es API/PG con doble del proveedor, no entrega Android ni prueba de Keystore.

## Migración poblada

`backend/scripts/verify_notifications_migration.py` permite solo fuentes PostgreSQL locales `fashionstore_test_*`. Creó copia nueva `fashionstore_test_push_migration_f04bd619b0` mediante pg_dump/pg_restore; no alteró la base live. Simuló dispositivo antiguo sin columnas de protección, ejecutó DDL dos veces y conservó el registro, inelegible para entrega. Comparó las otras 42 tablas antes/después, incluyendo datos comerciales. Dump temporal eliminado; copia QA conservada para inspección.

Fuente QA: `fashionstore_test_notifications_utf8_live`; pytest usó exclusivamente `fashionstore_test_notifications_utf8`. Ninguna migración/seed/restore/compra/cobro remotos ni llamada real al proveedor en C1.

## Estado para continuar

- Backend base f635346; web 59188ba; móvil d64ffb1. Ningún commit/push ni cambio Railway/Vercel en C1. Git preferido D-blip-max.
- Código Flutter y generados desktop previos conservados; no hubo nueva APK ni suite móvil en esta fase. No sumar resultados anteriores como pruebas nuevas.
- Build web actual apunta a Railway, no API QA. No prueba que Vercel ya incluya cambios. El aumento/aviso de bundle se revisará en preparación C3, sin subir presupuestos para ocultarlo.
- Evidencias de pruebas en TEMP/notifications-c1-*.log, resultado migración TEMP/notifications-c1-migration.json; hashes/manifiesto en NOTIFICACIONES_C1_ARTEFACTOS.json.
- C2 pendiente: recepción push Android real, tap/arranque/reconexión/cuentas, clic real web. C3: regresión completa y APK candidata. C4: publicación y aceptación remota.
- No declarar la ampliación completa ni iniciar automáticamente otra fase. No publicar estos cambios aislados antes de los criterios del plan.
- API QA 58013 y PostgreSQL propio 55444 detenidos al cierre; copia de migración conservada en el cluster apagado. Reiniciar explícitamente para C2. Plan/spec/cierre/manifiesto copiados a docs/notificaciones de los tres repos, aún sin commit.
