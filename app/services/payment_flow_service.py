"""Durable PayPal orchestration: quote, bind, verify, apply or compensate."""
import hashlib
import re
import uuid
from collections import Counter
from decimal import Decimal
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, func

from app.config import settings
from app.core.money import money, quote
from app.models.payment_intent import PaymentIntent
from app.models.inventory import Inventory
from app.models.reservation import Reservation, ReservationStatus
from app.models.sale import Sale, SaleStatus
from app.models.product_exchange import ProductExchange
from app.schemas.sale import PresencialSaleCreate, PaymentInfo, SaleItemCreate
from app.services.sale_service import SaleService
from app.services.role_service import RoleService
from app.services.paypal_service import PayPalService
from app.core.commercial_transaction import lock_branch


def request_key(intent, action):
    return str(uuid.uuid5(intent.id, action))


class PaymentFlowService:
    @staticmethod
    def credentials(db, user, intent=None):
        from app.services.company_paypal_service import CompanyPayPalService
        return CompanyPayPalService.resolve(db,user.tenant_id,intent,for_new_payment=intent is None)

    @staticmethod
    def merchant(creds):
        return hashlib.sha256(f"{creds['mode']}:{creds['client_id']}".encode()).hexdigest()

    @staticmethod
    def sale_payload(db, user, data):
        staff = 'ventas.create' in RoleService.get_permission_codes(db, user.role_id)
        if data.cliente_id and data.cliente_id != user.id and not staff:
            raise HTTPException(403, 'El cliente debe operar con su propia cuenta')
        from app.models.branch import Branch
        if not db.query(Branch.id).filter_by(id=data.sucursal_id, tenant_id=user.tenant_id, activa=True).first():
            raise HTTPException(404, 'Sucursal no disponible')
        items = [item.model_copy(update={'reserva_id': data.reserva_id or item.reserva_id}) for item in data.detalles]
        if not items or not data.sucursal_id:
            raise HTTPException(400, 'Identifica la sucursal y las variantes del carrito')
        customer = data.cliente_id or (None if staff else user.id)
        if data.reserva_id:
            reservation = db.query(Reservation).filter_by(id=data.reserva_id, tenant_id=user.tenant_id).first()
            if not reservation or (not staff and reservation.cliente_id != user.id):
                raise HTTPException(404, 'Reserva no disponible')
            customer = customer or reservation.cliente_id
        sale = PresencialSaleCreate(sucursal_id=data.sucursal_id, cliente_id=customer, items=items,
            pago=PaymentInfo(metodo='PAYPAL'), codigo_cupon=data.codigo_cupon)
        for rid in {i.reserva_id for i in items if i.reserva_id}:
            reservation = db.query(Reservation).filter_by(id=rid, tenant_id=user.tenant_id).first()
            if not reservation or (customer and reservation.cliente_id != customer):
                raise HTTPException(404, 'Reserva no disponible para este cliente')
            customer = reservation.cliente_id
        sale.cliente_id = customer
        SaleService._validate_scope(db, sale, user.tenant_id, customer)
        return sale, staff

    @staticmethod
    def validate_sale(db, user, sale):
        SaleService._validate_scope(db, sale, user.tenant_id, sale.cliente_id)
        pricing = quote(db, sale.items, user.tenant_id, sale.codigo_cupon)
        reserved = Counter()
        for rid in {i.reserva_id for i in sale.items if i.reserva_id}:
            row = db.query(Reservation).filter_by(id=rid).populate_existing().first()
            if not row or row.estado not in (ReservationStatus.PENDIENTE, ReservationStatus.PREPARADA) or row.fecha_expiracion <= datetime.now(timezone.utc):
                raise HTTPException(409, 'La reserva ya no está vigente')
            actual, expected = Counter(), Counter()
            for item in sale.items:
                if item.reserva_id == rid:
                    actual[item.variante_id] += item.cantidad
            for detail in row.detalles:
                expected[detail.variante_id] += detail.cantidad
            if actual != expected:
                raise HTTPException(409, 'La reserva se liquida completa')
            reserved.update(expected)
        required = Counter()
        for item in sale.items:
            required[item.variante_id] += item.cantidad
        for variant, quantity in required.items():
            inv = db.query(Inventory).filter_by(sucursal_id=sale.sucursal_id, variante_id=variant, activo=True).first()
            if not inv or inv.stock_actual < quantity or inv.stock_reservado < reserved[variant] or inv.stock_disponible < quantity-reserved[variant]:
                raise HTTPException(409, 'La disponibilidad cambió; actualiza el carrito')
        return pricing

    @classmethod
    def create(cls, db, user, data, *, purpose='SALE'):
        try:
            from app.services.entitlement_service import EntitlementService
            licensed=EntitlementService.check(db,user)
            SaleService._actor_tenant(db, user.id)
            # Identity authorization precedes configuration/provider errors,
            # including when the company has not enabled PayPal yet.
            if (purpose == 'SALE' and data.cliente_id and data.cliente_id != user.id
                    and 'ventas.create' not in RoleService.get_permission_codes(db,user.role_id)):
                raise HTTPException(403, 'El cliente debe operar con su propia cuenta')
            if purpose == 'SALE':
                from app.models.branch import Branch
                if not db.query(Branch.id).filter_by(
                        id=data.sucursal_id, tenant_id=user.tenant_id, activa=True).first():
                    raise HTTPException(404, 'Sucursal no disponible')
            creds = cls.credentials(db, user)
            credential_id = creds.pop('_credential_id',None)
            unresolved = (db.query(PaymentIntent).filter(
                PaymentIntent.user_id == user.id, PaymentIntent.tenant_id == user.tenant_id,
                PaymentIntent.purpose == purpose,
                PaymentIntent.state.in_(['CAPTURING','CAPTURED','REFUND_PENDING','REVIEW']))
                .order_by(PaymentIntent.created_at).first())
            if unresolved:
                if unresolved.state == 'REVIEW':
                    raise HTTPException(409, f'La orden {unresolved.provider_order_id} requiere conciliación antes de otro pago')
                cls.capture(db, user, unresolved.provider_order_id, purpose=purpose)
                raise HTTPException(409, 'Se recuperó el pago anterior. Revisa tu historial antes de iniciar otra compra')
            input_hash = hashlib.sha256(data.model_dump_json(exclude={'request_id'}).encode()).hexdigest()
            if data.request_id:
                previous = db.query(PaymentIntent).filter_by(id=data.request_id).with_for_update().first()
                if previous:
                    if (previous.user_id != user.id or previous.purpose != purpose
                        or previous.payload.get('input_hash') != input_hash
                        or previous.merchant_key != cls.merchant(creds)):
                        raise HTTPException(409, 'La clave ya corresponde a otra operación')
                    if previous.provider_order_id:
                        return cls.creation_result(previous)
            if purpose == 'SALE':
                sale, staff = cls.sale_payload(db, user, data)
                priced = cls.validate_sale(db, user, sale)
                if money(data.monto_bob) != priced['total']:
                    raise HTTPException(409, 'El total no coincide con la cotización del servidor')
                sale.items = [SaleItemCreate(**row) for row in priced['items']]
                payload = {'sale': sale.model_dump(mode='json'), 'staff': staff}
                amount = priced['total']
            else:
                amount = money(data.monto_bob or data.monto_bs, positive=True)
                payload = {'topup': str(amount)}
            key = data.request_id or uuid.uuid4()
            # Serialize creation of the same request ID, including the absent-row case.
            advisory = int.from_bytes(hashlib.sha256(key.bytes).digest()[:8], 'big', signed=True)
            db.execute(select(func.pg_advisory_xact_lock(advisory)))
            intent = db.query(PaymentIntent).filter_by(id=key).with_for_update().first()
            if intent:
                if intent.user_id != user.id or intent.purpose != purpose or intent.payload.get('request') != payload:
                    raise HTTPException(409, 'La clave ya pertenece a otra operación')
            else:
                # Core boolean prevents hidden cross-tenant UUID collisions from becoming 500.
                if db.execute(select(PaymentIntent.__table__.c.id).where(PaymentIntent.__table__.c.id == key)).first():
                    raise HTTPException(409, 'Clave de operación no disponible')
                rate = Decimal(str(settings.PAYPAL_EXCHANGE_RATE))
                usd = money(PayPalService.convert_bob_to_usd(amount, rate=rate), positive=True)
                intent = PaymentIntent(id=key, tenant_id=user.tenant_id, user_id=user.id, purpose=purpose,
                    cliente_id=sale.cliente_id if purpose == 'SALE' else user.id,
                    sucursal_id=sale.sucursal_id if purpose == 'SALE' else None,
                    state='CREATING', amount_bob=amount, amount_usd=usd, exchange_rate=rate,
                    merchant_key=cls.merchant(creds), provider_mode=creds['mode'], payload={'request': payload, 'input_hash': input_hash,
                        'license_term_id':str(licensed['term']['id']),
                        'paypal_credential_id':credential_id})
                db.add(intent)
                db.commit()  # Recovery identity exists before a network effect.
                intent = db.query(PaymentIntent).filter_by(id=key).with_for_update().one()
            if cls.merchant(creds) != intent.merchant_key:
                raise HTTPException(409, 'La cuenta de cobro cambió; requiere conciliación')
            if not intent.provider_order_id:
                result = PayPalService.create_order(monto_bob=float(intent.amount_bob),
                    sucursal_id=str(intent.sucursal_id) if intent.sucursal_id else None,
                    descripcion='Recarga de billetera' if purpose == 'WALLET' else 'Compra FashionStore',
                    request_id=request_key(intent, 'create'), custom_id=str(intent.id), amount_usd=intent.amount_usd,
                    **creds)
                intent.provider_order_id = result['order_id']
                intent.payload = {**intent.payload, 'approve_url': result.get('approve_url')}
                intent.state = 'CREATED'
                db.commit()
            return cls.creation_result(intent)
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def creation_result(intent):
        return dict(order_id=intent.provider_order_id, status=intent.state,
            monto_bob=float(intent.amount_bob), monto_usd=float(intent.amount_usd),
            exchange_rate=float(intent.exchange_rate), approve_url=intent.payload.get('approve_url'))

    @classmethod
    def owned(cls, db, user, order_id, purpose, *, staff=False):
        intent = (db.query(PaymentIntent).filter_by(provider_order_id=order_id, tenant_id=user.tenant_id, purpose=purpose)
                  .populate_existing().with_for_update().first())
        if not intent or (intent.user_id != user.id and not staff):
            raise HTTPException(404, 'Orden no disponible')
        return intent

    @staticmethod
    def verify_capture(intent, raw):
        try:
            units = raw['purchase_units']
            assert raw['id'] == intent.provider_order_id and raw['status'] == 'COMPLETED' and len(units) == 1
            unit = units[0]
            assert unit['custom_id'] == str(intent.id)
            captures = unit['payments']['captures']
            assert len(captures) == 1
            capture = captures[0]
            assert capture['status'] == 'COMPLETED' and isinstance(capture['id'],str) and re.fullmatch(r'[A-Za-z0-9-]{3,100}',capture['id'])
            assert capture['amount']['currency_code'] == 'USD'
            assert money(capture['amount']['value'], positive=True) == money(intent.amount_usd)
            return capture['id']
        except (KeyError, IndexError, TypeError, AssertionError):
            raise HTTPException(409, 'La captura no coincide con el importe, moneda o identidad de la orden')

    @classmethod
    def compensate(cls, db, intent, creds):
        intent.state = 'REFUND_PENDING'
        db.commit()
        intent = db.query(PaymentIntent).filter_by(id=intent.id).with_for_update().one()
        try:
            cls.refund_remote(intent, creds)
            db.commit()
        except Exception:
            db.rollback()  # REFUND_PENDING stays durable, including ambiguous provider timeouts.
        raise HTTPException(409, 'La compra no se aplicó. Reembolso confirmado' if intent.state == 'REFUNDED'
                            else 'La compra no se aplicó. Reembolso pendiente de conciliación; consulta esta misma orden')

    @staticmethod
    def refund_remote(intent, creds):
        api = {k:v for k,v in creds.items() if k != 'brand_name'}
        result = (PayPalService.get_refund_details(intent.refund_id, **api) if intent.refund_id
                  else PayPalService.refund_capture(intent.capture_id, request_id=request_key(intent, 'refund'), **api))
        if result.get('id'):
            intent.refund_id = result['id']
        if (result.get('status') == 'COMPLETED' and result.get('id')
            and result.get('amount', {}).get('currency_code') == 'USD'
            and money(result.get('amount', {}).get('value')) == money(intent.amount_usd)):
            intent.state = 'REFUNDED'
            return True
        return False

    @classmethod
    def capture(cls, db, user, order_id, *, purpose='SALE', supplied=None):
        try:
            can_sell = 'ventas.create' in RoleService.get_permission_codes(db, user.role_id)
            if purpose == 'SALE' and supplied and supplied.cliente_id and supplied.cliente_id != user.id and not can_sell:
                raise HTTPException(403, 'El cliente debe operar con su propia cuenta')
            intent = cls.owned(db, user, order_id, purpose)
            from app.services.entitlement_service import EntitlementService
            can_capture=EntitlementService.payment_recovery(db,user,intent)
            if not can_capture and intent.state not in ('APPLIED','CAPTURED','CAPTURING','REFUND_PENDING','REFUNDED'):
                raise HTTPException(409,'REVISION_REQUERIDA: pago sin autorización contractual previa; no se inicia un cargo')
            creds = cls.credentials(db, user, intent)
            creds.pop('_credential_id',None)
            if cls.merchant(creds) != intent.merchant_key:
                raise HTTPException(409, 'La cuenta de cobro cambió; requiere conciliación')
            if purpose == 'SALE':
                original = PresencialSaleCreate.model_validate(intent.payload['request']['sale'])
            if purpose == 'SALE' and supplied:
                rows = [i.model_copy(update={'reserva_id': supplied.reserva_id or i.reserva_id}) for i in supplied.detalles]
                def contents(items):
                    counts = Counter()
                    for item in items: counts[(str(item.variante_id), str(item.reserva_id))] += item.cantidad
                    return counts
                if (supplied.sucursal_id != intent.sucursal_id or contents(rows) != contents(original.items)
                        or (supplied.cliente_id and supplied.cliente_id != intent.cliente_id)):
                    raise HTTPException(409, 'La captura corresponde a otro carrito')
            if intent.state == 'APPLIED':
                if purpose == 'SALE' and not can_sell and intent.cliente_id != user.id:
                    raise HTTPException(404, 'Venta no disponible')
                return cls.result(db, intent)
            if intent.state == 'REFUNDED':
                raise HTTPException(409, 'Esta orden ya fue reembolsada')
            if intent.state == 'REFUND_PENDING':
                return cls.compensate(db, intent, creds)
            first = intent.state == 'CREATED'
            if first and purpose == 'SALE' and intent.payload['request']['staff'] and not can_sell:
                raise HTTPException(403, 'La cuenta ya no tiene autorización de caja')
            if first and purpose == 'SALE':
                cls.validate_sale(db, user, original)
            if first:
                intent.state = 'CAPTURING'
                db.commit()
                intent = cls.owned(db, user, order_id, purpose)
            if intent.state == 'APPLIED':
                return cls.result(db, intent)
            if intent.state not in ('CAPTURED','CAPTURING'):
                raise HTTPException(409, 'La orden requiere conciliación')
            if intent.state == 'CAPTURING':
                raw = PayPalService.get_order_details(order_id, **{k:v for k,v in creds.items() if k != 'brand_name'})
                if raw.get('status') != 'COMPLETED':
                    if not can_capture:raise HTTPException(409,'REVISION_REQUERIDA: solo se permite conciliar una captura ya realizada')
                    if purpose == 'SALE': cls.validate_sale(db, user, original)
                    result = PayPalService.capture_order(order_id,
                        request_id=request_key(intent, 'capture'), **{k:v for k,v in creds.items() if k != 'brand_name'})
                    raw = result.get('raw_response', {})
                capture_id = cls.verify_capture(intent, raw)
                from app.core.payment_evidence import capture_claimed_elsewhere
                if capture_claimed_elsewhere(db,capture_id,commercial_id=intent.id):
                    intent.state = 'REVIEW'
                    db.commit()
                    raise HTTPException(409, 'La captura ya está vinculada a otra operación; requiere conciliación')
                intent.capture_id, intent.state = capture_id, 'CAPTURED'
                db.commit()
                intent = cls.owned(db, user, order_id, purpose)
            if intent.state == 'APPLIED': return cls.result(db, intent)
            if intent.state == 'REFUND_PENDING': return cls.compensate(db, intent, creds)
            if intent.state != 'CAPTURED': raise HTTPException(409, 'La orden requiere conciliación')
            try:
                if purpose == 'SALE':
                    if intent.payload['request']['staff'] and not can_sell:
                        raise HTTPException(403, 'La cuenta ya no tiene autorización de caja')
                    data = PresencialSaleCreate.model_validate(intent.payload['request']['sale'])
                    data.pago.referencia = f'PAYPAL_{intent.capture_id}'
                    previous = db.info.get('contract_reconciliation')
                    db.info['contract_reconciliation'] = (user.id, user.tenant_id, intent.id)
                    try:
                        SaleService.create_presencial_sale(db, data,
                            cajero_id=user.id if intent.payload['request']['staff'] else None,
                            actor_id=user.id, _payment_intent=intent)
                    finally:
                        if previous is None: db.info.pop('contract_reconciliation', None)
                        else: db.info['contract_reconciliation'] = previous
                else:
                    from app.services.wallet_service import WalletService
                    WalletService.topup_paypal(db, user.id, user.tenant_id, order_id,
                        float(intent.amount_usd), _payment_intent=intent)
            except Exception:
                db.rollback()
                intent = cls.owned(db, user, order_id, purpose)
                if intent.state == 'APPLIED': return cls.result(db, intent)
                return cls.compensate(db, intent, {k:v for k,v in creds.items() if k != 'brand_name'})
            return cls.result(db, intent)
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def result(db, intent):
        if intent.purpose == 'SALE':
            sale = db.query(Sale).filter_by(id=intent.sale_id, tenant_id=intent.tenant_id).one()
            return dict(venta=SaleService._format_sale_response(sale), paypal_order_id=intent.provider_order_id,
                paypal_capture_id=intent.capture_id, paypal_status='COMPLETED', monto_usd=float(intent.amount_usd))
        from app.services.wallet_service import WalletService
        return WalletService.get_or_create_wallet(db, intent.user_id, intent.tenant_id)


    @classmethod
    def refund_sale(cls, db, user, sale_id, reason):
        try:
            intent = db.query(PaymentIntent).filter_by(sale_id=sale_id, tenant_id=user.tenant_id).with_for_update().first()
            if not intent:
                return SaleService.cancel_sale(db, sale_id, reason, user.id)
            sale = db.query(Sale).filter_by(id=sale_id, tenant_id=user.tenant_id).first()
            if not sale:
                raise HTTPException(404, 'Venta no disponible')
            lock_branch(db, sale.sucursal_id)
            db.refresh(sale)
            if sale.estado == SaleStatus.CANCELADA and intent.state == 'REFUNDED':
                return SaleService._format_sale_response(sale)
            if sale.estado != SaleStatus.COMPLETADA or db.query(ProductExchange.id).filter_by(venta_id=sale_id).first():
                raise HTTPException(409, 'La venta requiere conciliación por sus cambios o estado')
            if intent.state not in ('APPLIED','REFUND_PENDING','REFUNDED'):
                raise HTTPException(409, 'Estado de pago no reembolsable')
            creds = cls.credentials(db, user, intent)
            creds.pop('_credential_id',None)
            if cls.merchant(creds) != intent.merchant_key:
                raise HTTPException(409, 'La cuenta de cobro cambió; requiere conciliación')
            if intent.state != 'REFUNDED':
                intent.state = 'REFUND_PENDING'; db.commit()
                intent = db.query(PaymentIntent).filter_by(id=intent.id).with_for_update().one()
                if intent.state != 'REFUNDED':
                    if not cls.refund_remote(intent, creds):
                        db.commit()
                        raise HTTPException(409, 'Reembolso pendiente: la venta conserva su estado hasta confirmación')
                    db.commit()
            # A crash after remote refund is recoverable: repeat cancels the same local sale only once.
            return SaleService.cancel_sale(db, sale_id, reason, user.id)
        except Exception:
            db.rollback()
            raise
