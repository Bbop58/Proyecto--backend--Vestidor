"""Read existing operations without writes, provider calls or new numbering."""
from datetime import timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import lazyload
from app.models.tenant import Tenant
from app.models.user import User
from app.models.branch import Branch
from app.models.sale import Sale
from app.models.reservation import Reservation
from app.models.product_exchange import ProductExchange
from app.models.wallet_transaction import WalletTransaction
from app.models.user_wallet import UserWallet
from app.schemas.receipt import ReceiptDocument, ReceiptParty, ReceiptItem
from app.services.role_service import RoleService

BUSINESS_TZ = ZoneInfo('America/La_Paz')


def money(value):
    try:
        number=Decimal(str(value))
        if not number.is_finite():raise ValueError()
        return format(number.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'.2f')
    except (InvalidOperation,ValueError,TypeError):
        raise HTTPException(409,'La operación no tiene un importe válido para el comprobante') from None


def local_date(value):
    if value is None:raise HTTPException(409,'La operación no tiene fecha registrada')
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(BUSINESS_TZ)


def _value(value):return value.value if hasattr(value,'value') else str(value)


def _missing():raise HTTPException(404,'Comprobante no encontrado')


def _party(entity, *, customer=False):
    if entity is None:return None
    return ReceiptParty(id=entity.id,name=entity.full_name if customer else entity.nombre,
        email=entity.email if customer else getattr(entity,'email_contacto',None),
        address=None if customer else getattr(entity,'direccion',None))


def _item(detail,variant, *, tenant_id,section=None, quantity=None,price=None,subtotal=None):
    product=variant.producto if variant else None
    if product is not None and product.tenant_id!=tenant_id:_missing()
    return ReceiptItem(description=product.nombre if product else 'Prenda no disponible en catálogo',
        sku=variant.sku if variant else None,size=variant.talla if variant else None,
        color=variant.color if variant else None,quantity=quantity if quantity is not None else detail.cantidad,
        unit_price=money(price if price is not None else detail.precio_unitario),
        subtotal=money(subtotal if subtotal is not None else detail.subtotal),section=section)


def ensure_receipt_actor(actor):
    # Reject the platform context before even querying commercial tables.
    from app.core.access_policy import is_platform_candidate
    if actor.is_platform_admin or is_platform_candidate(actor):
        raise HTTPException(403,'La plataforma no tiene acceso a comprobantes comerciales')
    if not actor.is_active or not actor.tenant_id:_missing()


def get_receipt_document(db, actor, kind, entity_id) -> ReceiptDocument:
    ensure_receipt_actor(actor)
    models={'sale':Sale,'reservation':Reservation,'wallet_topup':WalletTransaction,'exchange':ProductExchange}
    if kind not in models:_missing()
    with db.no_autoflush:
        tenant=db.get(Tenant,actor.tenant_id)
        if tenant is None or tenant.es_operadora:_missing()
        # Fetch scalar header only. Eager defaults must not load private lines
        # before the owner/permission check, including with an unscoped Session.
        model=models[kind]
        row=db.execute(select(model).options(lazyload('*')).where(
            model.id==entity_id,model.tenant_id==actor.tenant_id)).scalar_one_or_none()
        if row is None:_missing()
        permission='reservas.list' if kind=='reservation' else 'ventas.list'
        if kind=='wallet_topup':
            wallet=db.execute(select(UserWallet).options(lazyload('*')).where(
                UserWallet.id==row.wallet_id,UserWallet.tenant_id==actor.tenant_id)).scalar_one_or_none()
            if wallet is None:_missing()
            owner=wallet.user_id
        elif kind=='exchange':
            original=db.execute(select(Sale).options(lazyload('*')).where(
                Sale.id==row.venta_id,Sale.tenant_id==actor.tenant_id)).scalar_one_or_none()
            if original is None:_missing()
            owner=row.cliente_id or original.cliente_id
        else:owner=row.cliente_id
        if owner!=actor.id:
            permissions=RoleService.get_permission_codes(db,actor.role_id) if actor.role_id else []
            if permission not in permissions:_missing()
        # No per-user branch assignments exist in this project's user schema.
        # Enforce the existing enterprise permissions and validate the branch's
        # enterprise, without inventing a new employee restriction.
        branch=None
        if kind!='wallet_topup':
            branch=db.query(Branch).filter_by(id=row.sucursal_id,tenant_id=actor.tenant_id).first()
            if branch is None:_missing()
        customer=db.query(User).filter_by(id=owner,tenant_id=actor.tenant_id).first() if owner else None
        common=dict(kind=kind,entity_id=row.id,company=_party(tenant),branch=_party(branch),
            customer=_party(customer,customer=True),issued_at=local_date(row.created_at),
            notes=['Documento comercial. No constituye factura fiscal.'])
        if kind=='sale':
            state=_value(row.estado)
            labels={'COMPLETADA':'PAGADO','PENDIENTE_PAGO':'PENDIENTE DE PAGO','CANCELADA':'CANCELADA'}
            if state not in labels:raise HTTPException(409,'Estado de venta no compatible')
            items=[_item(d,d.variante,tenant_id=actor.tenant_id) for d in row.detalles]
            return ReceiptDocument(**common,title='Nota de venta',number=row.numero_recibo,
                status=state,payment_status=labels[state],payment_method=_value(row.metodo_pago),
                reference=row.referencia_pago,items=items,total=money(row.monto_total),
                subtotal=money(row.monto_subtotal) if row.monto_subtotal is not None else None,
                discount=money(row.descuento),shipping=money(row.costo_envio or 0),
                received=money(row.monto_recibido) if _value(row.metodo_pago)=='EFECTIVO' and row.monto_recibido is not None else None,
                change=money(row.cambio) if _value(row.metodo_pago)=='EFECTIVO' and row.cambio is not None else None)
        if kind=='reservation':
            return ReceiptDocument(**common,title='Constancia de reserva',number=row.codigo,
                status=_value(row.estado),payment_status='NO ACREDITA PAGO',
                expires_at=local_date(row.fecha_expiracion),items=[_item(d,d.variante,tenant_id=actor.tenant_id) for d in row.detalles],
                total=money(row.total_estimado))
        if kind=='wallet_topup':
            methods={'RECARGA_PAYPAL':'PAYPAL','RECARGA_EFECTIVO_MOSTRADOR':'EFECTIVO'}
            if row.tipo not in methods or row.estado_sync!='CONCILIADO' or row.monto<=0:
                raise HTTPException(409,'La transacción no es una recarga confirmada')
            amount=money(row.monto)
            return ReceiptDocument(**common,title='Comprobante de recarga',number='REC-'+str(row.client_operation_id or row.id),
                status='CONCILIADO',payment_status='ACREDITADO',payment_method=methods[row.tipo],
                reference=row.referencia_externa,total=amount,
                items=[ReceiptItem(description='Recarga de billetera',quantity=1,unit_price=amount,subtotal=amount)])
        items=[]
        for detail in row.detalles:
            items.append(_item(detail,detail.variante_devuelta,tenant_id=actor.tenant_id,
                section='DEVUELTA',quantity=detail.cantidad_devuelta,price=detail.precio_unitario_devuelto,
                subtotal=detail.precio_unitario_devuelto*detail.cantidad_devuelta))
            if detail.variante_entregada_id and detail.cantidad_entregada:
                items.append(_item(detail,detail.variante_entregada,tenant_id=actor.tenant_id,
                    section='ENTREGADA',quantity=detail.cantidad_entregada,price=detail.precio_unitario_entregado,
                    subtotal=detail.precio_unitario_entregado*detail.cantidad_entregada))
        difference=Decimal(row.diferencia)
        common['issued_at']=local_date(row.fecha_cambio)
        common['notes'].append('Recibo original: '+original.numero_recibo)
        is_return=(row.tipo_operacion or 'CAMBIO')=='DEVOLUCION'
        method=row.metodo_pago_diferencia or 'NINGUNO'
        payment_status=(f'DEVOLUCIÓN REEMBOLSADA · {method}' if is_return else
            f'REEMBOLSO REGISTRADO · {method}' if difference<0 else
            f'DIFERENCIA COBRADA · {method}' if difference>0 else 'SIN DIFERENCIA')
        amount=-difference if is_return else difference
        return ReceiptDocument(**common,title='Nota de devolución' if is_return else 'Nota de cambio',number=row.numero_cambio,
            status=row.estado_liquidacion or 'CONFIRMADO',payment_status=payment_status,
            payment_method=method,items=items,total=money(amount))
