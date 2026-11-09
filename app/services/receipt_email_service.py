"""Manual SMTP delivery, separate from the already committed commercial action."""
import hashlib
import json
import re
import smtplib
import ssl
import logging
from datetime import datetime,timedelta,timezone
from email.message import EmailMessage
from email.utils import formataddr
from pydantic import EmailStr,TypeAdapter,ValidationError
from fastapi import HTTPException
from sqlalchemy import text
from app.config import settings
from app.models.receipt_email_delivery import ReceiptEmailDelivery as Delivery
from app.schemas.receipt import ReceiptEmailResult
from app.services.receipt_document_service import get_receipt_document
from app.services.receipt_pdf_service import render_receipt_pdf

logger=logging.getLogger(__name__)


class EmailRejected(Exception):
    """Provider definitively refused the message before accepting its DATA."""


def now():return datetime.now(timezone.utc)


def _address(value):
    # Reject all controls rather than allowing email-validator to trim them.
    if not isinstance(value,str) or any(ord(char)<32 or ord(char)==127 for char in value):
        raise HTTPException(422,'Correo destinatario inválido')
    try:return str(TypeAdapter(EmailStr).validate_python(value))
    except ValidationError:raise HTTPException(422,'Correo destinatario inválido') from None


def email_enabled():
    if not (settings.RECEIPT_EMAIL_ENABLED and settings.SMTP_HOST and settings.SMTP_USERNAME
            and settings.SMTP_PASSWORD and settings.SMTP_PASSWORD.get_secret_value() and settings.SMTP_FROM):return False
    try:_address(settings.SMTP_FROM)
    except HTTPException:return False
    return True


def smtp_send(document,recipient,pdf):
    """No provider exception text is persisted: it can include credentials/body."""
    message=EmailMessage()
    company=' '.join(document.company.name.split())[:120]
    message['From']=formataddr(('FashionStore · '+company,settings.SMTP_FROM))
    message['To']=recipient
    message['Subject']=document.title+' · '+re.sub(r'[\r\n\x00-\x1f]',' ',document.number)
    message.set_content('Adjuntamos tu comprobante de '+company+'.\n'
        +document.payment_status+'.\nEl documento refleja el estado registrado de la operación.')
    filename=re.sub(r'[^a-zA-Z0-9_-]','_',document.number)[:90]
    message.add_attachment(pdf,maintype='application',subtype='pdf',filename='comprobante-'+filename+'.pdf')
    connection=None
    try:
        context=ssl.create_default_context()
        if settings.SMTP_PORT==465:
            connection=smtplib.SMTP_SSL(settings.SMTP_HOST,465,timeout=15,context=context)
        else:
            connection=smtplib.SMTP(settings.SMTP_HOST,587,timeout=15)
            connection.ehlo();connection.starttls(context=context);connection.ehlo()
        connection.login(settings.SMTP_USERNAME,settings.SMTP_PASSWORD.get_secret_value())
    except Exception as error:
        # Exception class is diagnostic; provider text may contain private data.
        logger.warning('Receipt SMTP connection/auth rejected: %s',type(error).__name__)
        if connection:
            try:connection.close()
            except Exception:pass
        raise EmailRejected() from None
    try:
        refused=connection.send_message(message,from_addr=settings.SMTP_FROM,to_addrs=[recipient])
        if refused:raise EmailRejected()
    except (smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError,smtplib.SMTPNotSupportedError):
        raise EmailRejected() from None
    except Exception as error:
        logger.warning('Receipt SMTP acceptance uncertain: %s',type(error).__name__)
        raise
    finally:
        # A failed QUIT after acceptance must never cause a duplicate resend.
        try:connection.quit()
        except Exception:
            try:connection.close()
            except Exception:pass


def _result(row):
    messages={'PENDING':'Envío en curso. Consulta de nuevo con la misma solicitud.',
        'ACCEPTED':'Correo aceptado para envío. Revisa también Spam.',
        'FAILED':'No se pudo enviar el correo. La operación comercial sigue registrada.',
        'UNKNOWN':'No se pudo confirmar el envío. Revisa tu correo antes de reenviarlo.'}
    return ReceiptEmailResult(operation_id=row.operation_id,status=row.status,recipient=row.recipient,message=messages[row.status])


def send_receipt_email(db,actor,kind,entity_id,recipient,operation_id) -> ReceiptEmailResult:
    recipient=_address(recipient)
    # This API must not commit incidental sale/ledger changes from a caller.
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409,'Finaliza la operación comercial antes de enviar el comprobante')
    document=get_receipt_document(db,actor,kind,entity_id)
    request_hash=hashlib.sha256(json.dumps([kind,str(entity_id),recipient],ensure_ascii=False).encode()).hexdigest()
    # Short actor lock reserves an attempt durably; release BEFORE SMTP.
    # Other requests see PENDING and never send in parallel for the same key.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
        {'key':f'receipt-email:{actor.tenant_id}:{actor.id}'})
    existing=db.query(Delivery).filter_by(tenant_id=actor.tenant_id,actor_id=actor.id,operation_id=operation_id).first()
    instant=now()
    if existing:
        if existing.request_hash!=request_hash:raise HTTPException(409,'La solicitud ya pertenece a otro envío')
        if existing.status=='PENDING' and existing.updated_at<=instant-timedelta(minutes=2):
            existing.status='UNKNOWN';existing.updated_at=instant
        result=_result(existing);db.commit();return result
    if not email_enabled():raise HTTPException(503,'Correo no configurado; puedes descargar el PDF')
    attempts=db.query(Delivery).filter(Delivery.tenant_id==actor.tenant_id,Delivery.actor_id==actor.id)
    if attempts.filter(Delivery.created_at>instant-timedelta(minutes=10)).count()>=5:
        raise HTTPException(429,'Límite de 5 intentos en 10 minutos; vuelve a intentarlo más tarde')
    if attempts.filter(Delivery.kind==kind,Delivery.entity_id==entity_id,Delivery.recipient==recipient,
                       Delivery.created_at>instant-timedelta(seconds=60)).first():
        raise HTTPException(429,'Espera 60 segundos antes de reenviar el mismo comprobante')
    pdf=render_receipt_pdf(document)
    row=Delivery(tenant_id=actor.tenant_id,actor_id=actor.id,operation_id=operation_id,kind=kind,
        entity_id=entity_id,recipient=recipient,request_hash=request_hash,
        pdf_hash=hashlib.sha256(pdf).hexdigest(),status='PENDING',created_at=instant,updated_at=instant)
    db.add(row);db.commit()
    try:
        smtp_send(document,recipient,pdf);state='ACCEPTED'
    except EmailRejected:state='FAILED'
    except Exception:state='UNKNOWN'
    # KeyboardInterrupt/process failure intentionally leaves durable PENDING.
    # A replay later reports UNKNOWN; it cannot automatically resend.
    row.status=state;row.updated_at=now();db.commit()
    return _result(row)
