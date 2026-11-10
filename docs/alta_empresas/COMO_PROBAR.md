# Alta de empresas por invitación

1. Web: Superadmin → Empresas → Crear empresa. Completa empresa, NIT, identificador, nombre y correo nuevo del propietario. Pulsa Crear y enviar invitación.
2. Propietario: abre el correo, verifica su dirección creando una contraseña y entra desde el enlace al login empresarial. El enlace vence en 48 horas y sirve una sola vez. Puede reabrir el correo si cerró la página antes de confirmar.
3. Mi suscripción: cotiza un plan, acepta sus condiciones y envía la solicitud. Superadmin aprueba desde Solicitudes; propietario prepara/aprueba/confirma el pago existente. Después puede crear sucursales y configurar su propio PayPal.

Empresa habilitada administrativamente no significa contrato pagado. Sin período vigente no se admiten nuevas operaciones comerciales. No hay año gratis, contraseña elegida por superadmin ni registro público. El correo debe ser nuevo porque las cuentas actuales pertenecen a una sola empresa.

Si falla o vence el correo, Empresas → Reenviar invitación. Invalida el enlace anterior y conserva la empresa. No volver a crearla. Si el propietario ya aceptó, entra con su contraseña; no reenviar. Una suspensión explícita del superadmin no puede levantarse desde el enlace.

Usa una ventana privada para abrir la invitación si mantienes otra cuenta iniciada. No exige nuevo APK ni variables: reutiliza Brevo y SUBSCRIPTION_WEB_URL existentes. Hay una tabla operacional nueva company_invitations, creada en el fresh demo de despliegue y excluida de los backups comerciales. No se guardan tokens en claro ni contraseñas en auditoría.
