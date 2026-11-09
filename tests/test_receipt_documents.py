"""Read-only commercial notes: no new payment, ledger entry or numbering."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from tests.test_tenant_sales import shops
from app.models import User, Role
from app.models.sale import Sale, SaleStatus, PaymentMethod
from app.models.sale_detail import SaleDetail
from app.models.reservation import Reservation, ReservationStatus
from app.models.reservation_detail import ReservationDetail
from app.models.user_wallet import UserWallet
from app.models.wallet_transaction import WalletTransaction
from app.models.product_exchange import ProductExchange
from app.models.product_exchange_detail import ProductExchangeDetail
from app.services.receipt_document_service import get_receipt_document


def sale(db, shop, **overrides):
    user, variant, branches = shop
    values=dict(tenant_id=user.tenant_id, numero_recibo='VTA-'+uuid.uuid4().hex[:12],
        sucursal_id=branches[0].id, cliente_id=user.id, cajero_id=user.id,
        monto_subtotal=Decimal('25.25'), descuento=Decimal('2.00'), costo_envio=Decimal('3.00'),
        monto_total=Decimal('26.25'), monto_recibido=Decimal('30'), cambio=Decimal('3.75'),
        metodo_pago=PaymentMethod.EFECTIVO, estado=SaleStatus.COMPLETADA)
    row=Sale(**(values|overrides)); db.add(row); db.flush()
    db.add(SaleDetail(venta_id=row.id,variante_id=variant.id,cantidad=1,
        precio_unitario=Decimal('25.25'),subtotal=Decimal('25.25')))
    db.flush();return row


def customer(db, tenant):
    role=db.query(Role).filter_by(name='cliente').one()
    user=User(tenant_id=tenant,role_id=role.id,email=uuid.uuid4().hex+'@example.com',
        full_name='Cliente específico',hashed_password='unused',is_active=True)
    db.add(user);db.flush();return user


def test_legacy_wallet_receipt_number_resolves_only_authorized_sale(client,db_session,shops):
    from app.main import app
    from app.api.deps import get_history_user
    actor,_,_=shops['a'];owner=customer(db_session,actor.tenant_id)
    stranger=customer(db_session,actor.tenant_id)
    row=sale(db_session,shops['a'],cliente_id=owner.id)
    url=f'/api/v1/receipts/sale-number/{row.numero_recibo}'
    app.dependency_overrides[get_history_user]=lambda:owner
    try:
        response=client.get(url)
        assert response.status_code==200 and response.json()['entity_id']==str(row.id)
        assert response.headers['cache-control']=='private, no-store'
        for intruder in (stranger,shops['b'][0]):
            app.dependency_overrides[get_history_user]=lambda:intruder
            assert client.get(url).status_code==404
    finally:app.dependency_overrides.pop(get_history_user,None)


def test_wallet_receipt_history_without_wallet_is_read_only(client,db_session,shops):
    from app.main import app
    from app.api.deps import get_history_user
    actor=customer(db_session,shops['a'][0].tenant_id)
    before=db_session.query(UserWallet).count()
    app.dependency_overrides[get_history_user]=lambda:actor
    try:
        result=client.get('/api/v1/wallet/history')
        assert result.status_code==200 and result.json()==[]
        assert db_session.query(UserWallet).count()==before
    finally:app.dependency_overrides.pop(get_history_user,None)


@pytest.mark.parametrize('company',['a','b'])
@pytest.mark.parametrize('method',list(PaymentMethod))
def test_sale_preserves_saved_prices_totals_and_real_method(db_session,shops,company,method):
    actor,variant,_=shops[company];row=sale(db_session,shops[company],metodo_pago=method)
    variant.producto.precio_base=999;db_session.flush()
    doc=get_receipt_document(db_session,actor,'sale',row.id)
    assert doc.total=='26.25' and doc.items[0].unit_price=='25.25'
    assert doc.discount=='2.00' and doc.shipping=='3.00'
    assert doc.payment_method==method.value and doc.payment_status=='PAGADO'
    assert doc.company.id==actor.tenant_id and doc.number==row.numero_recibo
    assert 'impuesto' not in doc.model_dump_json().lower()
    assert doc.reference is None  # no invented capture/reference
    if method!=PaymentMethod.EFECTIVO:assert doc.received is None and doc.change is None


@pytest.mark.parametrize('status,label',[(SaleStatus.PENDIENTE_PAGO,'PENDIENTE DE PAGO'),(SaleStatus.CANCELADA,'CANCELADA')])
def test_pending_paypal_and_cancelled_sale_are_not_paid(db_session,shops,status,label):
    actor,_,_=shops['a'];row=sale(db_session,shops['a'],estado=status,metodo_pago=PaymentMethod.PAYPAL)
    assert get_receipt_document(db_session,actor,'sale',row.id).payment_status==label


def test_client_only_sees_own_company_document(db_session,shops):
    actor,_,_=shops['a'];own=customer(db_session,actor.tenant_id);other=customer(db_session,actor.tenant_id)
    row=sale(db_session,shops['a'],cliente_id=own.id)
    assert get_receipt_document(db_session,own,'sale',row.id).customer.name=='Cliente específico'
    for intruder in (other,shops['b'][0]):
        with pytest.raises(HTTPException) as error:get_receipt_document(db_session,intruder,'sale',row.id)
        assert error.value.status_code==404
    platform=SimpleNamespace(id=uuid.uuid4(),tenant_id=actor.tenant_id,is_active=True,is_platform_admin=True)
    with pytest.raises(HTTPException) as error:get_receipt_document(db_session,platform,'sale',row.id)
    assert error.value.status_code==403


@pytest.mark.parametrize('status',list(ReservationStatus))
def test_reservation_never_certifies_a_payment(db_session,shops,status):
    actor,variant,branches=shops['a'];now=datetime.now(timezone.utc)
    row=Reservation(tenant_id=actor.tenant_id,codigo='RES-'+uuid.uuid4().hex[:8],cliente_id=actor.id,
        sucursal_id=branches[0].id,estado=status,fecha_hora_esperada=now,
        fecha_expiracion=now+timedelta(hours=24),total_estimado=Decimal('25.25'))
    db_session.add(row);db_session.flush()
    db_session.add(ReservationDetail(reserva_id=row.id,variante_id=variant.id,cantidad=1,
        precio_unitario=Decimal('25.25'),subtotal=Decimal('25.25')));db_session.flush()
    doc=get_receipt_document(db_session,actor,'reservation',row.id)
    assert doc.payment_status=='NO ACREDITA PAGO' and doc.status==status.value
    assert doc.expires_at is not None and doc.total=='25.25'


def topup(db,actor,**overrides):
    wallet=UserWallet(tenant_id=actor.tenant_id,user_id=actor.id,saldo_actual=50)
    db.add(wallet);db.flush()
    values=dict(tenant_id=actor.tenant_id,wallet_id=wallet.id,tipo='RECARGA_PAYPAL',
        monto=Decimal('50'),saldo_anterior=0,saldo_posterior=50,
        estado_sync='CONCILIADO',referencia_externa='PAYPAL_QA_CAPTURE')
    row=WalletTransaction(**(values|overrides));db.add(row);db.flush();return row


@pytest.mark.parametrize('kind,method',[('RECARGA_PAYPAL','PAYPAL'),('RECARGA_EFECTIVO_MOSTRADOR','EFECTIVO')])
def test_topup_only_exposes_amount_not_wallet_secrets(db_session,shops,kind,method):
    actor,_,_=shops['a'];row=topup(db_session,actor,tipo=kind)
    doc=get_receipt_document(db_session,actor,'wallet_topup',row.id)
    assert doc.total=='50.00' and doc.payment_method==method
    for secret in ('saldo_actual','saldo_anterior','saldo_posterior','firma','nonce'):
        assert secret not in doc.model_dump_json()


def test_topup_access_and_no_implicit_wallet_creation(db_session,shops):
    actor,_,_=shops['a'];owner=customer(db_session,actor.tenant_id);stranger=customer(db_session,actor.tenant_id)
    row=topup(db_session,owner)
    assert get_receipt_document(db_session,owner,'wallet_topup',row.id).total=='50.00'
    assert get_receipt_document(db_session,actor,'wallet_topup',row.id).total=='50.00'
    for user in (stranger,shops['b'][0]):
        with pytest.raises(HTTPException) as error:get_receipt_document(db_session,user,'wallet_topup',row.id)
        assert error.value.status_code==404
    assert db_session.query(UserWallet).count()==1


def test_foreign_header_is_rejected_before_loading_private_lines(db_session,shops):
    actor,_,_=shops['a'];row=sale(db_session,shops['b'])
    identifier=row.id
    db_session.expire_all();queries=[]
    def track(conn,cursor,statement,parameters,context,many):queries.append(statement.lower())
    connection=db_session.connection();event.listen(connection,'before_cursor_execute',track)
    try:
        with pytest.raises(HTTPException) as error:get_receipt_document(db_session,actor,'sale',identifier)
        assert error.value.status_code==404
        assert not any('from detalles_venta' in sql for sql in queries)
    finally:event.remove(connection,'before_cursor_execute',track)


@pytest.mark.parametrize('overrides',[{'tipo':'PAGO_ORDEN_OFFLINE'},{'estado_sync':'EN_DISPUTA'}])
def test_wallet_debit_or_unconfirmed_credit_is_not_a_topup_receipt(db_session,shops,overrides):
    actor,_,_=shops['a'];row=topup(db_session,actor,**overrides)
    with pytest.raises(HTTPException) as error:get_receipt_document(db_session,actor,'wallet_topup',row.id)
    assert error.value.status_code==409


def test_exchange_negative_difference_is_wallet_credit_not_sale(db_session,shops):
    actor,variant,branches=shops['a'];original=sale(db_session,shops['a'])
    row=ProductExchange(tenant_id=actor.tenant_id,numero_cambio='CAM-QA',venta_id=original.id,
        sucursal_id=branches[0].id,cajero_id=actor.id,cliente_id=actor.id,motivo='Talla',
        monto_total_devuelto=25.25,monto_total_entregado=15,diferencia=-10.25)
    db_session.add(row);db_session.flush()
    db_session.add(ProductExchangeDetail(cambio_id=row.id,variante_devuelta_id=variant.id,
        variante_entregada_id=variant.id,cantidad_devuelta=1,cantidad_entregada=1,
        precio_unitario_devuelto=25.25,precio_unitario_entregado=15,subtotal_diferencia=-10.25));db_session.flush()
    doc=get_receipt_document(db_session,actor,'exchange',row.id)
    assert doc.total=='-10.25' and doc.payment_method=='BILLETERA'
    assert doc.payment_status=='DIFERENCIA A FAVOR EN BILLETERA'
    assert [i.section for i in doc.items]==['DEVUELTA','ENTREGADA']


def test_document_read_does_not_write_or_change_commercial_rows(db_session,shops):
    actor,_,_=shops['a'];row=sale(db_session,shops['a']);db_session.flush()
    writes=[]
    def track(conn,cursor,statement,parameters,context,many):
        if statement.lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE'}:writes.append(statement)
    connection=db_session.connection();event.listen(connection,'before_cursor_execute',track)
    try:
        first=get_receipt_document(db_session,actor,'sale',row.id)
        assert get_receipt_document(db_session,actor,'sale',row.id)==first
        assert writes==[]
    finally:event.remove(connection,'before_cursor_execute',track)


def test_inconsistent_foreign_branch_or_product_cannot_leak_labels(db_session,shops):
    actor,_,_=shops['a'];_,foreign_variant,foreign_branches=shops['b']
    row=sale(db_session,shops['a'],sucursal_id=foreign_branches[0].id)
    with pytest.raises(HTTPException) as error:get_receipt_document(db_session,actor,'sale',row.id)
    assert error.value.status_code==404
    row.sucursal_id=shops['a'][2][0].id
    detail=db_session.query(SaleDetail).filter_by(venta_id=row.id).one()
    detail.variante_id=foreign_variant.id;db_session.flush()
    db_session.expire(detail,['variante']);db_session.expire(row,['detalles'])
    with pytest.raises(HTTPException) as error:get_receipt_document(db_session,actor,'sale',row.id)
    assert error.value.status_code==404


def test_receipt_http_authentication_headers_and_pdf(client,db_session,shops):
    from app.main import app
    from app.api.deps import get_history_user
    actor,_,_=shops['a'];row=sale(db_session,shops['a'])
    url=f'/api/v1/receipts/sale/{row.id}'
    assert client.get(url).status_code==401
    app.dependency_overrides[get_history_user]=lambda:actor
    try:
        response=client.get(url);assert response.status_code==200
        assert response.json()['total']=='26.25'
        pdf=client.get(url+'/pdf');assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-')
        assert pdf.headers['cache-control']=='private, no-store'
        assert pdf.headers['content-type']=='application/pdf'
        assert 'attachment;' in pdf.headers['content-disposition']
        app.dependency_overrides[get_history_user]=lambda:shops['b'][0]
        assert client.get(url).status_code==404 and client.get(url+'/pdf').status_code==404
    finally:app.dependency_overrides.pop(get_history_user,None)
