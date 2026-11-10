"""Explicit platform provisioning; no commercial data projections or free contract."""
import hashlib,json,secrets,uuid,logging
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,insert,update,func
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from app.models.company_invitation import CompanyInvitation as Invite
from app.models.tenant import Tenant
from app.models.user import User
from app.models.role import Role
from app.services.platform_company_service import PlatformCompanyService as Companies
from app.services.platform_audit_service import PlatformAuditService
from app.services.transactional_mail import mail_available,send_invitation,invitation_url
from app.core.security import get_password_hash

def now():return datetime.now(timezone.utc)
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def _row(connection,company_id):
    return connection.execute(select(Invite.__table__).where(Invite.tenant_id==company_id)).mappings().first()
def _result(db,row):
    return {'company':Companies.get_company(db,db.info['_invitation_actor'],row['tenant_id']),
        'owner_email':row['owner_email'],'owner_name':row['owner_name'],'expires_at':row['expires_at'],
        'email_sent':row['email_sent'],'state':'ACEPTADA' if row['accepted_at'] else 'VENCIDA' if row['expires_at']<=now() else 'PENDIENTE'}
def _send(db,row,token):
    try:
        send_invitation(row['owner_email'],db.connection().execute(select(Tenant.nombre).where(Tenant.id==row['tenant_id'])).scalar_one(),invitation_url(token))
        sent=True
    except Exception as error:
        logging.getLogger(__name__).warning('Company invitation email failed: %s',type(error).__name__)
        sent=False
    db.connection().execute(update(Invite).where(Invite.id==row['id'],Invite.token_hash==digest(token)).values(email_sent=sent));db.commit()
    return _result(db,_row(db.connection(),row['tenant_id']))

def create(db,actor,body):
    Companies._authorize(db,actor);db.info['_invitation_actor']=actor
    fingerprint=digest(json.dumps(body.model_dump(mode='json'),sort_keys=True))
    try:
        c=db.connection();Companies._lock_operation(c,actor,body.operation_id)
        old=c.execute(select(Invite.__table__).where(Invite.operation_id==body.operation_id)).mappings().first()
        if old:
            if old['fingerprint']!=fingerprint or old['created_by']!=actor.id:raise HTTPException(409,'La operación ya contiene otra invitación')
            return _result(db,old)
        if not mail_available():raise HTTPException(503,'El envío de invitaciones no está configurado')
        if c.execute(select(User.id).where(func.lower(User.email)==str(body.owner_email).lower())).first():
            raise HTTPException(409,'Este correo ya pertenece a una cuenta; usa un propietario nuevo')
        company_id=uuid.uuid4();token=secrets.token_urlsafe(32)
        values=body.model_dump(include={'nombre','razon_social','nit_rut','slug','telefono'})
        c.execute(insert(Tenant).values(id=company_id,activo=False,es_operadora=False,platform_revision=0,
            email_contacto=str(body.owner_email).lower(),**values))
        c.execute(insert(Invite).values(tenant_id=company_id,created_by=actor.id,operation_id=body.operation_id,
            fingerprint=fingerprint,owner_email=str(body.owner_email).lower(),owner_name=body.owner_name,
            token_hash=digest(token),expires_at=now()+timedelta(hours=48)))
        result=Companies.get_company(db,actor,company_id)
        PlatformAuditService.record(db,actor,'COMPANY_INVITE','COMPANY',company_id,{},result,body.reason,
            body.operation_id,fingerprint,result)
        db.commit();return _send(db,_row(db.connection(),company_id),token)
    except IntegrityError:
        db.rollback();raise HTTPException(409,'La empresa, correo u operación ya están registrados') from None
    except Exception:
        db.rollback();raise

def list_invitations(db,actor):
    Companies._authorize(db,actor);db.info['_invitation_actor']=actor
    rows=db.connection().execute(select(Invite.__table__).order_by(Invite.created_at.desc()).limit(500)).mappings().all()
    return [_result(db,row) for row in rows]

def resend(db,actor,company_id,body):
    Companies._authorize(db,actor);db.info['_invitation_actor']=actor
    fingerprint=digest('resend:'+str(company_id))
    try:
        c=db.connection();Companies._lock_operation(c,actor,body.operation_id)
        previous=Companies._replay(c,actor,body.operation_id,fingerprint)
        if previous is not None:return _result(db,_row(c,company_id))
        row=c.execute(select(Invite.__table__).where(Invite.tenant_id==company_id).with_for_update()).mappings().first()
        if not row:raise HTTPException(404,'Invitación no disponible')
        if row['accepted_at']:raise HTTPException(409,'El propietario ya aceptó la invitación')
        if not mail_available():raise HTTPException(503,'El envío de invitaciones no está configurado')
        token=secrets.token_urlsafe(32)
        c.execute(update(Invite).where(Invite.id==row['id']).values(token_hash=digest(token),expires_at=now()+timedelta(hours=48),email_sent=False))
        PlatformAuditService.record(db,actor,'COMPANY_INVITE_RESEND','COMPANY',company_id,{}, {},
            'Reenvío de invitación de propietario',body.operation_id,fingerprint,{'id':str(company_id)})
        db.commit();return _send(db,_row(db.connection(),company_id),token)
    except Exception:
        db.rollback();raise

def _valid(connection,token,lock=False):
    value=token.get_secret_value()
    if not 32<=len(value)<=256:raise HTTPException(400,'Invitación inválida, vencida o ya utilizada; solicita un nuevo envío')
    query=select(Invite.__table__).where(Invite.token_hash==digest(value))
    row=connection.execute(query.with_for_update() if lock else query).mappings().first()
    if not row or row['accepted_at'] or row['expires_at']<=now():
        raise HTTPException(400,'Invitación inválida, vencida o ya utilizada; solicita un nuevo envío')
    return row

def preview(db,body):
    row=_valid(db.connection(),body.token)
    return {'company_name':db.connection().execute(select(Tenant.nombre).where(Tenant.id==row['tenant_id'])).scalar_one(),
        'owner_email':row['owner_email'],'owner_name':row['owner_name'],'expires_at':row['expires_at']}

def accept(db,body):
    try:
        if len(body.password.get_secret_value().encode('utf-8'))>72:
            raise HTTPException(422,'La contraseña supera el máximo de 72 bytes; usa una más corta')
        c=db.connection();row=_valid(c,body.token,True)
        company=c.execute(select(Tenant.__table__).where(Tenant.id==row['tenant_id'],Tenant.es_operadora.is_(False)).with_for_update()).mappings().first()
        if not company or (not company['activo'] and company['platform_revision']>0):
            raise HTTPException(409,'Empresa suspendida; solicita su habilitación al superadmin')
        # A bearer invitation can only provision the exact invited identity, once.
        if c.execute(select(User.id).where(func.lower(User.email)==row['owner_email'])).first():
            raise HTTPException(409,'El correo ya está registrado; solicita asistencia al superadmin')
        role=c.execute(select(Role.id).where(Role.tenant_id.is_(None),Role.name=='admin')).scalar_one_or_none()
        if role is None:raise HTTPException(503,'Falta configurar el rol administrador')
        c.execute(insert(User).values(tenant_id=row['tenant_id'],role_id=role,email=row['owner_email'],
            full_name=row['owner_name'],hashed_password=get_password_hash(body.password.get_secret_value()),is_active=True,is_verified=True))
        c.execute(update(Tenant).where(Tenant.id==row['tenant_id'],Tenant.es_operadora.is_(False)).values(activo=True,platform_revision=Tenant.platform_revision+1))
        c.execute(update(Invite).where(Invite.id==row['id']).values(accepted_at=now()))
        slug=c.execute(select(Tenant.slug).where(Tenant.id==row['tenant_id'])).scalar_one()
        db.commit();return {'message':'Correo verificado y administrador creado. Inicia sesión para contratar tu plan.','tenant_slug':slug}
    except IntegrityError:
        db.rollback();raise HTTPException(409,'El propietario ya está registrado; solicita asistencia') from None
    except Exception:
        db.rollback();raise
