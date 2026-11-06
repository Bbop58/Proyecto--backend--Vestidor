"""Server-owned contractual capabilities, independent of commercial role grants."""
import hashlib,hmac,uuid
from datetime import datetime,timedelta,timezone
from enum import Enum
import jwt
from fastapi import HTTPException
from sqlalchemy import select,func
from app.config import settings
from app.core import billing_calendar
from app.core.plan_features import PLAN_FEATURES
from app.core.access_policy import is_platform_candidate
from app.models.subscription import Subscription
from app.models.subscription_term import SubscriptionTerm
from app.models.subscription_plan import SubscriptionPlan
from app.models.subscription_plan_version import SubscriptionPlanVersion
from app.models.branch import Branch
from app.services.role_service import RoleService


class Purpose(str,Enum):
    START_NEW='START_NEW'
    READ_HISTORY='READ_HISTORY'
    RECONCILE_PENDING='RECONCILE_PENDING'
    REMEDIATE_LIMIT='REMEDIATE_LIMIT'


class EntitlementService:
    @staticmethod
    def can_recover(user):
        return bool(user and user.is_active and user.is_verified and user.tenant and
            not is_platform_candidate(user) and user.role and
            any(p.code=='subscriptions.manage' for p in user.role.permissions))

    @classmethod
    def resolve(cls,db,user,at=None):
        if not user or not user.is_active or not user.tenant_id or is_platform_candidate(user):
            raise HTTPException(403,'Se requiere una identidad comercial activa')
        if db.info.get('tenant_scope') and db.info['tenant_scope']!=user.tenant_id:
            raise HTTPException(403,'Contrato de otra empresa')
        connection=db.connection();today=at or billing_calendar.business_today()
        sub=connection.execute(select(Subscription.__table__).where(Subscription.tenant_id==user.tenant_id)).mappings().first()
        term=None
        if sub:
            term=connection.execute(select(SubscriptionTerm.__table__).where(SubscriptionTerm.subscription_id==sub['id'],
                SubscriptionTerm.tenant_id==user.tenant_id,SubscriptionTerm.estado=='CONFIRMADO',
                SubscriptionTerm.fecha_inicio<=today,SubscriptionTerm.fecha_fin>today)).mappings().first()
        if term and term['origen']=='PAYPAL':
            from app.models.subscription_payment import SubscriptionPayment
            verified=connection.execute(select(SubscriptionPayment.id).where(
                SubscriptionPayment.request_id==term['request_id'],SubscriptionPayment.tenant_id==user.tenant_id,
                SubscriptionPayment.state=='APPLIED')).first()
            if not verified:term=None
        active=bool(user.tenant.activo and sub and sub['estado']=='ACTIVA' and term)
        state='ACTIVA' if active else ('EMPRESA_SUSPENDIDA' if not user.tenant.activo else
            'SIN_CONTRATO' if not sub else 'SUSCRIPCION_SUSPENDIDA' if sub['estado']!='ACTIVA' else 'SIN_PERIODO_VIGENTE')
        usage=connection.execute(select(func.count(Branch.id)).where(Branch.tenant_id==user.tenant_id,Branch.activa.is_(True))).scalar_one()
        features={key:bool(active and term['features'].get(key) is True) for key in PLAN_FEATURES}
        return {'active':active,'estado_efectivo':state,'service_restricted':not active,
            'features':features,'max_sucursales':term['max_sucursales'] if term else 0,
            'sucursales_usadas':usage,'exceso_sucursales':max(0,usage-(term['max_sucursales'] if term else 0)),
            'term':dict(term) if term else None,'subscription':dict(sub) if sub else None}

    @classmethod
    def check(cls,db,user,feature=None,purpose=Purpose.START_NEW):
        if purpose not in set(Purpose):raise HTTPException(403,'Propósito contractual desconocido')
        if feature is not None and feature not in PLAN_FEATURES:raise HTTPException(403,'Capacidad contractual desconocida')
        result=cls.resolve(db,user)
        if purpose in (Purpose.READ_HISTORY,Purpose.REMEDIATE_LIMIT):return result
        if purpose==Purpose.RECONCILE_PENDING:
            raise HTTPException(403,'La conciliación exige una autorización previa verificable por operación')
        if not result['active']:raise HTTPException(403,'Servicio restringido: requiere un período contratado vigente')
        labels={'reportes_ia':'reportes IA','analitica_avanzada':'analítica avanzada','offline':'operaciones offline'}
        if feature and not result['features'][feature]:raise HTTPException(403,f'El período contratado no incluye {labels[feature]}')
        return result

    @staticmethod
    def _key():return hmac.new(settings.SECRET_KEY.encode(),b'FashionStore:offline-entitlements:v1',hashlib.sha256).digest()

    @classmethod
    def payment_recovery(cls,db,user,intent):
        if cls.resolve(db,user)['active']:return True
        reference=(intent.payload or {}).get('license_term_id')
        if not reference:return False
        try:term_id=uuid.UUID(reference)
        except (ValueError,TypeError):return False
        row=db.connection().execute(select(SubscriptionTerm.__table__).where(SubscriptionTerm.id==term_id,
            SubscriptionTerm.tenant_id==user.tenant_id)).mappings().first()
        created=intent.created_at.replace(tzinfo=timezone.utc) if intent.created_at.tzinfo is None else intent.created_at
        day=created.astimezone(timezone(timedelta(hours=-4))).date()
        return bool(row and row['fecha_inicio']<=day<row['fecha_fin'] and datetime.now(timezone.utc)<created+timedelta(hours=24))

    @classmethod
    def authorize_offline(cls,db,user,channel,count=10):
        result=cls.check(db,user,'offline');codes=RoleService.get_permission_codes(db,user.role_id)
        kinds=[]
        if channel=='WALLET' and 'reservas.create' in codes:kinds=['COMPRA_BILLETERA_OFFLINE']
        if channel=='SYNC':
            if 'ventas.create' in codes:kinds.append('VENTA_CONTADO_OFFLINE')
            if 'reservas.create' in codes:kinds.append('RESERVA_OFFLINE')
        if not kinds:raise HTTPException(403,'Tu rol no autoriza este canal offline')
        now=datetime.now(timezone.utc)
        end=datetime.combine(result['term']['fecha_fin'],datetime.min.time(),timezone(timedelta(hours=-4)))
        expires=min(now+timedelta(hours=24),end.astimezone(timezone.utc)+timedelta(hours=24))
        grants=[]
        for _ in range(count):
            op=uuid.uuid4();payload={'purpose':'OFFLINE_ENTITLEMENT','iss':'FashionStore','aud':'offline-entitlements-v1',
                'iat':int(now.timestamp()),'exp':int(expires.timestamp()),'operation_id':str(op),
                'actor_id':str(user.id),'tenant_id':str(user.tenant_id),'term_id':str(result['term']['id']),
                'channel':channel,'kinds':kinds}
            grants.append({'client_operation_id':str(op),'authorization':jwt.encode(payload,cls._key(),algorithm='HS256'),
                           'expires_at':expires.isoformat(),'channel':channel})
        return {'grants':grants}

    @classmethod
    def reconcile(cls,db,user,operation_id,channel,kind,authorization=None):
        current=cls.resolve(db,user)
        if current['active'] and current['features']['offline']:return current
        if not authorization:raise HTTPException(409,'REVISION_REQUERIDA: conserva el pendiente sin autorización contractual previa')
        try:
            claims=jwt.decode(authorization,cls._key(),algorithms=['HS256'],audience='offline-entitlements-v1',issuer='FashionStore',
                options={'require':['exp','iat','purpose','actor_id','tenant_id','operation_id','channel','term_id']})
        except jwt.InvalidTokenError:raise HTTPException(409,'REVISION_REQUERIDA: autorización ausente, vencida o no verificable; conserva el pendiente')
        if (claims['purpose']!='OFFLINE_ENTITLEMENT' or claims['actor_id']!=str(user.id) or claims['tenant_id']!=str(user.tenant_id)
            or claims['operation_id']!=str(operation_id) or claims['channel']!=channel or kind not in claims.get('kinds',[])):
            raise HTTPException(409,'REVISION_REQUERIDA: la autorización no corresponde a esta operación')
        try:term_id=uuid.UUID(claims['term_id'])
        except ValueError:raise HTTPException(409,'REVISION_REQUERIDA: referencia contractual inválida')
        term=db.connection().execute(select(SubscriptionTerm.__table__).where(SubscriptionTerm.id==term_id,
            SubscriptionTerm.tenant_id==user.tenant_id)).mappings().first()
        issued=datetime.fromtimestamp(claims['iat'],timezone.utc).astimezone(timezone(timedelta(hours=-4))).date()
        if not term or not (term['fecha_inicio']<=issued<term['fecha_fin']) or term['features'].get('offline') is not True:
            raise HTTPException(409,'REVISION_REQUERIDA: la autorización no tenía período habilitado')
        return current
