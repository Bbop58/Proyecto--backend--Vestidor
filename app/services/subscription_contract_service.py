"""Accepted offers and manual, atomic decisions. Never captures money."""
import hashlib,hmac,json,uuid
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone,date
from decimal import Decimal
import jwt
from fastapi import HTTPException
from sqlalchemy import select,insert,update,func
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.core.billing_calendar import business_today,next_billing_date
from app.core.plan_features import feature_values
from app.models.tenant import Tenant
from app.models.branch import Branch
from app.models.subscription import Subscription as Sub
from app.models.subscription_term import SubscriptionTerm as Term
from app.models.subscription_change_request import SubscriptionChangeRequest as Request
from app.models.subscription_plan import SubscriptionPlan as Plan
from app.models.subscription_plan_version import SubscriptionPlanVersion as Version
from app.services.audit_service import AuditService
from app.services.platform_audit_service import PlatformAuditService
from app.services.plan_catalog_service import PlanCatalogService


class SubscriptionContractService:
    AUDIENCE='fashionstore-subscription-quotes-v1'

    @staticmethod
    def now():return datetime.now(timezone.utc)

    @staticmethod
    def _key():return hmac.new(settings.SECRET_KEY.encode(),b'FashionStore:subscription-quote:v1',hashlib.sha256).digest()

    @staticmethod
    def _hash(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    @staticmethod
    def _company(db,actor):
        from app.api.deps import get_subscription_manager
        return get_subscription_manager(db,{'sub':str(actor.id),'tenant_id':str(actor.tenant_id)})

    @staticmethod
    @contextmanager
    def _write(db):
        try:
            from app.core.payment_evidence import lock_contract_tables
            connection=db.connection();lock_contract_tables(connection)
            yield connection;db.commit()
        except IntegrityError:
            db.rollback();raise HTTPException(409,'El contrato cambió o se intentó duplicar una operación')
        except Exception:
            db.rollback();raise

    @staticmethod
    def lock_company(connection,tenant_id):
        if not connection.execute(select(Tenant.id).where(Tenant.id==tenant_id,Tenant.es_operadora.is_(False))
                .with_for_update(key_share=True)).first():raise HTTPException(404,'Empresa no disponible')

    @staticmethod
    def _subscription(connection,tenant_id,lock=False):
        query=select(Sub.__table__).where(Sub.tenant_id==tenant_id)
        row=connection.execute(query.with_for_update(key_share=True) if lock else query).mappings().first()
        return dict(row) if row else None

    @staticmethod
    def _version(connection,version_id,current=False,lock=False):
        query=select(Version.__table__).join(Plan,Plan.id==Version.plan_id)\
            .where(Version.id==version_id,Version.estado=='PUBLICADA',Plan.activo.is_(True))
        row=connection.execute(query.with_for_update(read=True,of=Version) if lock else query).mappings().first()
        if not row:raise HTTPException(409,'La oferta no está disponible; solicita una nueva cotización')
        if current:
            latest=connection.execute(select(func.max(Version.revision)).where(Version.plan_id==row['plan_id'],Version.estado!='BORRADOR')).scalar_one()
            if row['revision']!=latest:raise HTTPException(409,'Cotiza la revisión actualmente ofrecida')
        return dict(row)

    @staticmethod
    def _conditions(row):
        return {'precio_mensual_bob':format(row['precio_mensual_bob'],'.2f'),'moneda':row['moneda'],
            'periodicidad':row['periodicidad'],'max_sucursales':row['max_sucursales'],
            'features':feature_values(row),'revision':row['revision'],'plan_id':str(row['plan_id'])}

    @staticmethod
    def _schedule(connection,sub,kind):
        latest=None
        if sub:
            latest=connection.execute(select(Term.__table__).where(Term.subscription_id==sub['id'])
                .order_by(Term.fecha_fin.desc()).limit(1)).mappings().first()
            if not latest and sub['estado']!='PENDIENTE':
                raise HTTPException(409,'Falta inicializar la referencia contractual legada en mantenimiento')
        if latest and kind=='ALTA':raise HTTPException(409,'La empresa tiene historia; solicita renovación o cambio')
        if not latest and kind!='ALTA':raise HTTPException(409,'La empresa requiere una solicitud de alta contractual')
        today=business_today()
        if latest and latest['fecha_fin']>=today:
            start=latest['fecha_fin'];anchor=latest['anchor_day']
        else:start=today;anchor=start.day
        return {'basis_term_id':str(latest['id']) if latest else None,'fecha_inicio':start.isoformat(),
                'fecha_fin':next_billing_date(start,anchor).isoformat(),'anchor_day':anchor}

    @classmethod
    def quote(cls,db,actor,version_id,kind):
        actor=cls._company(db,actor);connection=db.connection()
        version=cls._version(connection,version_id,current=True)
        sub=cls._subscription(connection,actor.tenant_id)
        schedule=cls._schedule(connection,sub,kind)
        now=cls.now();expires=now+timedelta(minutes=15)
        payload={'purpose':'SUBSCRIPTION_QUOTE','iss':'FashionStore','aud':cls.AUDIENCE,
            'iat':int(now.timestamp()),'exp':int(expires.timestamp()),'jti':str(uuid.uuid4()),
            'actor_id':str(actor.id),'tenant_id':str(actor.tenant_id),
            'subscription_id':str(sub['id']) if sub else None,'version_id':str(version_id),
            'kind':kind,'conditions':cls._conditions(version),'schedule':schedule,'billing_mode':'PAYPAL'}
        return {'quote_token':jwt.encode(payload,cls._key(),algorithm='HS256'),'expires_at':expires,
                'version_id':version_id,'kind':kind,'conditions':payload['conditions'],**schedule,
                'approval_mode':'MANUAL','payment_status':'NO_VERIFICADO'}

    @classmethod
    def _decode(cls,token,actor):
        try:
            payload=jwt.decode(token,cls._key(),algorithms=['HS256'],audience=cls.AUDIENCE,issuer='FashionStore',
                options={'require':['exp','iat','aud','iss','jti','purpose']})
        except jwt.ExpiredSignatureError:raise HTTPException(409,'La cotización venció; acepta una nueva')
        except jwt.InvalidTokenError:raise HTTPException(400,'Cotización inválida')
        if payload.get('purpose')!='SUBSCRIPTION_QUOTE' or payload.get('actor_id')!=str(actor.id) or payload.get('tenant_id')!=str(actor.tenant_id):
            raise HTTPException(403,'La cotización no pertenece a esta identidad y empresa')
        return payload

    @staticmethod
    def _request_json(row,connection=None):
        result=row['decision_result'] or {}
        status=result.get('payment_status','NO_VERIFICADO');payment=None
        if connection is not None:
            from app.models.subscription_payment import SubscriptionPayment
            saved=connection.execute(select(SubscriptionPayment.id,SubscriptionPayment.state).where(SubscriptionPayment.request_id==row['id'])).first()
            if saved:payment=str(saved.id);status='VERIFICADO' if saved.state=='APPLIED' else saved.state
        return {'id':str(row['id']),'tenant_id':str(row['tenant_id']),'subscription_id':str(row['subscription_id']),
            'version_id':str(row['version_id']),'kind':row['kind'],'estado':row['estado'],
            'lock_version':row['lock_version'],'conditions':row['quote_payload']['conditions'],
            'schedule':row['quote_payload']['schedule'],'accepted_by':str(row['accepted_by']),
            'accepted_at':row['accepted_at'].isoformat(),'quote_expires_at':row['quote_expires_at'].isoformat(),
            'reason':row['reason'],'decision_result':row['decision_result'],
            'approval_mode':'MANUAL','payment_status':status,'billing_payment_id':payment}

    @staticmethod
    def _request(connection,request_id,tenant_id=None,lock=False):
        query=select(Request.__table__).where(Request.id==request_id)
        if tenant_id is not None:query=query.where(Request.tenant_id==tenant_id)
        row=connection.execute(query.with_for_update() if lock else query).mappings().first()
        if not row:raise HTTPException(404,'Solicitud no disponible')
        return dict(row)

    @classmethod
    def request_change(cls,db,actor,body):
        actor=cls._company(db,actor)
        fingerprint=cls._hash({'actor_id':str(actor.id),'token_digest':hashlib.sha256(body.quote_token.encode()).hexdigest(),'acceptance':body.acceptance})
        with cls._write(db) as connection:
            cls.lock_company(connection,actor.tenant_id)
            replay=connection.execute(select(Request.__table__).where(Request.tenant_id==actor.tenant_id,
                Request.idempotency_key==body.idempotency_key)).mappings().first()
            if replay:
                if replay['fingerprint']!=fingerprint:raise HTTPException(409,'La clave ya contiene otra solicitud')
                return cls._request_json(replay,connection)
            payload=cls._decode(body.quote_token,actor)
            version=cls._version(connection,uuid.UUID(payload['version_id']))
            if cls._conditions(version)!=payload['conditions']:raise HTTPException(409,'Las condiciones cambiaron')
            sub=cls._subscription(connection,actor.tenant_id,lock=True)
            if cls._schedule(connection,sub,payload['kind'])!=payload['schedule']:
                raise HTTPException(409,'El calendario contractual cambió; recotiza')
            if sub and payload['subscription_id'] not in (None,str(sub['id'])):raise HTTPException(409,'La suscripción cambió')
            if not sub:
                sub_id=connection.execute(insert(Sub).values(tenant_id=actor.tenant_id,plan_id=version['plan_id'],
                    estado='PENDIENTE',fecha_inicio=business_today(),fecha_vencimiento=business_today()).returning(Sub.id)).scalar_one()
            else:sub_id=sub['id']
            request_id=connection.execute(insert(Request).values(tenant_id=actor.tenant_id,subscription_id=sub_id,
                version_id=version['id'],kind=payload['kind'],quote_payload=payload,accepted_by=actor.id,
                accepted_at=cls.now(),quote_expires_at=datetime.fromtimestamp(payload['exp'],timezone.utc),
                idempotency_key=body.idempotency_key,fingerprint=fingerprint).returning(Request.id)).scalar_one()
            AuditService.record(db,'Solicitud de suscripción aceptada; pendiente de decisión','Suscripciones',actor)
            return cls._request_json(cls._request(connection,request_id,actor.tenant_id),connection)

    @classmethod
    def decide_request(cls,db,actor,request_id,body):
        PlanCatalogService._authorize(db,actor)
        fingerprint=cls._hash({'request_id':str(request_id),**body.model_dump(mode='json')})
        with cls._write(db) as connection:
            key=int.from_bytes(hashlib.sha256(f'contract:{actor.id}:{body.idempotency_key}'.encode()).digest()[:8],'big',signed=True)
            connection.execute(select(func.pg_advisory_xact_lock(key)))
            previous=connection.execute(select(Request.__table__).where(Request.decided_by==actor.id,
                Request.decision_key==body.idempotency_key)).mappings().first()
            if previous:
                if previous['decision_fingerprint']!=fingerprint:raise HTTPException(409,'La clave contiene otra decisión')
                return cls._request_json(previous,connection)
            info=cls._request(connection,request_id);cls.lock_company(connection,info['tenant_id'])
            sub=cls._subscription(connection,info['tenant_id'],lock=True)
            row=cls._request(connection,request_id,lock=True)
            if row['estado']!='PENDIENTE' or row['lock_version']!=body.expected_version:
                raise HTTPException(409,'La solicitud cambió; actualiza antes de decidir')
            term_id=None
            requires_payment=False
            if body.decision=='APROBADA':
                if cls.now()>=row['quote_expires_at']:raise HTTPException(409,'La cotización venció; requiere nueva aceptación')
                version=cls._version(connection,row['version_id'],lock=True);payload=row['quote_payload']
                if payload['conditions']!=cls._conditions(version) or payload['schedule']!=cls._schedule(connection,sub,row['kind']):
                    raise HTTPException(409,'La base o fechas cambiaron; requiere nueva aceptación')
                active=connection.execute(select(func.count(Branch.id)).where(Branch.tenant_id==row['tenant_id'],Branch.activa.is_(True))).scalar_one()
                if active>version['max_sucursales']:raise HTTPException(409,'Reduce las sucursales activas antes de aprobar este límite')
                schedule=payload['schedule']
                requires_payment=payload.get('billing_mode')=='PAYPAL' and version['precio_mensual_bob']>0
                if not requires_payment and sub['estado']=='PENDIENTE':
                    connection.execute(update(Sub).where(Sub.id==sub['id']).values(estado='ACTIVA',
                        fecha_inicio=date.fromisoformat(schedule['fecha_inicio']),fecha_vencimiento=date.fromisoformat(schedule['fecha_fin'])-timedelta(days=1)))
                if not requires_payment:
                    term_id=connection.execute(insert(Term).values(tenant_id=row['tenant_id'],subscription_id=sub['id'],
                    version_id=row['version_id'],request_id=row['id'],fecha_inicio=date.fromisoformat(schedule['fecha_inicio']),
                    fecha_fin=date.fromisoformat(schedule['fecha_fin']),anchor_day=schedule['anchor_day'],
                    precio_mensual_bob=version['precio_mensual_bob'],moneda='BOB',periodicidad='MENSUAL',
                    max_sucursales=version['max_sucursales'],features=feature_values(version),origen='MANUAL',
                    accepted_by=row['accepted_by'],accepted_at=row['accepted_at'],approved_by=actor.id,approved_at=cls.now()).returning(Term.id)).scalar_one()
            result={'term_id':str(term_id) if term_id else None,'approval_mode':'MANUAL','payment_status':'NO_VERIFICADO'}
            if body.decision=='APROBADA' and requires_payment:result['payment_status']='PENDIENTE'
            elif body.decision=='APROBADA' and row['quote_payload'].get('billing_mode')=='PAYPAL':result['payment_status']='SIN_CARGO'
            connection.execute(update(Request).where(Request.id==row['id']).values(estado=body.decision,lock_version=row['lock_version']+1,
                decision_key=body.idempotency_key,decision_fingerprint=fingerprint,decided_by=actor.id,decided_at=cls.now(),
                reason=body.reason,decision_result=result))
            if requires_payment:
                from app.services.subscription_billing_service import SubscriptionBillingService
                SubscriptionBillingService.authorize(db,cls._request(connection,row['id']),actor)
            after={'id':str(row['id']),'tenant_id':str(row['tenant_id']),'estado':body.decision,**result}
            PlatformAuditService.record(db,actor,'CONTRACT_DECISION','CONTRACT_REQUEST',row['id'],
                {'id':str(row['id']),'estado':'PENDIENTE'},after,body.reason)
            return cls._request_json(cls._request(connection,row['id']),connection)

    @classmethod
    def cancel(cls,db,actor,request_id,body):
        actor=cls._company(db,actor)
        with cls._write(db) as connection:
            cls.lock_company(connection,actor.tenant_id);cls._subscription(connection,actor.tenant_id,lock=True)
            row=cls._request(connection,request_id,actor.tenant_id,lock=True)
            if row['estado']=='CANCELADA':return cls._request_json(row,connection)
            if row['estado']!='PENDIENTE' or row['lock_version']!=body.expected_version:raise HTTPException(409,'La solicitud cambió')
            connection.execute(update(Request).where(Request.id==row['id']).values(estado='CANCELADA',lock_version=row['lock_version']+1,
                decided_by=actor.id,decided_at=cls.now(),reason=body.reason))
            AuditService.record(db,'Solicitud de suscripción cancelada','Suscripciones',actor)
            return cls._request_json(cls._request(connection,request_id,actor.tenant_id),connection)

    @classmethod
    def list_requests(cls,db,actor,platform=False,limit=50,offset=0):
        if platform:PlanCatalogService._authorize(db,actor)
        else:actor=cls._company(db,actor)
        query=select(Request.__table__)
        if not platform:query=query.where(Request.tenant_id==actor.tenant_id)
        return [cls._request_json(row,db.connection()) for row in db.connection().execute(query.order_by(Request.accepted_at.desc(),Request.id)
            .limit(limit).offset(offset)).mappings()]

    @staticmethod
    def get_current_term(db,subscription,at=None):
        sub_id=subscription['id'] if isinstance(subscription,dict) else subscription.id
        tenant_id=subscription['tenant_id'] if isinstance(subscription,dict) else subscription.tenant_id
        if db.info.get('tenant_scope') and db.info['tenant_scope']!=tenant_id:
            raise HTTPException(403,'Contrato de otra empresa')
        row=db.connection().execute(select(Term.__table__).where(Term.subscription_id==sub_id,Term.tenant_id==tenant_id,
            Term.fecha_inicio<=(at or business_today()),Term.fecha_fin>(at or business_today()))).mappings().first()
        if row and row['origen']=='PAYPAL':
            from app.models.subscription_payment import SubscriptionPayment
            if not db.connection().execute(select(SubscriptionPayment.id).where(SubscriptionPayment.request_id==row['request_id'],
                    SubscriptionPayment.tenant_id==tenant_id,SubscriptionPayment.state=='APPLIED')).first():return None
        return dict(row) if row else None

    @classmethod
    def contract(cls,db,actor):
        actor=cls._company(db,actor);connection=db.connection();sub=cls._subscription(connection,actor.tenant_id)
        terms=[] if not sub else [dict(row) for row in connection.execute(select(Term.__table__).where(Term.subscription_id==sub['id'],Term.tenant_id==actor.tenant_id).order_by(Term.fecha_inicio)).mappings()]
        current=cls.get_current_term(db,sub) if sub else None
        return {'subscription_id':str(sub['id']) if sub else None,'tenant_id':str(actor.tenant_id),
            'estado_administrativo':sub['estado'] if sub else 'SIN_CONTRATO','terms':terms,
            'current_term':current,'approval_mode':'MANUAL','payment_status':'VERIFICADO' if current and current['origen']=='PAYPAL' else 'NO_VERIFICADO'}
