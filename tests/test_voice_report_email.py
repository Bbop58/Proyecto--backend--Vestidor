import uuid
import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock
from io import BytesIO

import pytest
from fastapi import HTTPException

from tests.test_tenant_sales import shops
from app.models.receipt_email_delivery import ReceiptEmailDelivery
from app.schemas.voice_report import VoiceReportEmailRequest
from app.services import voice_report_email_service as service
from app.services import receipt_email_service as transport
from app.config import settings


class _Query:
    def __init__(self,rows):self.rows=rows
    def filter_by(self,**kwargs):
        return _Query([row for row in self.rows if all(getattr(row,key)==value for key,value in kwargs.items())])
    def filter(self,*_expressions):return self
    def first(self):return self.rows[0] if self.rows else None
    def count(self):return len(self.rows)


class _Session:
    def __init__(self):self.rows=[];self.new=[];self.dirty=[];self.deleted=[]
    def execute(self,*_args,**_kwargs):return None
    def query(self,_model):return _Query(self.rows)
    def add(self,row):self.rows.append(row)
    def commit(self):return None


def payload(operation_id=None, recipient='qa@example.com'):
    return VoiceReportEmailRequest(
        recipient=recipient,
        operation_id=operation_id or uuid.uuid4(),
        pregunta='Ventas por sucursal',
        interpretacion='Resumen de ventas',
        columnas=['sucursal', 'ingreso_total_bs'],
        filas=[{'sucursal': 'Sucursal Central', 'ingreso_total_bs': 1250.5}],
    )


def test_report_email_attaches_matching_pdf_and_excel_once(db_session, shops, monkeypatch):
    actor=shops['a'][0]
    deliver=Mock()
    monkeypatch.setattr(service,'email_enabled',lambda:True)
    monkeypatch.setattr(service,'deliver_files',deliver)
    request=payload()
    first=service.send_voice_report_email(db_session,actor,request)
    replay=service.send_voice_report_email(db_session,actor,request)
    assert first.status=='ACCEPTED' and replay==first
    assert deliver.call_count==1
    files=deliver.call_args.args[4]
    assert [item[0] for item in files]==['reporte-fashionstore.pdf','reporte-fashionstore.xlsx']
    assert files[0][2].startswith(b'%PDF-')
    assert files[1][2][:2]==b'PK'
    row=db_session.query(ReceiptEmailDelivery).filter_by(operation_id=request.operation_id).one()
    assert row.kind=='voice_report' and row.status=='ACCEPTED'


def test_report_email_same_key_with_changed_recipient_is_conflict(db_session, shops, monkeypatch):
    actor=shops['a'][0];deliver=Mock()
    monkeypatch.setattr(service,'email_enabled',lambda:True)
    monkeypatch.setattr(service,'deliver_files',deliver)
    operation_id=uuid.uuid4()
    service.send_voice_report_email(db_session,actor,payload(operation_id))
    with pytest.raises(HTTPException) as error:
        service.send_voice_report_email(db_session,actor,payload(operation_id,'other@example.com'))
    assert error.value.status_code==409 and deliver.call_count==1


def test_report_email_unconfigured_does_not_reserve_attempt(db_session, shops, monkeypatch):
    monkeypatch.setattr(service,'email_enabled',lambda:False)
    with pytest.raises(HTTPException) as error:
        service.send_voice_report_email(db_session,shops['a'][0],payload())
    assert error.value.status_code==503
    assert db_session.query(ReceiptEmailDelivery).count()==0


def test_report_email_requires_bounded_and_valid_snapshot():
    with pytest.raises(Exception):
        VoiceReportEmailRequest(recipient='bad',operation_id=uuid.uuid4(),pregunta='ok',
            columnas=['x'],filas=[])
    with pytest.raises(Exception):
        VoiceReportEmailRequest(recipient='a@example.com',operation_id=uuid.uuid4(),
            pregunta='ventas',columnas=['x']*31,filas=[])
    with pytest.raises(Exception):
        VoiceReportEmailRequest(recipient='a@example.com',operation_id=uuid.uuid4(),
            pregunta='ventas',columnas=['x'],filas=[{'x':'x'*4001}])


def test_brevo_transport_sends_both_report_files(monkeypatch):
    from pydantic import SecretStr
    for name,value in {'RECEIPT_EMAIL_PROVIDER':'brevo','RECEIPT_EMAIL_ENABLED':True,
                       'BREVO_API_KEY':SecretStr('private-test-key'),'SMTP_FROM':'sender@example.com'}.items():
        monkeypatch.setattr(settings,name,value)
    response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    response.status=201;response.read.return_value=b'{"messageId":"provider-id"}'
    provider=Mock(return_value=response);monkeypatch.setattr(transport,'urlopen',provider)
    transport.deliver_files('qa@example.com','UrbanStyle','Reporte de prueba','Adjuntos del reporte',[
        ('reporte.pdf','application/pdf',b'%PDF-report'),
        ('reporte.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',b'PK-report'),
    ])
    request=provider.call_args.args[0]
    body=json.loads(request.data)
    assert [a['name'] for a in body['attachment']]==['reporte.pdf','reporte.xlsx']
    assert [base64.b64decode(a['content']) for a in body['attachment']]==[b'%PDF-report',b'PK-report']
    assert b'private-test-key' not in request.data


def test_report_email_idempotency_without_database_side_effects(monkeypatch):
    actor=SimpleNamespace(tenant_id=uuid.uuid4(),id=uuid.uuid4(),tenant=SimpleNamespace(nombre='UrbanStyle'))
    db=_Session();deliver=Mock()
    monkeypatch.setattr(service,'email_enabled',lambda:True)
    monkeypatch.setattr(service,'deliver_files',deliver)
    monkeypatch.setattr(service,'render_voice_report_pdf',lambda *_args:b'%PDF-report')
    monkeypatch.setattr(service.VoiceReportService,'generate_excel_report',lambda *_args:b'PK-report')
    request=payload()
    first=service.send_voice_report_email(db,actor,request)
    replay=service.send_voice_report_email(db,actor,request)
    assert first.status=='ACCEPTED' and replay==first and deliver.call_count==1
    assert len(db.rows)==1 and db.rows[0].kind=='voice_report'
