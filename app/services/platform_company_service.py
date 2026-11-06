"""Explicit administrative projections. Never selects a company's private graph."""
import hashlib
import json
import uuid
from fastapi import HTTPException
from sqlalchemy import select, func, insert, update
from sqlalchemy.exc import IntegrityError
from app.models.tenant import Tenant
from app.models.branch import Branch
from app.models.platform_audit_log import PlatformAuditLog
from app.schemas.platform import PlatformCompanyResponse
from app.core.access_policy import resolve_access_scope, AccessScope
from app.services.platform_audit_service import PlatformAuditService

FIELDS = ('id','nombre','razon_social','nit_rut','slug','email_contacto','telefono',
          'activo','platform_revision','created_at')


class PlatformCompanyService:
    @staticmethod
    def _authorize(db, actor):
        if resolve_access_scope(actor) != AccessScope.PLATFORM or db.info.get('platform_actor_id') != actor.id:
            raise HTTPException(403, 'Se requiere contexto de plataforma verificado')

    @staticmethod
    def _projection():
        count = select(func.count(Branch.id)).where(
            Branch.tenant_id == Tenant.id, Branch.activa.is_(True)).scalar_subquery()
        return select(*(getattr(Tenant, k) for k in FIELDS), count.label('sucursales_activas')).where(Tenant.es_operadora.is_(False))

    @staticmethod
    def _json(row):
        return PlatformCompanyResponse.model_validate(dict(row)).model_dump(mode='json')

    @classmethod
    def list_companies(cls, db, actor, limit=50, offset=0):
        cls._authorize(db, actor)
        connection = db.connection()
        total = connection.execute(select(func.count(Tenant.id)).where(Tenant.es_operadora.is_(False))).scalar_one()
        rows = connection.execute(cls._projection().order_by(Tenant.created_at,Tenant.id).limit(limit).offset(offset)).mappings()
        return {'total':total, 'companies':[cls._json(row) for row in rows]}

    @classmethod
    def get_company(cls, db, actor, company_id):
        cls._authorize(db, actor)
        row = db.connection().execute(cls._projection().where(Tenant.id == company_id)).mappings().first()
        if not row:
            raise HTTPException(404, 'Empresa no disponible')
        return cls._json(row)

    @staticmethod
    def _fingerprint(action, target, data):
        return hashlib.sha256(json.dumps({'action':action,'target':str(target),'data':data},
                sort_keys=True, ensure_ascii=False, separators=(',',':')).encode()).hexdigest()

    @staticmethod
    def _lock_operation(connection, actor, operation_id):
        digest = hashlib.sha256(f'{actor.id}:{operation_id}'.encode()).digest()
        key = int.from_bytes(digest[:8], 'big', signed=True)
        connection.execute(select(func.pg_advisory_xact_lock(key)))

    @staticmethod
    def _replay(connection, actor, operation_id, fingerprint):
        row = connection.execute(select(PlatformAuditLog.request_fingerprint,PlatformAuditLog.result_payload)
            .where(PlatformAuditLog.actor_id==actor.id, PlatformAuditLog.operation_id==operation_id)).mappings().first()
        if row:
            if row['request_fingerprint'] != fingerprint:
                raise HTTPException(409, 'La clave de operación ya contiene otra solicitud')
            return row['result_payload']
        return None

    @classmethod
    def create_company(cls, db, actor, request):
        cls._authorize(db, actor)
        data = request.model_dump(mode='json')
        fingerprint = cls._fingerprint('COMPANY_CREATE','NEW',data)
        try:
            connection = db.connection()
            cls._lock_operation(connection, actor, request.operation_id)
            replay = cls._replay(connection,actor,request.operation_id,fingerprint)
            if replay is not None:
                return replay
            company_id=uuid.uuid4()
            # Creation is metadata only. No company credentials, subscription or free year.
            values={k:data[k] for k in ('nombre','razon_social','nit_rut','slug','email_contacto','telefono')}
            connection.execute(insert(Tenant).values(id=company_id,activo=False,es_operadora=False,
                                                      platform_revision=0,**values))
            result=cls.get_company(db,actor,company_id)
            PlatformAuditService.record(db,actor,'COMPANY_CREATE','COMPANY',company_id,{},result,
                request.reason,request.operation_id,fingerprint,result)
            db.commit()
            return result
        except IntegrityError:
            db.rollback()
            raise HTTPException(409,'Empresa u operación ya registrada')
        except Exception:
            db.rollback()
            raise

    @classmethod
    def set_company_status(cls, db, actor, company_id, request):
        cls._authorize(db, actor)
        fingerprint=cls._fingerprint('COMPANY_STATUS',company_id,request.model_dump(mode='json'))
        try:
            connection=db.connection()
            cls._lock_operation(connection,actor,request.operation_id)
            replay=cls._replay(connection,actor,request.operation_id,fingerprint)
            if replay is not None:
                return replay
            row=connection.execute(select(Tenant.platform_revision).where(
                Tenant.id==company_id,Tenant.es_operadora.is_(False)).with_for_update()).first()
            if not row:
                raise HTTPException(404,'Empresa no disponible')
            before=cls.get_company(db,actor,company_id)
            if row.platform_revision != request.expected_version:
                raise HTTPException(409,'La empresa cambió; actualiza antes de confirmar')
            if before['activo'] != request.activo:
                connection.execute(update(Tenant).where(Tenant.id==company_id).values(
                    activo=request.activo,platform_revision=row.platform_revision+1))
            after=cls.get_company(db,actor,company_id)
            PlatformAuditService.record(db,actor,'COMPANY_STATUS','COMPANY',company_id,before,after,
                request.reason,request.operation_id,fingerprint,after)
            db.commit()
            return after
        except Exception:
            db.rollback()
            raise
