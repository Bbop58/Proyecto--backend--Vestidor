"""Platform billing, with durable provider identity and explicit compensation."""
import hashlib,json,uuid
from datetime import datetime,timedelta,timezone,date
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlparse
from fastapi import HTTPException
from sqlalchemy import select,insert,update,func,and_,or_
from app.config import settings
from app.core.money import money
from app.core.billing_calendar import business_today
from app.models.subscription_payment import SubscriptionPayment as Payment,SubscriptionPaymentEvent as Event
from app.models.subscription_term import SubscriptionTerm as Term
from app.models.tenant import Tenant
from app.models.branch import Branch
from app.models.payment_intent import PaymentIntent
from app.services.subscription_contract_service import SubscriptionContractService as Contract
from app.services.paypal_service import PayPalService
from app.services.payment_flow_service import PaymentFlowService


class SubscriptionBillingService:
    @staticmethod
    def _authorize(db,actor):
        from app.models import User,Role,Permission
        from app.models.role_permission import role_permissions
        row=db.connection().execute(select(User.id).join(Tenant,Tenant.id==User.tenant_id).join(Role,Role.id==User.role_id)
            .join(role_permissions,role_permissions.c.role_id==Role.id).join(Permission,Permission.id==role_permissions.c.permission_id)
            .where(User.id==actor.id,User.tenant_id==actor.tenant_id,User.is_active.is_(True),User.is_verified.is_(True),
                Tenant.es_operadora.is_(False),Role.name!='superadmin_plataforma',Permission.code=='subscriptions.manage',
                or_(Role.tenant_id==actor.tenant_id,and_(Role.tenant_id.is_(None),Role.name.in_(['admin','encargado','cajero','cliente']))))).first()
        if not row:raise HTTPException(403,'Se requiere autorización empresarial vigente para facturación')

    @staticmethod
    def credentials(payment=None,db=None):
        cid=settings.PAYPAL_CLIENT_ID.strip();secret=settings.PAYPAL_CLIENT_SECRET.strip();mode=settings.PAYPAL_MODE
        if not cid or not secret or mode not in ('sandbox','live'):
            raise HTTPException(503,'Configura el PayPal de plataforma para suscripciones')
        result={'client_id':cid,'client_secret':secret,'mode':mode}
        if db is not None:
            from app.models.company_paypal_credential import CompanyPayPalCredential
            if db.connection().execute(select(CompanyPayPalCredential.id).where(CompanyPayPalCredential.client_id==cid)).first():
                raise HTTPException(503,'La plataforma debe usar una aplicación distinta de las empresas')
        if payment and (payment['merchant_key']!=PaymentFlowService.merchant(result) or payment['provider_mode']!=mode):
            raise HTTPException(409,'La cuenta original de plataforma cambió; conserva la orden para conciliación')
        return result

    @staticmethod
    def _key(row,operation):return str(uuid.uuid5(row['id'],'subscription:'+operation))

    @staticmethod
    def _payment(connection,payment_id,tenant_id=None,lock=False):
        query=select(Payment.__table__).where(Payment.id==payment_id)
        if tenant_id is not None:query=query.where(Payment.tenant_id==tenant_id)
        row=connection.execute(query.with_for_update() if lock else query).mappings().first()
        if not row:raise HTTPException(404,'Pago de suscripción no disponible')
        return dict(row)

    @classmethod
    def _lock(cls,db,payment_id,actor=None):
        connection=db.connection()
        row=cls._payment(connection,payment_id,actor.tenant_id if actor else None)
        from app.core.payment_evidence import lock_contract_tables
        lock_contract_tables(connection)
        Contract.lock_company(connection,row['tenant_id'])
        Contract._subscription(connection,row['tenant_id'],lock=True)
        return cls._payment(connection,payment_id,row['tenant_id'],lock=True)

    @staticmethod
    def _set(db,row,**values):
        values['updated_at']=Contract.now()
        db.connection().execute(update(Payment).where(Payment.id==row['id']).values(**values))
        return {**row,**values}

    @staticmethod
    def summary(row):
        return {key:row[key] for key in ('id','request_id','version_id','state','amount_bob','amount_usd','exchange_rate',
            'provider_mode','provider_order_id','capture_id','refund_id','expires_at','approve_url')}

    @classmethod
    def authorize(cls,db,request,actor):
        now=Contract.now()
        pending=db.connection().execute(select(Payment.id).where(Payment.subscription_id==request['subscription_id'],
            or_(Payment.state.in_(['CAPTURING','CAPTURED','REFUND_PENDING','REVIEW']),
                and_(Payment.state.in_(['READY','CREATING','CREATED']),Payment.expires_at>now)))).first()
        if pending:raise HTTPException(409,'Conciliar primero la autorización de pago pendiente de esta suscripción')
        creds=cls.credentials(db=db);rate=Decimal(str(settings.PAYPAL_EXCHANGE_RATE))
        amount=money(request['quote_payload']['conditions']['precio_mensual_bob'],positive=True)
        usd=money(PayPalService.convert_bob_to_usd(amount,rate=rate),positive=True)
        row=db.connection().execute(insert(Payment).values(request_id=request['id'],tenant_id=request['tenant_id'],
            subscription_id=request['subscription_id'],version_id=request['version_id'],approved_by=actor.id,
            approved_at=now,expires_at=now+timedelta(minutes=30),amount_bob=amount,amount_usd=usd,exchange_rate=rate,
            merchant_key=PaymentFlowService.merchant(creds),provider_mode=creds['mode']).returning(Payment.__table__)).mappings().one()
        return cls.summary(row)

    @classmethod
    def list_payments(cls,db,actor,platform=False,limit=50,offset=0):
        if platform:
            from app.services.plan_catalog_service import PlanCatalogService
            PlanCatalogService._authorize(db,actor)
        else:actor=Contract._company(db,actor);cls._authorize(db,actor)
        query=select(Payment.__table__)
        if not platform:query=query.where(Payment.tenant_id==actor.tenant_id)
        rows=db.connection().execute(query.order_by(Payment.created_at.desc(),Payment.id).limit(limit).offset(offset)).mappings()
        return [{**cls.summary(row),**({'tenant_id':row['tenant_id']} if platform else {})} for row in rows]

    @classmethod
    def for_request(cls,db,actor,request_id):
        actor=Contract._company(db,actor);Contract._request(db.connection(),request_id,actor.tenant_id)
        cls._authorize(db,actor)
        row=db.connection().execute(select(Payment.__table__).where(Payment.request_id==request_id,Payment.tenant_id==actor.tenant_id)).mappings().first()
        if not row:raise HTTPException(409,'Esta solicitud no tiene autorización de pago')
        return cls.summary(row)

    @classmethod
    def _eligible(cls,db,row,*,captured=False):
        connection=db.connection();request=Contract._request(connection,row['request_id'],row['tenant_id'])
        sub=Contract._subscription(connection,row['tenant_id'])
        company=connection.execute(select(Tenant.activo).where(Tenant.id==row['tenant_id'])).scalar_one()
        if not company or sub['estado']=='SUSPENDIDA':raise HTTPException(409,'La empresa o suscripción está suspendida')
        instant=row['captured_at'] if captured else Contract.now()
        if instant is None or instant>row['expires_at']:raise HTTPException(409,'La autorización de pago venció; requiere nueva aceptación')
        if request['estado']!='APROBADA':raise HTTPException(409,'La solicitud no está aprobada')
        if Contract._schedule(connection,sub,request['kind'])!=request['quote_payload']['schedule']:
            raise HTTPException(409,'El calendario cambió; no se pueden aplicar las condiciones aceptadas')
        limit=request['quote_payload']['conditions']['max_sucursales']
        active=connection.execute(select(func.count()).select_from(Branch).where(Branch.tenant_id==row['tenant_id'],Branch.activa.is_(True))).scalar_one()
        if active>limit:raise HTTPException(409,'El límite de sucursales ya no se cumple')
        return request,sub

    @classmethod
    def create(cls,db,actor,payment_id):
        actor=Contract._company(db,actor)
        cls._authorize(db,actor)
        try:
            row=cls._lock(db,payment_id,actor)
            if row['state'] in ('CREATED','CAPTURING','CAPTURED','APPLIED','REFUND_PENDING','REFUNDED','REVIEW'):
                return cls.summary(row)
            cls._eligible(db,row);creds=cls.credentials(row,db=db)
            if row['state'] not in ('READY','CREATING'):raise HTTPException(409,'Esta autorización ya no admite una orden')
            row=cls._set(db,row,state='CREATING');db.commit()
            row=cls._lock(db,payment_id,actor)
            if row['provider_order_id']:return cls.summary(row)
            # Recheck after waiting for another creator/decision. A timeout repeats this UUID.
            cls._eligible(db,row);creds=cls.credentials(row,db=db)
            target=settings.SUBSCRIPTION_WEB_URL
            cls._authorize(db,actor)
            if urlparse(target).scheme!='https' and urlparse(target).hostname not in ('localhost','127.0.0.1'):
                raise HTTPException(503,'Configura la URL de retorno de suscripciones')
            result=PayPalService.create_order(monto_bob=float(row['amount_bob']),amount_usd=row['amount_usd'],
                custom_id=str(row['id']),request_id=cls._key(row,'create'),descripcion='Suscripción FashionStore',
                brand_name='FashionStore SaaS',return_url=target,cancel_url=target,**creds)
            link=result.get('approve_url');host='www.sandbox.paypal.com' if row['provider_mode']=='sandbox' else 'www.paypal.com'
            if not link or urlparse(link).scheme!='https' or urlparse(link).hostname!=host:
                raise HTTPException(502,'La pasarela no devolvió un enlace de aprobación válido')
            order=PayPalService._id(result['order_id'])
            row=cls._set(db,row,state='CREATED',provider_order_id=order,approve_url=link);db.commit()
            return cls.summary(row)
        except Exception:db.rollback();raise

    @classmethod
    def capture(cls,db,actor,payment_id):
        actor=Contract._company(db,actor)
        cls._authorize(db,actor)
        return cls._reconcile(db,payment_id,actor=actor,allow_charge=True)

    @classmethod
    def _refund(cls,db,row,creds):
        if row['state']=='CAPTURED':row=cls._set(db,row,state='REFUND_PENDING');db.commit();row=cls._lock(db,row['id'])
        if row['state']=='REFUNDED':return cls.summary(row)
        if row['state']!='REFUND_PENDING':raise HTTPException(409,'Reembolso requiere conciliación')
        result=(PayPalService.get_refund_details(row['refund_id'],**creds) if row['refund_id'] else
            PayPalService.refund_capture(row['capture_id'],request_id=cls._key(row,'refund'),**creds))
        refund_id=PayPalService._id(result['id']) if result.get('id') else None
        if refund_id:row=cls._set(db,row,refund_id=refund_id)
        if (refund_id and result.get('status')=='COMPLETED' and result.get('amount',{}).get('currency_code')=='USD'
                and money(result['amount']['value'])==money(row['amount_usd'])):
            row=cls._set(db,row,state='REFUNDED')
        db.commit();return cls.summary(row)

    @classmethod
    def _reconcile(cls,db,payment_id,actor=None,allow_charge=False):
        try:
            row=cls._lock(db,payment_id,actor)
            if row['state'] in ('APPLIED','REFUNDED','REVIEW','EXPIRED'):return cls.summary(row)
            creds=cls.credentials(row,db=db)
            if row['state']=='REFUND_PENDING':return cls._refund(db,row,creds)
            if not row['provider_order_id']:raise HTTPException(409,'Primero crea la orden de esta autorización')
            if row['state']=='CREATED':row=cls._set(db,row,state='CAPTURING');db.commit();row=cls._lock(db,payment_id,actor)
            if row['state']=='APPLIED':return cls.summary(row)
            if row['state']=='CAPTURING':
                raw=PayPalService.get_order_details(row['provider_order_id'],**creds)
                pending_captures=[capture for unit in raw.get('purchase_units',[]) if isinstance(unit,dict)
                    for capture in unit.get('payments',{}).get('captures',[]) if isinstance(capture,dict) and capture.get('status')=='PENDING']
                if pending_captures:
                    raise HTTPException(409,'La captura está pendiente en PayPal; consulta la misma orden sin crear otro cargo')
                if raw.get('status')!='COMPLETED':
                    if not allow_charge:return cls.summary(row)
                    # User can reconcile completed payments after expiration, but never begin a late charge.
                    cls._eligible(db,row)
                    if actor:cls._authorize(db,actor)
                    if raw.get('status')!='APPROVED':raise HTTPException(409,'Aprueba primero el pago en PayPal')
                    raw=PayPalService.capture_order(row['provider_order_id'],request_id=cls._key(row,'capture'),**creds).get('raw_response',{})
                try:
                    capture_id=PaymentFlowService.verify_capture(SimpleNamespace(**row),raw)
                    capture=raw['purchase_units'][0]['payments']['captures'][0]
                    instant=datetime.fromisoformat(capture['create_time'].replace('Z','+00:00'))
                    if instant.tzinfo is None or instant<row['approved_at']-timedelta(seconds=5):raise ValueError()
                except (HTTPException,ValueError,KeyError,TypeError):
                    row=cls._set(db,row,state='REVIEW');db.commit()
                    raise HTTPException(409,'La evidencia de captura no coincide; requiere revisión sin nuevo cargo')
                from app.core.payment_evidence import capture_claimed_elsewhere
                if capture_claimed_elsewhere(db,capture_id,billing_id=row['id']):
                    cls._set(db,row,state='REVIEW');db.commit();raise HTTPException(409,'La captura ya tiene otra atribución')
                row=cls._set(db,row,state='CAPTURED',capture_id=capture_id,captured_at=instant);db.commit();row=cls._lock(db,payment_id,actor)
            if row['state']=='APPLIED':return cls.summary(row)
            if row['state']=='REFUND_PENDING':return cls._refund(db,row,creds)
            if row['state']!='CAPTURED':return cls.summary(row)
            try:request,sub=cls._eligible(db,row,captured=True)
            except HTTPException:
                return cls._refund(db,row,creds)
            conditions=request['quote_payload']['conditions'];schedule=request['quote_payload']['schedule']
            if sub['estado']=='PENDIENTE':
                from app.models.subscription import Subscription
                db.connection().execute(update(Subscription).where(Subscription.id==sub['id']).values(estado='ACTIVA',
                    fecha_inicio=date.fromisoformat(schedule['fecha_inicio']),fecha_vencimiento=date.fromisoformat(schedule['fecha_fin'])-timedelta(days=1)))
            elif sub['estado']=='VENCIDA':
                from app.models.subscription import Subscription
                db.connection().execute(update(Subscription).where(Subscription.id==sub['id']).values(estado='ACTIVA'))
            db.connection().execute(insert(Term).values(tenant_id=row['tenant_id'],subscription_id=row['subscription_id'],
                version_id=row['version_id'],request_id=row['request_id'],fecha_inicio=date.fromisoformat(schedule['fecha_inicio']),
                fecha_fin=date.fromisoformat(schedule['fecha_fin']),anchor_day=schedule['anchor_day'],precio_mensual_bob=row['amount_bob'],
                moneda='BOB',periodicidad='MENSUAL',max_sucursales=conditions['max_sucursales'],features=conditions['features'],
                origen='PAYPAL',accepted_by=request['accepted_by'],accepted_at=request['accepted_at'],approved_by=row['approved_by'],approved_at=row['approved_at']))
            row=cls._set(db,row,state='APPLIED');db.commit();return cls.summary(row)
        except Exception:db.rollback();raise

    @classmethod
    def webhook(cls,db,headers,event):
        creds=cls.credentials(db=db)
        PayPalService.verify_webhook_signature(headers,event,settings.PAYPAL_WEBHOOK_ID,**creds)
        event_id=event.get('id');kind=event.get('event_type');resource=event.get('resource',{})
        if not isinstance(event_id,str) or not 1<=len(event_id)<=128:raise HTTPException(400,'Evento inválido')
        if kind not in ('PAYMENT.CAPTURE.COMPLETED','PAYMENT.CAPTURE.REFUNDED','PAYMENT.CAPTURE.REVERSED'):return {'accepted':True}
        order_id=resource.get('supplementary_data',{}).get('related_ids',{}).get('order_id')
        row=db.connection().execute(select(Payment.__table__).where(Payment.provider_order_id==order_id)).mappings().first()
        if not row:return {'accepted':True}
        cls.credentials(row,db=db)
        fingerprint=Contract._hash(event)
        try:
            # Serialize duplicates before recording, including absent event rows.
            lock=int.from_bytes(hashlib.sha256(event_id.encode()).digest()[:8],'big',signed=True)
            db.connection().execute(select(func.pg_advisory_xact_lock(lock)))
            previous=db.connection().execute(select(Event.__table__).where(Event.event_id==event_id)).mappings().first()
            if previous:
                if previous['fingerprint']!=fingerprint:raise HTTPException(409,'El evento cambió de contenido')
                if previous['processed']:return {'accepted':True}
            else:
                db.connection().execute(insert(Event).values(event_id=event_id,fingerprint=fingerprint,payment_id=row['id'],event_type=kind))
            db.commit()
            if kind=='PAYMENT.CAPTURE.COMPLETED':
                result=cls._reconcile(db,row['id'],allow_charge=False)
                if result['state'] in ('CREATED','CAPTURING'):raise HTTPException(503,'La captura aún no está disponible para conciliar')
            else:
                locked=cls._lock(db,row['id']);raw=PayPalService.get_order_details(locked['provider_order_id'],**creds)
                captures=raw.get('purchase_units',[{}])[0].get('payments',{}).get('captures',[])
                reversed_capture=next((c for c in captures if c.get('id')==locked['capture_id'] and c.get('status') in ('REFUNDED','PARTIALLY_REFUNDED','REVERSED','DECLINED')),None)
                if not reversed_capture:raise HTTPException(409,'Reversión no confirmada por la orden original')
                if locked['state']=='APPLIED':cls._set(db,locked,state='REVIEW')
            db.connection().execute(update(Event).where(Event.event_id==event_id).values(processed=True));db.commit()
            return {'accepted':True}
        except Exception:db.rollback();raise
