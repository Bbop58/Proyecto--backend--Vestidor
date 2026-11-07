"""Observed business receipts. No ML training, seed, synthetic fallback or writes."""
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from uuid import UUID
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, func, case, or_
from app.models import Tenant, Sale, SaleStatus, SaleDetail, ProductVariant, Product, Branch, User, Role

BUSINESS_TZ = ZoneInfo('America/La_Paz')


def company_id(db, value):
    try: identifier = UUID(str(value))
    except (ValueError, TypeError): raise HTTPException(422, 'Empresa explícita requerida')
    scope = db.info.get('tenant_scope')
    if db.info.get('platform_actor_id') or (scope and UUID(str(scope)) != identifier):
        raise HTTPException(403, 'El análisis requiere el contexto de la propia empresa')
    row = db.execute(select(Tenant.id, Tenant.es_operadora).where(Tenant.id == identifier)).first()
    if not row: raise HTTPException(404, 'Empresa no disponible')
    if row.es_operadora: raise HTTPException(403, 'La operadora no tiene análisis comerciales')
    return identifier


def normalized_cutoff(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, 'El corte requiere fecha y zona horaria')
    value = value.astimezone(timezone.utc)
    if value > datetime.now(timezone.utc): raise HTTPException(422, 'El corte no puede estar en el futuro')
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=True).encode()).hexdigest()


@dataclass
class ForecastDataset:
    tenant_id: UUID
    cutoff: datetime
    period_end: date
    rows: list
    quality: dict
    fingerprint: str
    warnings: tuple = ()
    source: str = 'BASE_DATOS_EMPRESA'
    environment: str = 'DEMO'


@dataclass
class RfmDataset:
    tenant_id: UUID
    cutoff: datetime
    rows: list
    quality: dict
    fingerprint: str
    warnings: tuple = ()
    source: str = 'BASE_DATOS_EMPRESA'
    environment: str = 'DEMO'


def _receipts(db, tenant, start, end, inclusive=False):
    # Aggregate details BEFORE joining receipts: two lines are still one sale.
    details = (select(SaleDetail.venta_id.label('sale_id'),
        func.sum(SaleDetail.subtotal).label('detail_total'),
        func.sum(case((or_(Product.tenant_id != tenant, SaleDetail.cantidad <= 0,
            SaleDetail.precio_unitario < 0, SaleDetail.subtotal < 0,
            SaleDetail.subtotal != SaleDetail.precio_unitario * SaleDetail.cantidad), 1), else_=0)).label('bad_details'))
        .join(Sale, Sale.id == SaleDetail.venta_id)
        .join(ProductVariant, ProductVariant.id == SaleDetail.variante_id)
        .join(Product, Product.id == ProductVariant.producto_id)
        .where(Sale.tenant_id == tenant).group_by(SaleDetail.venta_id).subquery())
    query = (select(Sale.id, Sale.created_at, Sale.cliente_id, Sale.monto_total,
        Sale.monto_subtotal, Sale.descuento, Sale.costo_envio,
        Branch.tenant_id.label('branch_tenant'), User.tenant_id.label('buyer_tenant'),
        User.full_name.label('buyer_name'), details.c.detail_total, details.c.bad_details)
        .join(Branch, Branch.id == Sale.sucursal_id)
        .outerjoin(User, User.id == Sale.cliente_id)
        .outerjoin(details, details.c.sale_id == Sale.id)
        .where(Sale.tenant_id == tenant, Sale.estado == SaleStatus.COMPLETADA,
               Sale.created_at >= start, Sale.created_at <= end if inclusive else Sale.created_at < end))
    valid, invalid = [], 0
    for row in db.execute(query).mappings():
        try:
            total = Decimal(str(row['monto_total']))
            gross = Decimal(str(row['monto_subtotal']))
            discount = Decimal(str(row['descuento']))
            shipping = Decimal(str(row['costo_envio'] or 0))
            numbers = (total, gross, discount, shipping)
            correct = (all(v.is_finite() and v >= 0 for v in numbers)
                and discount <= gross and total == gross - discount + shipping
                and row['detail_total'] == gross and row['bad_details'] == 0
                and row['branch_tenant'] == tenant
                and (row['cliente_id'] is None or row['buyer_tenant'] == tenant))
        except (InvalidOperation, TypeError, ValueError):
            correct = False
        if not correct:
            invalid += 1
            continue
        valid.append({**row, 'merchandise_bob': (total - shipping).quantize(Decimal('0.01'))})
    return valid, invalid


def build_weekly_sales(db, tenant_id, cutoff):
    tenant, cutoff = company_id(db, tenant_id), normalized_cutoff(cutoff)
    local = cutoff.astimezone(BUSINESS_TZ)
    end_day = local.date() - timedelta(days=local.weekday())
    end = datetime.combine(end_day, datetime.min.time(), BUSINESS_TZ)
    earliest = db.scalar(select(func.min(Sale.created_at)).where(
        Sale.tenant_id == tenant, Sale.created_at < end))
    rows, invalid, receipts = [], 0, []
    if earliest:
        start = earliest.astimezone(BUSINESS_TZ).date()
        start -= timedelta(days=start.weekday())
        start = max(start, end_day - timedelta(weeks=104))
        receipts, invalid = _receipts(db, tenant, datetime.combine(start, datetime.min.time(), BUSINESS_TZ), end)
        by_week = {}
        for sale in receipts:
            day = sale['created_at'].astimezone(BUSINESS_TZ).date()
            monday = day - timedelta(days=day.weekday())
            bucket = by_week.setdefault(monday, [Decimal('0.00'), 0])
            bucket[0] += sale['merchandise_bob']; bucket[1] += 1
        while start < end_day:
            value, count = by_week.get(start, (Decimal('0.00'), 0))
            year, week, _ = start.isocalendar()
            rows.append({'week_start': start, 'iso_year': year, 'iso_week': week,
                         'sales_bob': value, 'receipt_count': count})
            start += timedelta(weeks=1)
    quality = {'weeks': len(rows), 'nonzero_weeks': sum(r['sales_bob'] > 0 for r in rows),
               'sales_count': len(receipts), 'excluded_invalid_sales': invalid}
    warnings = ('Hay ventas no conciliadas excluidas del análisis',) if invalid else ()
    return ForecastDataset(tenant, cutoff, end_day, rows, quality,
        fingerprint({'tenant': tenant, 'rows': rows, 'invalid': invalid}), warnings)


def build_customer_rfm(db, tenant_id, cutoff):
    tenant, cutoff = company_id(db, tenant_id), normalized_cutoff(cutoff)
    receipts, invalid = _receipts(db, tenant, cutoff - timedelta(days=365), cutoff, inclusive=True)
    customers = {}
    guests = 0
    for sale in receipts:
        identifier = sale['cliente_id']
        if identifier is None:
            guests += 1; continue
        row = customers.setdefault(identifier, {'customer_id': str(identifier), 'name': sale['buyer_name'],
            'frequency': 0, 'monetary_bob': Decimal('0.00'), 'last_purchase': sale['created_at']})
        row['frequency'] += 1; row['monetary_bob'] += sale['merchandise_bob']
        row['last_purchase'] = max(row['last_purchase'], sale['created_at'])
    rows = []
    for identifier in sorted(customers, key=str):
        row = customers[identifier]
        last = row.pop('last_purchase')
        row['recency_days'] = (cutoff.astimezone(BUSINESS_TZ).date() - last.astimezone(BUSINESS_TZ).date()).days
        rows.append(row)
    registered = set(db.scalars(select(User.id).join(Role, Role.id == User.role_id)
        .where(User.tenant_id == tenant, Role.name == 'cliente')).all())
    quality = {'eligible_buyers': len(rows), 'guest_receipts': guests,
        'customers_without_purchases': len(registered - set(customers)), 'excluded_invalid_sales': invalid}
    warnings = ('Hay ventas no conciliadas excluidas del análisis',) if invalid else ()
    return RfmDataset(tenant, cutoff, rows, quality,
        fingerprint({'tenant': tenant, 'rows': rows, 'quality': quality}), warnings)
