# Configuración de correo del software

## Brevo HTTPS — transporte elegido al cerrar

Railway no pudo conectar SMTP Gmail. Se usa directamente `https://api.brevo.com/v3/smtp/email`, sin puente Vercel ni SDK nuevo. Configurar privadamente `RECEIPT_EMAIL_PROVIDER=brevo`, `BREVO_API_KEY`, `RECEIPT_EMAIL_ENABLED=true` y `SMTP_FROM` con el remitente verificado. El nombre SMTP_FROM se conserva por compatibilidad, también identifica al remitente Brevo. Las claves nunca se incorporan a Angular/APK/Git. SMTP sigue opcional para entornos que lo permitan; no hay fallback automático ni doble envío.

Se adjuntan los mismos bytes PDF canónicos. HTTP201 con messageId significa ACCEPTED; 4xx definitivos FAILED; timeout/5xx/aceptación incierta UNKNOWN. El UUID de envío existente impide replay con efectos duplicados. No cambiar la APK por esta elección de proveedor.

El backend envía el mismo PDF que entrega Descargar PDF. Un remitente SMTP para toda la plataforma; el nombre visible incluye la empresa. Los correos de prueba son destinatarios, no credenciales del servidor.

Variables del servicio backend (valores privados fuera de Git):

```dotenv
RECEIPT_EMAIL_ENABLED="true"
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
SMTP_USERNAME="<cuenta Gmail remitente>"
SMTP_PASSWORD="<contraseña de aplicación>"
SMTP_FROM="<misma cuenta Gmail remitente>"
```

También admite puerto465 con TLS desde el inicio. Nunca admite SMTP sin cifrar. Gmail requiere verificación en dos pasos y contraseña de aplicación, no contraseña normal.

La configuración privada de esta instalación está en `LOCALAPPDATA/FashionStore/receipt-email/smtp.pending.json`. Leer desde archivo sin imprimir valores. Autenticación local comprobada. En F3 se aplicaron las seis variables en Railway con skipDeploys=true; requiere el despliegue y aceptación de entrega real que registra el cierre F3.

Base existente: ejecutar de forma explícita `python scripts/migrate_receipt_email.py` con la base correcta. Es aditiva y repetible. La demo desechable con el predeploy acordado crea la tabla automáticamente en el fresh; no ejecutar ambos para forzar resets. Health requiere esquema compatible.

Sin configuración, capabilities indica email_enabled=false y PDF sigue disponible. El servicio acepta un destinatario por solicitud; el UUID debe reutilizarse si se pierde la respuesta. Reenvío deliberado utiliza otro UUID tras el límite. ACCEPTED significa aceptado por SMTP: para certificar la llegada debe comprobarse el buzón.

No activar aún envío automático tras venta, colas offline de correo, envíos masivos ni otro servicio Railway.
