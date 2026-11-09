import uuid
from datetime import timedelta
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from tests.test_tenant_sales import shops
from tests.test_receipt_documents import sale
from tests.test_commercial_invariants import committed_shop
from app.config import settings
from app.models.receipt_email_delivery import ReceiptEmailDelivery
from app.services import receipt_email_service as email


@pytest.fixture
def transport(monkeypatch):
    for name,value in {'RECEIPT_EMAIL_ENABLED':True,'SMTP_HOST':'smtp.example.com','SMTP_PORT':587,
                       'SMTP_USERNAME':'sender@example.com','SMTP_FROM':'sender@example.com'}.items():
        monkeypatch.setattr(settings,name,value)
    from pydantic import SecretStr
    monkeypatch.setattr(settings,'SMTP_PASSWORD',SecretStr('synthetic-test-secret'))
    sender=Mock();monkeypatch.setattr(email,'smtp_send',sender);return sender


def test_same_request_sends_once_and_returns_prior_result(db_session,shops,transport):
    actor,_,_=shops['a'];row=sale(db_session,shops['a']);key=uuid.uuid4()
    first=email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key)
    second=email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key)
    assert first==second and first.status=='ACCEPTED' and transport.call_count==1
    assert transport.call_args.args[2].startswith(b'%PDF-')
    assert db_session.query(ReceiptEmailDelivery).count()==1


def test_reusing_key_with_changed_recipient_is_conflict(db_session,shops,transport):
    actor,_,_=shops['a'];row=sale(db_session,shops['a']);key=uuid.uuid4()
    email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key)
    with pytest.raises(HTTPException) as error:
        email.send_receipt_email(db_session,actor,'sale',row.id,'other@example.com',key)
    assert error.value.status_code==409 and transport.call_count==1


def test_cross_company_request_never_contacts_smtp(db_session,shops,transport):
    row=sale(db_session,shops['a'])
    with pytest.raises(HTTPException) as error:
        email.send_receipt_email(db_session,shops['b'][0],'sale',row.id,'qa@example.com',uuid.uuid4())
    assert error.value.status_code==404;transport.assert_not_called()


def test_unconfigured_email_preserves_pdf_and_creates_no_attempt(db_session,shops,transport,monkeypatch):
    actor,_,_=shops['a'];row=sale(db_session,shops['a']);monkeypatch.setattr(settings,'RECEIPT_EMAIL_ENABLED',False)
    with pytest.raises(HTTPException) as error:
        email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',uuid.uuid4())
    assert error.value.status_code==503 and 'configurado' in error.value.detail
    assert db_session.query(ReceiptEmailDelivery).count()==0;transport.assert_not_called()


def test_document_cooldown_and_user_limit(db_session,shops,transport,monkeypatch):
    actor,_,_=shops['a'];row=sale(db_session,shops['a']);instant=email.now()
    monkeypatch.setattr(email,'now',lambda:instant)
    email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',uuid.uuid4())
    with pytest.raises(HTTPException) as error:
        email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',uuid.uuid4())
    assert error.value.status_code==429
    for index in range(1,5):
        monkeypatch.setattr(email,'now',lambda:instant+timedelta(seconds=61*index))
        assert email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',uuid.uuid4()).status=='ACCEPTED'
    with pytest.raises(HTTPException) as error:
        email.send_receipt_email(db_session,actor,'sale',row.id,'different@example.com',uuid.uuid4())
    assert error.value.status_code==429 and transport.call_count==5


@pytest.mark.parametrize('failure,status',[(email.EmailRejected(),'FAILED'),(TimeoutError(),'UNKNOWN')])
def test_failure_does_not_rollback_sale_or_retry_same_key(db_session,shops,transport,failure,status):
    actor,_,_=shops['a'];row=sale(db_session,shops['a']);key=uuid.uuid4();transport.side_effect=failure
    first=email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key)
    assert first.status==status
    assert email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key)==first
    assert transport.call_count==1
    from app.models.sale import Sale
    assert db_session.get(Sale,row.id).monto_total==row.monto_total


def test_old_pending_becomes_unknown_without_automatic_retry(db_session,shops,transport,monkeypatch):
    actor,_,_=shops['a'];row=sale(db_session,shops['a']);key=uuid.uuid4()
    def crash(*args):raise KeyboardInterrupt()
    transport.side_effect=crash
    with pytest.raises(KeyboardInterrupt):email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key)
    instant=email.now()+timedelta(minutes=3);monkeypatch.setattr(email,'now',lambda:instant)
    assert email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key).status=='UNKNOWN'
    assert transport.call_count==1


def test_header_injection_is_rejected(db_session,shops,transport):
    actor,_,_=shops['a'];row=sale(db_session,shops['a'])
    with pytest.raises(HTTPException) as error:
        email.send_receipt_email(db_session,actor,'sale',row.id,'x@example.com\r\nBcc: bad@example.com',uuid.uuid4())
    assert error.value.status_code==422;transport.assert_not_called()


def test_delivery_metadata_is_not_in_commercial_backup(db_session,shops):
    from app.services.backup_recovery import models_for
    assert 'receipt_email_deliveries' not in models_for(shops['a'][0].tenant_id)


def test_two_real_connections_reserve_one_send_and_release_db_lock_before_smtp(committed_shop,transport):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.models import User,ProductVariant,Branch
    ids,factory=committed_shop;user_id,variant_id,branch_id,_=ids;key=uuid.uuid4()
    with factory() as db:
        actor=db.get(User,user_id)
        row=sale(db,(actor,db.get(ProductVariant,variant_id),[db.get(Branch,branch_id)]))
        identifier=row.id;db.commit()
    entered,release=Event(),Event()
    def slow_sender(*args):
        entered.set();assert release.wait(25),'SMTP test gate not released'
    transport.side_effect=slow_sender
    def send():
        with factory() as db:
            return email.send_receipt_email(db,db.get(User,user_id),'sale',identifier,'qa@example.com',key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(send)
        try:
            assert entered.wait(25)
            pending=pool.submit(send).result(timeout=25)
            assert pending.status=='PENDING'
        finally:release.set()
        assert first.result(timeout=25).status=='ACCEPTED'
    assert send().status=='ACCEPTED' and transport.call_count==1


def test_additive_migration_twice_preserves_populated_tables(committed_shop):
    from sqlalchemy import select
    from app.database import Base
    from app.core.receipt_email_ddl import ensure_receipt_email
    _,factory=committed_shop;engine=factory.kw['bind'];table=ReceiptEmailDelivery.__table__
    with engine.begin() as connection:
        table.drop(connection)
        others=[t for t in Base.metadata.sorted_tables if t.name!=table.name]
        def snapshot():
            return {t.name:sorted(str(dict(r)) for r in connection.execute(select(t)).mappings()) for t in others}
        before=snapshot();ensure_receipt_email(connection);ensure_receipt_email(connection)
        assert snapshot()==before
        assert list(connection.execute(select(table)))==[]


@pytest.mark.parametrize('port',[465,587])
def test_smtp_uses_tls_attaches_exact_pdf_and_ignores_quit_failure(monkeypatch,transport,port):
    # Undo only the provider double for this transport unit test.
    from tests.test_receipt_pdf import sample
    # The real function is captured below; transport fixture patched its name.
    real_send=_REAL_SMTP_SEND
    connection=Mock();connection.send_message.return_value={};connection.quit.side_effect=OSError('quit after acceptance')
    monkeypatch.setattr(settings,'SMTP_PORT',port)
    ssl_factory=Mock(return_value=connection);tls_factory=Mock(return_value=connection)
    monkeypatch.setattr(email.smtplib,'SMTP_SSL',ssl_factory);monkeypatch.setattr(email.smtplib,'SMTP',tls_factory)
    pdf=b'%PDF-synthetic-attachment'
    real_send(sample(),'qa@example.com',pdf)
    selected=ssl_factory if port==465 else tls_factory
    assert selected.call_args.kwargs['timeout']==15
    if port==587:connection.starttls.assert_called_once()
    else:connection.starttls.assert_not_called()
    message=connection.send_message.call_args.args[0]
    assert list(message.iter_attachments())[0].get_payload(decode=True)==pdf
    assert connection.send_message.call_args.kwargs['to_addrs']==['qa@example.com']
    assert 'synthetic-test-secret' not in message.as_string()


def test_capabilities_and_email_route_validate_and_authorize(client,db_session,shops,transport):
    from app.main import app
    from app.api.deps import get_history_user
    actor,_,_=shops['a'];row=sale(db_session,shops['a'])
    app.dependency_overrides[get_history_user]=lambda:actor
    try:
        assert client.get('/api/v1/receipts/capabilities').json()=={'email_enabled':True}
        url=f'/api/v1/receipts/sale/{row.id}/email';body={'recipient':'qa@example.com','operation_id':str(uuid.uuid4())}
        response=client.post(url,json=body);assert response.status_code==200
        assert response.json()['status']=='ACCEPTED'
        assert response.headers['cache-control']=='private, no-store'
        assert client.post(url,json=body).json()==response.json();assert transport.call_count==1
        app.dependency_overrides[get_history_user]=lambda:shops['b'][0]
        assert client.post(url,json=body).status_code==404
        assert transport.call_count==1
    finally:app.dependency_overrides.pop(get_history_user,None)


_REAL_SMTP_SEND=email.smtp_send


def test_restore_style_user_replacement_does_not_erase_delivery_identity(db_session,shops,transport):
    from sqlalchemy import delete
    from app.models import User
    actor,_,_=shops['a'];row=sale(db_session,shops['a']);key=uuid.uuid4()
    assert email.send_receipt_email(db_session,actor,'sale',row.id,'qa@example.com',key).status=='ACCEPTED'
    db_session.connection().execute(delete(User.__table__).where(User.id==actor.id))
    assert db_session.query(ReceiptEmailDelivery).filter_by(operation_id=key).one().status=='ACCEPTED'


@pytest.mark.parametrize('stage',['login','send','quit'])
def test_real_transport_distinguishes_preaccept_failure_uncertainty_and_accepted_quit(monkeypatch,transport,stage):
    import smtplib
    from tests.test_receipt_pdf import sample
    connection=Mock();connection.send_message.return_value={}
    monkeypatch.setattr(settings,'SMTP_PORT',587)
    monkeypatch.setattr(email.smtplib,'SMTP',Mock(return_value=connection))
    if stage=='login':
        connection.login.side_effect=smtplib.SMTPAuthenticationError(535,b'provider-private-detail')
        with pytest.raises(email.EmailRejected) as error:_REAL_SMTP_SEND(sample(),'qa@example.com',b'%PDF')
        assert str(error.value)=='';connection.send_message.assert_not_called()
    elif stage=='send':
        connection.send_message.side_effect=TimeoutError('provider-private-detail')
        with pytest.raises(TimeoutError):_REAL_SMTP_SEND(sample(),'qa@example.com',b'%PDF')
    else:
        connection.quit.side_effect=TimeoutError('provider-private-detail')
        _REAL_SMTP_SEND(sample(),'qa@example.com',b'%PDF')
        connection.send_message.assert_called_once()
