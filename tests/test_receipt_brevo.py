import base64,json
from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError,URLError
from pydantic import SecretStr
import pytest
from tests.test_tenant_sales import shops
from tests.test_receipt_documents import sale
from app.config import settings
from app.services import receipt_email_service as email
from app.services.receipt_document_service import get_receipt_document
from app.services.receipt_pdf_service import render_receipt_pdf

@pytest.fixture
def brevo(monkeypatch):
    monkeypatch.setattr(settings,'RECEIPT_EMAIL_PROVIDER','brevo')
    monkeypatch.setattr(settings,'RECEIPT_EMAIL_ENABLED',True)
    monkeypatch.setattr(settings,'BREVO_API_KEY',SecretStr('test-private-key'))
    monkeypatch.setattr(settings,'SMTP_FROM','sender@example.com')
    monkeypatch.setattr(settings,'SMTP_PASSWORD',None)
    return monkeypatch

def test_brevo_exact_attachment_and_no_smtp_fallback(db_session,shops,brevo):
    import uuid
    actor=shops['a'][0];row=sale(db_session,shops['a'])
    doc=get_receipt_document(db_session,actor,'sale',row.id);pdf=render_receipt_pdf(doc)
    response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    response.status=201;response.read.return_value=b'{"messageId":"provider-id"}'
    provider=Mock(return_value=response);brevo.setattr(email,'urlopen',provider)
    smtp=Mock(side_effect=AssertionError('SMTP forbidden'));brevo.setattr(email,'smtp_send',smtp)
    key=uuid.uuid4();first=email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key)
    replay=email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key)
    assert first.status=='ACCEPTED' and first==replay and provider.call_count==1
    request=provider.call_args.args[0];body=json.loads(request.data)
    assert request.full_url=='https://api.brevo.com/v3/smtp/email'
    assert body['to']==[{'email':'qa@example.com'}] and len(body['attachment'])==1
    assert base64.b64decode(body['attachment'][0]['content'])==pdf
    assert b'test-private-key' not in request.data and smtp.call_count==0 and email.email_enabled()

@pytest.mark.parametrize('error,status',[
    (HTTPError('https://api.brevo.com',401,'private text',{},BytesIO(b'private')), 'FAILED'),
    (HTTPError('https://api.brevo.com',429,'private text',{},BytesIO(b'private')), 'FAILED'),
    (HTTPError('https://api.brevo.com',503,'private text',{},BytesIO(b'private')), 'UNKNOWN'),
    (URLError('private network text'),'UNKNOWN')])
def test_brevo_errors_not_retried_or_disclosed(db_session,shops,brevo,error,status,caplog):
    import uuid
    actor=shops['a'][0];row=sale(db_session,shops['a'])
    provider=Mock(side_effect=error);brevo.setattr(email,'urlopen',provider)
    key=uuid.uuid4();first=email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key)
    assert first.status==status
    assert email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key)==first
    assert provider.call_count==1 and 'private text' not in caplog.text and 'private network text' not in caplog.text

def test_brevo_missing_key_disables_email(brevo):
    brevo.setattr(settings,'BREVO_API_KEY',None)
    assert not email.email_enabled()
