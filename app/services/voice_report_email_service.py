"""Idempotent manual email for a report snapshot, using existing mail ledger."""
import hashlib
import json
import re
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import text

from app.models.receipt_email_delivery import ReceiptEmailDelivery as Delivery
from app.schemas.receipt import ReceiptEmailResult
from app.services.receipt_email_service import EmailRejected, _address, deliver_files, email_enabled, now
from app.services.voice_report_pdf_service import render_voice_report_pdf
from app.services.voice_report_service import VoiceReportService


def send_voice_report_email(db, actor, request):
    recipient=_address(str(request.recipient))
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409,'Finaliza la operación actual antes de enviar el reporte')
    tenant_id=actor.tenant_id
    if not tenant_id:raise HTTPException(403,'La cuenta no pertenece a una empresa')
    snapshot={'pregunta':request.pregunta,'interpretacion':request.interpretacion,
        'columnas':request.columnas,'filas':request.filas}
    canonical=json.dumps([recipient,snapshot],ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
    request_hash=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
        {'key':f'report-email:{tenant_id}:{actor.id}'})
    existing=db.query(Delivery).filter_by(tenant_id=tenant_id,actor_id=actor.id,operation_id=request.operation_id).first()
    instant=now()
    if existing:
        if existing.kind!='voice_report' or existing.request_hash!=request_hash:
            raise HTTPException(409,'Este intento ya está asociado a otro envío')
        if existing.status=='PENDING' and existing.updated_at<=instant-timedelta(minutes=2):
            existing.status='UNKNOWN';existing.updated_at=instant
        result=_result(existing);db.commit();return result
    if not email_enabled():raise HTTPException(503,'Correo no configurado; descarga el PDF o Excel')
    attempts=db.query(Delivery).filter(Delivery.tenant_id==tenant_id,Delivery.actor_id==actor.id)
    if attempts.filter(Delivery.created_at>instant-timedelta(minutes=10)).count()>=5:
        raise HTTPException(429,'Límite de 5 correos en 10 minutos; vuelve a intentarlo más tarde')
    pdf=render_voice_report_pdf(request.pregunta,request.interpretacion,request.columnas,request.filas)
    excel=VoiceReportService.generate_excel_report(request.pregunta,request.interpretacion,
        request.columnas,request.filas)
    if len(pdf)+len(excel)>8_000_000:
        raise HTTPException(413,'El reporte es demasiado grande para enviarlo por correo; reduce las filas')
    attachment_hash=hashlib.sha256(pdf+b'\0'+excel).hexdigest()
    row=Delivery(tenant_id=tenant_id,actor_id=actor.id,operation_id=request.operation_id,
        kind='voice_report',entity_id=request.operation_id,recipient=recipient,
        request_hash=request_hash,pdf_hash=attachment_hash,status='PENDING',created_at=instant,updated_at=instant)
    db.add(row);db.commit()
    company='FashionStore'
    if getattr(actor,'tenant',None):company=actor.tenant.nombre or company
    subject='Reporte FashionStore · '+re.sub(r'[\r\n\x00-\x1f]',' ',request.pregunta)[:120]
    try:
        deliver_files(recipient,company,subject,
            f'Adjuntamos el reporte solicitado para {company}. Incluye el PDF y el Excel con los mismos datos que se mostraron en pantalla.',
            [('reporte-fashionstore.pdf','application/pdf',pdf),
             ('reporte-fashionstore.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',excel)])
        state='ACCEPTED'
    except EmailRejected:state='FAILED'
    except Exception:state='UNKNOWN'
    row.status=state;row.updated_at=now();db.commit()
    return _result(row)


def _result(row):
    messages={'PENDING':'Envío en curso. Consulta de nuevo con la misma solicitud.',
        'ACCEPTED':'Reporte aceptado para envío. Revisa también Spam.',
        'FAILED':'No se pudo enviar el reporte; puedes descargar los archivos.',
        'UNKNOWN':'No se pudo confirmar el envío. Revisa tu correo antes de reenviarlo.'}
    return ReceiptEmailResult(operation_id=row.operation_id,status=row.status,
        recipient=row.recipient,message=messages[row.status])
