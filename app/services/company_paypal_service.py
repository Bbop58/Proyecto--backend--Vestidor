"""Company-owned credential revisions. No API, log or snapshot may return secrets."""
import hashlib
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select, insert, func, update, text
from app.config import settings
from app.models.company_paypal_credential import CompanyPayPalCredential as Credential
from app.models.tenant import Tenant
from app.core.payment_credentials_crypto import encrypt_secret, decrypt_secret


class CompanyPayPalService:
    @staticmethod
    def merchant(mode, client_id):
        return hashlib.sha256(f'{mode}:{client_id}'.encode()).hexdigest()

    @staticmethod
    def _authorize(db, actor):
        from app.core.access_policy import resolve_access_scope, AccessScope
        from app.models import User,Role,Permission
        from app.models.role_permission import role_permissions
        from sqlalchemy import or_,and_
        allowed=db.connection().execute(select(User.id).join(Tenant,Tenant.id==User.tenant_id)
            .join(Role,Role.id==User.role_id).join(role_permissions,role_permissions.c.role_id==Role.id)
            .join(Permission,Permission.id==role_permissions.c.permission_id)
            .where(User.id==actor.id,User.tenant_id==actor.tenant_id,User.is_active.is_(True),
                   Tenant.activo.is_(True),Tenant.es_operadora.is_(False),Role.name!='superadmin_plataforma',
                   or_(Role.tenant_id==actor.tenant_id,and_(Role.tenant_id.is_(None),Role.name.in_(['admin','encargado','cajero','cliente']))),
                   Permission.code=='payments.configure')).first()
        if resolve_access_scope(actor)!=AccessScope.COMPANY or not allowed:
            raise HTTPException(403,'No puedes configurar los pagos de esta empresa')

    @staticmethod
    def latest(connection, tenant_id):
        return connection.execute(select(Credential.__table__).where(Credential.tenant_id == tenant_id)
                                  .order_by(Credential.revision.desc()).limit(1)).mappings().first()

    @staticmethod
    def _decrypt(row):
        return decrypt_secret(row['secret_ciphertext'], row['tenant_id'], row['id'], row['revision'], row['client_id'], row['mode'])

    @staticmethod
    def legacy_required(connection,tenant_id):
        row=connection.execute(select(Tenant.paypal_client_id,Tenant.paypal_client_secret).where(Tenant.id==tenant_id)).first()
        return bool(row and (row.paypal_client_id or row.paypal_client_secret))

    @classmethod
    def verify_history(cls,connection,tenant_id):
        for row in connection.execute(select(Credential.__table__).where(Credential.tenant_id==tenant_id,Credential.enabled.is_(True))).mappings():
            cls._decrypt(row)

    @staticmethod
    def _validate(client_id, secret, mode):
        if (not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 255
                or not isinstance(secret, str) or not secret.strip() or len(secret) > 4096
                or mode not in ('sandbox', 'live')):
            raise HTTPException(422, 'Completa las credenciales de la cuenta PayPal')
        if client_id.strip() == settings.PAYPAL_CLIENT_ID.strip():
            raise HTTPException(503, 'La cuenta PayPal de plataforma no admite cobros comerciales')

    @staticmethod
    def _lock(connection, tenant_id, active_required=False):
        # Match snapshot lock ordering: credential table before the tenant row.
        schema=connection.schema_for_object(Credential.__table__) or connection.execute(text('SELECT current_schema()')).scalar_one()
        qualified=connection.dialect.identifier_preparer.quote_schema(schema)+'.company_paypal_credentials'
        connection.exec_driver_sql('LOCK TABLE '+qualified+' IN ROW EXCLUSIVE MODE')
        # NO KEY UPDATE: serialize configuration without blocking child FK checks.
        query=select(Tenant.id).where(Tenant.id==tenant_id,Tenant.es_operadora.is_(False))
        if active_required:query=query.where(Tenant.activo.is_(True))
        if not connection.execute(query.with_for_update(key_share=True)).first():
            raise HTTPException(403 if active_required else 404,'Empresa no disponible')

    @classmethod
    def append(cls, connection, tenant_id, *, client_id=None, secret=None, mode=None, verified_at=None):
        cls._lock(connection, tenant_id)
        previous = cls.latest(connection, tenant_id)
        revision = previous['revision'] + 1 if previous else 1
        identifier = uuid.uuid4()
        enabled = client_id is not None
        if enabled:
            cls._validate(client_id, secret, mode)
            client_id, secret = client_id.strip(), secret.strip()
            # Serialize even the absent-row case across companies using this app.
            lock = int.from_bytes(hashlib.sha256(client_id.encode()).digest()[:8], 'big', signed=True)
            connection.execute(select(func.pg_advisory_xact_lock(lock)))
            current = select(Credential.tenant_id, func.max(Credential.revision).label('revision')).group_by(Credential.tenant_id).subquery()
            duplicate = connection.execute(select(Credential.id).join(current,
                (Credential.tenant_id == current.c.tenant_id) & (Credential.revision == current.c.revision))
                .where(Credential.tenant_id != tenant_id, Credential.enabled.is_(True), Credential.client_id == client_id)).first()
            if duplicate: raise HTTPException(409, 'La configuración de cobro coincide con otra empresa')
        ciphertext = encrypt_secret(secret, tenant_id, identifier, revision, client_id, mode) if enabled else None
        connection.execute(insert(Credential).values(id=identifier, tenant_id=tenant_id, revision=revision,
            enabled=enabled, client_id=client_id, mode=mode, secret_ciphertext=ciphertext,
            merchant_key=cls.merchant(mode, client_id) if enabled else None, verified_at=verified_at))
        # These columns remain only for an explicit, one-time legacy migration.
        connection.execute(update(Tenant).where(Tenant.id == tenant_id).values(
            paypal_client_id=None, paypal_client_secret=None, paypal_mode=None))
        return identifier

    @classmethod
    def resolve(cls, db, tenant_id, intent=None, *, for_new_payment=False):
        connection = db.connection()
        if for_new_payment:cls._lock(connection,tenant_id,active_required=True)
        company = connection.execute(select(Tenant.id, Tenant.es_operadora).where(Tenant.id == tenant_id)).first()
        if not company or company.es_operadora: raise HTTPException(404, 'Empresa no disponible')
        row = None
        if intent is not None:
            if intent.tenant_id != tenant_id: raise HTTPException(404, 'Pago no disponible')
            if not isinstance(intent.payload,dict):raise HTTPException(409,'Referencia de pago inválida')
            identifier = intent.payload.get('paypal_credential_id')
            query = select(Credential.__table__).where(Credential.tenant_id == tenant_id, Credential.enabled.is_(True), Credential.merchant_key == intent.merchant_key)
            if identifier:
                try: identifier = uuid.UUID(str(identifier))
                except (ValueError, TypeError): raise HTTPException(409, 'Referencia de pago inválida') from None
                if not connection.execute(query.where(Credential.id == identifier)).first():
                    raise HTTPException(409, 'La referencia original del pago requiere conciliación')
            # A newer secret for the SAME application may replace a revoked secret;
            # tenant + mode + client_id/merchant remain exactly those of the intent.
            row = connection.execute(query.order_by(Credential.revision.desc()).limit(1)).mappings().first()
            if not row: raise HTTPException(409, 'La cuenta original del pago requiere conciliación')
        else:
            if cls.legacy_required(connection,tenant_id):
                raise HTTPException(503,'La configuración PayPal anterior requiere migración')
            row = cls.latest(connection, tenant_id)
        if not row or not row['enabled']: raise HTTPException(503, 'PayPal no está configurado para esta empresa')
        secret = cls._decrypt(row)
        cls._validate(row['client_id'], secret, row['mode'])
        name = connection.execute(select(Tenant.nombre).where(Tenant.id == tenant_id)).scalar_one()
        return {'client_id':row['client_id'], 'client_secret':secret, 'mode':row['mode'], 'brand_name':name, '_credential_id':str(row['id'])}

    @classmethod
    def state(cls, db, actor):
        cls._authorize(db, actor)
        row = cls.latest(db.connection(), actor.tenant_id)
        enabled = bool(row and row['enabled'])
        return {'revision':row['revision'] if row else 0, 'enabled':enabled,
                'available':cls.availability(db,actor)['enabled'],
                'legacy_migration_required':cls.legacy_required(db.connection(),actor.tenant_id),
                'client_id':row['client_id'] if enabled else None, 'mode':row['mode'] if enabled else 'sandbox',
                'verified_at':row['verified_at'] if enabled else None,
                'status':'verified' if enabled and row['verified_at'] else 'configured_unverified' if enabled else 'disabled'}

    @classmethod
    def availability(cls, db, actor):
        try:
            cls.resolve(db, actor.tenant_id)
            row = cls.latest(db.connection(), actor.tenant_id)
            return {'enabled':True, 'mode':row['mode'], 'status':'verified' if row['verified_at'] else 'configured_unverified'}
        except HTTPException:
            return {'enabled':False, 'mode':None, 'status':'unavailable'}

    @classmethod
    def configure(cls, db, actor, body):
        from app.services.paypal_service import PayPalService
        from app.services.audit_service import AuditService
        from app.core.payment_credentials_crypto import _key
        cls._authorize(db, actor)
        try:
            _key()
            if cls.legacy_required(db.connection(),actor.tenant_id):
                raise HTTPException(409,'Migra la configuración PayPal anterior antes de modificarla')
            cls.verify_history(db.connection(),actor.tenant_id)
            previous = cls.latest(db.connection(), actor.tenant_id)
            if (previous['revision'] if previous else 0) != body.expected_revision:
                raise HTTPException(409, 'La configuración cambió; vuelve a cargar antes de guardar')
            if previous and previous['enabled']:
                cls._decrypt(previous)  # Do not hide loss of the key by overwriting configuration.
            secret = body.client_secret.get_secret_value().strip() if body.client_secret else ''
            if not secret:
                if not previous or not previous['enabled'] or (previous['client_id'], previous['mode']) != (body.client_id, body.mode):
                    raise HTTPException(422, 'Introduce el secreto al cambiar o habilitar la cuenta')
                secret = cls._decrypt(previous)
            cls._validate(body.client_id, secret, body.mode)
            PayPalService.get_access_token(body.client_id, secret, body.mode)  # OAuth only; no charge.
            cls._lock(db.connection(), actor.tenant_id,active_required=True)
            cls._authorize(db,actor)
            if cls.legacy_required(db.connection(),actor.tenant_id):
                raise HTTPException(409,'La configuración anterior requiere migración')
            cls.verify_history(db.connection(),actor.tenant_id)
            locked = cls.latest(db.connection(), actor.tenant_id)
            if (locked['revision'] if locked else 0) != body.expected_revision:
                raise HTTPException(409, 'La configuración cambió durante la verificación')
            cls.append(db.connection(), actor.tenant_id, client_id=body.client_id, secret=secret,
                       mode=body.mode, verified_at=datetime.now(timezone.utc))
            AuditService.record(db, 'Configuración PayPal verificada y actualizada', 'Pagos', actor)
            db.commit()
            return cls.state(db, actor)
        except Exception:
            db.rollback(); raise

    @classmethod
    def disable(cls, db, actor, expected_revision):
        from app.services.audit_service import AuditService
        cls._authorize(db, actor)
        try:
            cls._lock(db.connection(), actor.tenant_id,active_required=True)
            cls._authorize(db,actor)
            if cls.legacy_required(db.connection(),actor.tenant_id):
                raise HTTPException(409,'Migra la configuración PayPal anterior antes de modificarla')
            previous = cls.latest(db.connection(), actor.tenant_id)
            if (previous['revision'] if previous else 0) != expected_revision:
                raise HTTPException(409, 'La configuración cambió; vuelve a cargar')
            cls.append(db.connection(), actor.tenant_id)
            AuditService.record(db, 'PayPal deshabilitado para nuevas operaciones', 'Pagos', actor)
            db.commit(); return cls.state(db, actor)
        except Exception:
            db.rollback(); raise
