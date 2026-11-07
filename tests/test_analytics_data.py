"""M1: observed company receipts, never generated ML sales or foreign buyers."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
import pytest
from fastapi import HTTPException
from app.models import Sale, SaleDetail, SaleStatus, User, Role
from tests.test_tenant_sales import shops
from app.ml.analytics_data import build_weekly_sales, build_customer_rfm

CUT = datetime(2026, 1, 14, 16, tzinfo=timezone.utc)


def buyer(db, company):
    role = db.query(Role).filter_by(name='cliente', tenant_id=None).one()
    user = User(tenant_id=company, role_id=role.id, email=f'{uuid.uuid4()}@example.com',
                full_name='Cliente QA', hashed_password='unused', is_active=True)
    db.add(user); db.flush()
    return user


def receipt(db, shop, when, amount=100, discount=0, shipping=0, customer=None,
            status=SaleStatus.COMPLETADA, lines=1):
    actor, variant, branches = shop
    gross = Decimal(str(amount))
    total = gross - Decimal(str(discount)) + Decimal(str(shipping))
    sale = Sale(tenant_id=actor.tenant_id, sucursal_id=branches[0].id,
                numero_recibo='ML-' + uuid.uuid4().hex[:20], cliente_id=customer.id if customer else None,
                estado=status, created_at=when, monto_subtotal=gross, descuento=discount,
                costo_envio=shipping, monto_total=total)
    db.add(sale); db.flush()
    for _ in range(lines):
        db.add(SaleDetail(venta_id=sale.id, variante_id=variant.id, cantidad=1,
                         precio_unitario=gross / lines, subtotal=gross / lines))
    db.commit()
    return sale


def test_weekly_receipts_discount_shipping_no_join_duplicates_or_foreign_data(db_session, shops):
    db = db_session; a = shops['a']; b = shops['b']
    receipt(db, a, CUT - timedelta(days=8), discount=10, shipping=5, lines=2)
    receipt(db, a, CUT - timedelta(days=7), amount=20)
    receipt(db, b, CUT - timedelta(days=8), amount=9000)
    receipt(db, a, CUT - timedelta(days=8), amount=200, status=SaleStatus.CANCELADA)
    receipt(db, a, CUT - timedelta(days=8), amount=400, status=SaleStatus.PENDIENTE_PAGO)
    receipt(db, a, CUT - timedelta(days=1), amount=999)  # current open week
    future = receipt(db, a, CUT + timedelta(days=8), amount=1000)
    data = build_weekly_sales(db, a[0].tenant_id, CUT)
    assert sum(row['sales_bob'] for row in data.rows) == Decimal('110.00')
    assert sum(row['receipt_count'] for row in data.rows) == 2
    assert data.source == 'BASE_DATOS_EMPRESA' and data.environment == 'DEMO'
    assert data.period_end.isoformat() == '2026-01-12'
    before = data.fingerprint
    future.monto_total = 123456; db.commit()
    assert build_weekly_sales(db, a[0].tenant_id, CUT).fingerprint == before


def test_rfm_counts_original_receipts_and_only_identified_own_buyers(db_session, shops):
    db = db_session; a = shops['a']; customer = buyer(db, a[0].tenant_id)
    other = buyer(db, shops['b'][0].tenant_id); buyer(db, a[0].tenant_id)
    receipt(db, a, CUT - timedelta(days=8), discount=10, shipping=5, customer=customer, lines=2)
    receipt(db, a, CUT - timedelta(days=1), amount=20, customer=customer)
    receipt(db, a, CUT - timedelta(days=2), amount=30)  # guest still in company sales
    receipt(db, shops['b'], CUT - timedelta(days=1), amount=9000, customer=other)
    receipt(db, a, CUT + timedelta(days=2), customer=customer)
    receipt(db, a, CUT - timedelta(days=366), customer=customer)
    receipt(db, a, CUT - timedelta(days=2), customer=customer, status=SaleStatus.CANCELADA)
    data = build_customer_rfm(db, a[0].tenant_id, CUT)
    assert len(data.rows) == 1
    row = data.rows[0]
    assert row['customer_id'] == str(customer.id)
    assert row['frequency'] == 2 and row['monetary_bob'] == Decimal('110.00')
    assert row['recency_days'] == 1
    assert data.quality['customers_without_purchases'] == 1


def test_no_sales_does_not_create_synthetic_history_or_customers(db_session, shops):
    company = shops['a'][0].tenant_id
    assert build_weekly_sales(db_session, company, CUT).rows == []
    assert build_customer_rfm(db_session, company, CUT).rows == []


def test_business_week_boundary_and_iso_week_53(db_session, shops):
    db = db_session; company = shops['a'][0].tenant_id
    receipt(db, shops['a'], datetime(2020, 12, 30, 12, tzinfo=timezone.utc))
    before = build_weekly_sales(db, company, datetime(2021, 1, 4, 3, 59, tzinfo=timezone.utc))
    after = build_weekly_sales(db, company, datetime(2021, 1, 4, 4, tzinfo=timezone.utc))
    assert before.rows == []
    assert len(after.rows) == 1
    assert after.rows[0]['iso_year'] == 2020 and after.rows[0]['iso_week'] == 53


def test_observed_zero_weeks_and_cap_without_erasing_inactive_history(db_session, shops):
    db = db_session; a = shops['a']; company = a[0].tenant_id
    receipt(db, a, CUT - timedelta(weeks=150))
    receipt(db, a, CUT - timedelta(days=8))
    a[2][0].activa = False; a[1].activo = False; a[1].producto.activo = False; db.commit()
    rows = build_weekly_sales(db, company, CUT).rows
    assert len(rows) == 104
    assert sum(r['sales_bob'] for r in rows) == 100
    assert len([r for r in rows if r['sales_bob'] == 0]) == 103


def test_invalid_receipt_is_reported_not_invented(db_session, shops):
    db = db_session; a = shops['a']
    bad = receipt(db, a, CUT - timedelta(days=8)); bad.monto_total = 300; db.commit()
    data = build_weekly_sales(db, a[0].tenant_id, CUT)
    assert sum(r['sales_bob'] for r in data.rows) == 0
    assert data.quality['excluded_invalid_sales'] == 1 and data.warnings


def test_negative_line_is_invalid_even_if_other_line_offsets_it(db_session, shops):
    db = db_session; a = shops['a']
    sale = receipt(db, a, CUT - timedelta(days=8), lines=2)
    lines = db.query(SaleDetail).filter_by(venta_id=sale.id).all()
    lines[0].precio_unitario = lines[0].subtotal = -10
    lines[1].precio_unitario = lines[1].subtotal = 110
    db.commit()
    data = build_weekly_sales(db, a[0].tenant_id, CUT)
    assert data.quality['excluded_invalid_sales'] == 1
    assert sum(row['sales_bob'] for row in data.rows) == 0


@pytest.mark.parametrize('builder', [build_weekly_sales, build_customer_rfm])
def test_scope_and_cutoff_are_explicit(db_session, shops, builder):
    with pytest.raises(HTTPException): builder(db_session, None, CUT)
    with pytest.raises(HTTPException): builder(db_session, shops['a'][0].tenant_id, CUT.replace(tzinfo=None))
    db_session.info['tenant_scope'] = shops['a'][0].tenant_id
    try:
        with pytest.raises(HTTPException) as error: builder(db_session, shops['b'][0].tenant_id, CUT)
        assert error.value.status_code == 403
    finally: db_session.info.pop('tenant_scope', None)
