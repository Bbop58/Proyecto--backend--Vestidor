"""Ledger-backed wallet. Provider amounts and client balances are never trusted."""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select
from app.config import settings
from app.core.money import money, quote
from app.core.commercial_transaction import commercial_transaction, lock_branch
from app.models.user import User
from app.models.user_wallet import UserWallet
from app.models.wallet_transaction import WalletTransaction
from app.models.payment_intent import PaymentIntent
from app.models.branch import Branch
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.sale import Sale, SaleType, SaleStatus, PaymentMethod
from app.models.sale_detail import SaleDetail


class WalletService:
    @staticmethod
    def _compute_signature(user_id, saldo, nonce, tenant_id=None, retained=0):
        key = settings.WALLET_SIGNING_KEY
        if not key:
            raise HTTPException(503, 'Configura una clave privada para la billetera')
        payload = f'{tenant_id}:{user_id}:BOB:{money(saldo):.2f}:{money(retained):.2f}:{nonce}'.encode()
        return hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _seal(wallet):
        wallet.firma_balance = WalletService._compute_signature(wallet.user_id, wallet.saldo_actual,
            wallet.nonce_secuencia, wallet.tenant_id, wallet.saldo_retenido)

    @staticmethod
    def verify_wallet_integrity(wallet):
        if wallet.moneda != 'BOB' or wallet.nonce_secuencia < 0:
            return False
        expected = WalletService._compute_signature(wallet.user_id, wallet.saldo_actual,
            wallet.nonce_secuencia, wallet.tenant_id, wallet.saldo_retenido)
        return hmac.compare_digest(wallet.firma_balance or '', expected)

    @staticmethod
    def _lock_user(db, user_id, tenant_id):
        user = db.query(User).filter_by(id=user_id, tenant_id=tenant_id, is_active=True).with_for_update(of=User).first()
        if not user:
            raise HTTPException(404, 'Cliente no disponible en esta empresa')
        return user

    @staticmethod
    def _locked_wallet(db, user_id, tenant_id):
        WalletService._lock_user(db, user_id, tenant_id)
        wallet = (db.query(UserWallet).filter_by(user_id=user_id, tenant_id=tenant_id)
                  .populate_existing().with_for_update(of=UserWallet).first())
        if not wallet:
            wallet = UserWallet(user_id=user_id, tenant_id=tenant_id, saldo_actual=Decimal('0.00'),
                saldo_retenido=Decimal('0.00'), nonce_secuencia=0, moneda='BOB')
            WalletService._seal(wallet)
            db.add(wallet); db.flush()
        elif not WalletService.verify_wallet_integrity(wallet):
            raise HTTPException(409, 'La billetera requiere conciliación de integridad; no se modificó su saldo')
        return wallet

    @staticmethod
    @commercial_transaction
    def get_or_create_wallet(db, user_id, tenant_id):
        wallet = WalletService._locked_wallet(db, user_id, tenant_id)
        db.commit(); db.refresh(wallet)
        return wallet

    @staticmethod
    def get_wallet_history(db, wallet_id, limit=30):
        return db.query(WalletTransaction).filter_by(wallet_id=wallet_id).order_by(WalletTransaction.created_at.desc()).limit(limit).all()

    @staticmethod
    def fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()

    @staticmethod
    def _existing(db, key, wallet, digest):
        tx = db.query(WalletTransaction).filter_by(client_operation_id=key).first()
        if tx:
            if tx.wallet_id != wallet.id or tx.tenant_id != wallet.tenant_id or (tx.metadatos or {}).get('request_hash') != digest:
                raise HTTPException(409, 'La clave de operación ya corresponde a otro contenido o cuenta')
            return tx
        if db.execute(select(WalletTransaction.__table__.c.id).where(WalletTransaction.__table__.c.client_operation_id == key)).first():
            raise HTTPException(409, 'La clave de operación no está disponible')
        return None

    @staticmethod
    @commercial_transaction
    def topup_paypal(db, user_id, tenant_id, order_id, monto_usd, monto_bs=None, _payment_intent=None):
        intent = _payment_intent
        if (not isinstance(intent, PaymentIntent) or intent.state != 'CAPTURED' or intent.purpose != 'WALLET'
            or intent.user_id != user_id or intent.tenant_id != tenant_id or intent.provider_order_id != order_id or not intent.capture_id):
            raise HTTPException(409, 'La recarga requiere una captura verificada por el servidor')
        wallet = WalletService._locked_wallet(db, user_id, tenant_id)
        before = wallet.saldo_actual
        credit = money(intent.amount_bob, positive=True)
        wallet.saldo_actual += credit; wallet.nonce_secuencia += 1
        wallet.ultimo_sync_at = datetime.now(timezone.utc)
        WalletService._seal(wallet)
        db.add(WalletTransaction(wallet_id=wallet.id, tenant_id=tenant_id, tipo='RECARGA_PAYPAL',
            monto=credit, saldo_anterior=before, saldo_posterior=wallet.saldo_actual,
            payment_intent_id=intent.id, referencia_externa=f'PAYPAL_{intent.capture_id}',
            origen='SERVER', estado_sync='CONCILIADO', metadatos={'order_id': order_id}))
        intent.state = 'APPLIED'
        db.commit(); db.refresh(wallet)
        return wallet

    @staticmethod
    @commercial_transaction
    def topup_cash_admin(db, admin_user, cliente_id, monto_bs, notas=None, client_operation_id=None):
        from app.services.role_service import RoleService
        if 'ventas.create' not in RoleService.get_permission_codes(db, admin_user.role_id):
            raise HTTPException(403, 'La recarga requiere autorización de caja')
        credit = money(monto_bs, positive=True)
        wallet = WalletService._locked_wallet(db, cliente_id, admin_user.tenant_id)
        key = client_operation_id or uuid.uuid4()
        digest = WalletService.fingerprint({'type': 'CASH', 'amount': str(credit), 'actor': str(admin_user.id)})
        existing = WalletService._existing(db, key, wallet, digest)
        if existing:
            return wallet
        before = wallet.saldo_actual
        wallet.saldo_actual += credit; wallet.nonce_secuencia += 1
        wallet.ultimo_sync_at = datetime.now(timezone.utc)
        WalletService._seal(wallet)
        db.add(WalletTransaction(wallet_id=wallet.id, tenant_id=wallet.tenant_id, tipo='RECARGA_EFECTIVO_MOSTRADOR',
            monto=credit, saldo_anterior=before, saldo_posterior=wallet.saldo_actual, client_operation_id=key,
            referencia_externa=f'CAJA_REC_{key}', origen='WEB_ADMIN', estado_sync='CONCILIADO', notas=notas,
            metadatos={'cajero_id': str(admin_user.id), 'request_hash': digest}))
        db.commit(); db.refresh(wallet)
        return wallet

    @staticmethod
    @commercial_transaction
    def sync_offline_orders_batch(db, user_id, tenant_id, orders):
        WalletService._lock_user(db, user_id, tenant_id)  # User -> branches -> wallet throughout.
        for branch_id in sorted({o.sucursal_id for o in orders}, key=str):
            branch = db.query(Branch).filter_by(id=branch_id, tenant_id=tenant_id, activa=True).first()
            if not branch:
                raise HTTPException(404, 'Sucursal no disponible')
            lock_branch(db, branch_id)
        wallet = WalletService._locked_wallet(db, user_id, tenant_id)
        results = []; successful = failed = 0
        for order in orders:
            digest = WalletService.fingerprint(order.model_dump(mode='json', exclude={'fecha_local','fecha_orden'}))
            try:
                existing = WalletService._existing(db, order.client_operation_id, wallet, digest)
                if existing:
                    results.append(dict(client_operation_id=str(order.client_operation_id), status='CONFIRMADA',
                        estado='YA_PROCESADA', receipt_number=existing.referencia_externa, numero_recibo=existing.referencia_externa))
                    successful += 1
                    continue
                pricing = quote(db, order.items, tenant_id, getattr(order, 'codigo_cupon', None))
                if money(order.monto_total) != pricing['total']:
                    raise HTTPException(409, 'El importe no coincide con los precios del servidor')
                if wallet.saldo_actual-wallet.saldo_retenido < pricing['total']:
                    raise HTTPException(400, 'RECHAZADA_POR_SOBREGIRO')
                quantities = {}
                for item in order.items: quantities[item.variante_id] = quantities.get(item.variante_id, 0)+item.cantidad
                for variant, quantity in quantities.items():
                    inv = db.query(Inventory).filter_by(sucursal_id=order.sucursal_id, variante_id=variant, activo=True).first()
                    if not inv or inv.stock_disponible < quantity:
                        raise HTTPException(400, 'COMPENSADA_POR_STOCK')
            except HTTPException as error:
                code = error.detail if error.detail in ('RECHAZADA_POR_SOBREGIRO','COMPENSADA_POR_STOCK') else 'CONFLICTO'
                results.append(dict(client_operation_id=str(order.client_operation_id), status=code, estado=code,
                                    error_detail=error.detail, mensaje=error.detail))
                failed += 1
                continue
            pricing = quote(db, order.items, tenant_id, getattr(order, 'codigo_cupon', None), consume=True)
            total = pricing['total']; tax = money(total*Decimal('.13'))
            sale = Sale(tenant_id=tenant_id, sucursal_id=order.sucursal_id, cliente_id=user_id,
                numero_recibo=f'VTA-WAL-{uuid.uuid4().hex[:20].upper()}', tipo=SaleType.DIGITAL,
                estado=SaleStatus.COMPLETADA, metodo_pago=PaymentMethod.BILLETERA,
                referencia_pago=f'WALLET_{order.client_operation_id}', monto_subtotal=pricing['subtotal'],
                descuento=pricing['descuento'], codigo_cupon=pricing['codigo_cupon'], monto_total=total,
                impuesto_iva=tax, monto_neto=total-tax, monto_recibido=total, cambio=0)
            db.add(sale); db.flush()
            for row in pricing['items']:
                variant_id = uuid.UUID(row['variante_id']); quantity = row['cantidad']; price = money(row['precio_unitario'])
                inv = db.query(Inventory).filter_by(sucursal_id=order.sucursal_id, variante_id=variant_id).one()
                before_stock = inv.stock_actual; inv.stock_actual -= quantity
                db.add(InventoryMovement(tenant_id=tenant_id, inventario_id=inv.id, tipo=MovementType.SALIDA_VENTA,
                    cantidad=quantity, stock_antes=before_stock, stock_despues=inv.stock_actual,
                    referencia=sale.numero_recibo, usuario_id=user_id))
                db.add(SaleDetail(venta_id=sale.id, variante_id=variant_id, cantidad=quantity,
                                  precio_unitario=price, subtotal=price*quantity))
            before = wallet.saldo_actual; wallet.saldo_actual -= total; wallet.nonce_secuencia += 1
            wallet.ultimo_sync_at = datetime.now(timezone.utc); WalletService._seal(wallet)
            db.add(WalletTransaction(wallet_id=wallet.id, tenant_id=tenant_id, tipo='PAGO_ORDEN_OFFLINE',
                monto=-total, saldo_anterior=before, saldo_posterior=wallet.saldo_actual,
                client_operation_id=order.client_operation_id, referencia_externa=sale.numero_recibo,
                origen='MOBILE', estado_sync='CONCILIADO',
                metadatos={'sale_id': str(sale.id), 'request_hash': digest}))
            db.flush()
            successful += 1
            results.append(dict(client_operation_id=str(order.client_operation_id), status='CONFIRMADA', estado='CONFIRMADA',
                receipt_number=sale.numero_recibo, numero_recibo=sale.numero_recibo, nuevo_saldo=float(wallet.saldo_actual)))
        db.commit(); db.refresh(wallet)
        return dict(procesadas=len(orders), exitosas=successful, fallidas=failed, nuevo_saldo=float(wallet.saldo_actual),
            nuevo_nonce=wallet.nonce_secuencia, nueva_firma=wallet.firma_balance, detalles=results, results=results)

    @staticmethod
    def refund_sale(db, sale, wallet_tx):
        wallet = WalletService._locked_wallet(db, sale.cliente_id, sale.tenant_id)
        if wallet.id != wallet_tx.wallet_id or money(-wallet_tx.monto) != money(sale.monto_total):
            raise HTTPException(409, 'La venta requiere conciliación de billetera')
        key = uuid.uuid5(sale.id, 'wallet-refund')
        if db.query(WalletTransaction.id).filter_by(client_operation_id=key).first():
            raise HTTPException(409, 'La devolución ya fue aplicada')
        before = wallet.saldo_actual; wallet.saldo_actual += money(sale.monto_total); wallet.nonce_secuencia += 1
        WalletService._seal(wallet)
        db.add(WalletTransaction(wallet_id=wallet.id, tenant_id=wallet.tenant_id, tipo='REEMBOLSO_DEVOLUCION_PRENDA',
            monto=money(sale.monto_total), saldo_anterior=before, saldo_posterior=wallet.saldo_actual,
            client_operation_id=key, referencia_externa=f'REFUND_{sale.numero_recibo}', origen='SERVER', estado_sync='CONCILIADO'))


    @staticmethod
    def credit_exchange(db, user_id, tenant_id, amount, exchange_id):
        wallet = WalletService._locked_wallet(db, user_id, tenant_id)
        credit = money(amount, positive=True)
        before = wallet.saldo_actual
        wallet.saldo_actual += credit; wallet.nonce_secuencia += 1
        WalletService._seal(wallet)
        db.add(WalletTransaction(wallet_id=wallet.id, tenant_id=tenant_id, tipo='REEMBOLSO_DEVOLUCION_PRENDA',
            monto=credit, saldo_anterior=before, saldo_posterior=wallet.saldo_actual,
            client_operation_id=uuid.uuid5(exchange_id, 'exchange-credit'), referencia_externa=f'CAMBIO_{exchange_id}',
            origen='SERVER', estado_sync='CONCILIADO', metadatos={'exchange_id': str(exchange_id)}))
