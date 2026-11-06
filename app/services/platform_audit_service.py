from fastapi import HTTPException
from app.models.platform_audit_log import PlatformAuditLog

COMPANY_FIELDS = {'id','nombre','razon_social','nit_rut','slug','email_contacto',
                  'telefono','activo','platform_revision','created_at','sucursales_activas'}
PLAN_FIELDS={'id','codigo','nombre','activo'}
VERSION_FIELDS={'id','plan_id','revision','estado','lock_version','precio_mensual_bob',
                'moneda','periodicidad','max_sucursales','features','descripcion','origen',
                'es_gratuito','published_at','created_at'}
CONTRACT_FIELDS={'id','tenant_id','estado','term_id','approval_mode','payment_status'}


class PlatformAuditService:
    @staticmethod
    def record(db, actor, action, target_type, target_id, before, after, reason,
               operation_id=None, fingerprint=None, result=None):
        allowed = {'COMPANY':COMPANY_FIELDS,'PLAN':PLAN_FIELDS,'PLAN_VERSION':VERSION_FIELDS,
                   'CONTRACT_REQUEST':CONTRACT_FIELDS}.get(target_type,set())
        if not set(before) <= allowed or not set(after) <= allowed or (result is not None and not set(result) <= allowed):
            raise HTTPException(400, 'La auditoría contiene campos no permitidos')
        entry = PlatformAuditLog(actor_id=actor.id, action=action, target_type=target_type,
            target_id=target_id, before_data=before, after_data=after, reason=reason,
            operation_id=operation_id, request_fingerprint=fingerprint, result_payload=result)
        db.add(entry)
        db.flush()
        return entry
