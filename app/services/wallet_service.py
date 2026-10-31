import uuid
import hmac
import hashlib
import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user_wallet import UserWallet
from app.models.wallet_transaction import WalletTransaction
from app.models.sale import Sale, SaleType, SaleStatus, PaymentMethod
from app.models.sale_detail import SaleDetail
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.product_variant import ProductVariant
from app.models.branch import Branch
from app.models.user import User
from app.schemas.wallet import OfflineOrderPayload
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

WALLET_SECRET_KEY = "FASHIONSTORE_WALLET_SECRET_KEY_SECURE_2026"


class WalletService:
    @staticmethod
    def _compute_signature(user_id: uuid.UUID, saldo: Decimal, nonce: int) -> str:
        """Calcula el hash criptográfico HMAC-SHA256 para sellar el balance del cliente."""
        payload = f"{user_id}:{float(saldo):.2f}:{nonce}".encode("utf-8")
        return hmac.new(WALLET_SECRET_KEY.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    @staticmethod
    def verify_wallet_integrity(wallet: UserWallet) -> bool:
        """Verifica si la firma HMAC-SHA256 coincide con el balance y nonce almacenados."""
        expected = WalletService._compute_signature(wallet.user_id, wallet.saldo_actual, wallet.nonce_secuencia)
        return hmac.compare_digest(wallet.firma_balance or "", expected)

    @staticmethod
    def get_or_create_wallet(db: Session, user_id: uuid.UUID, tenant_id: uuid.UUID) -> UserWallet:
        wallet = db.query(UserWallet).filter(
            UserWallet.user_id == user_id,
            UserWallet.tenant_id == tenant_id
        ).first()

        if not wallet:
            zero_saldo = Decimal("0.00")
            initial_sig = WalletService._compute_signature(user_id, zero_saldo, 0)
            wallet = UserWallet(
                user_id=user_id,
                tenant_id=tenant_id,
                saldo_actual=zero_saldo,
                saldo_retenido=zero_saldo,
                nonce_secuencia=0,
                firma_balance=initial_sig,
                moneda="BOB"
            )
            db.add(wallet)
            db.commit()
            db.refresh(wallet)
        elif not wallet.firma_balance:
            wallet.firma_balance = WalletService._compute_signature(user_id, wallet.saldo_actual, wallet.nonce_secuencia)
            db.commit()
            db.refresh(wallet)

        return wallet

    @staticmethod
    def get_wallet_history(db: Session, wallet_id: uuid.UUID, limit: int = 30) -> List[WalletTransaction]:
        return (
            db.query(WalletTransaction)
            .filter(WalletTransaction.wallet_id == wallet_id)
            .order_by(WalletTransaction.created_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def topup_paypal(
        db: Session,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        order_id: str,
        monto_usd: float,
        monto_bs: Optional[float] = None
    ) -> UserWallet:
        # 1. Validar que la orden de PayPal no fue procesada previamente (Idempotencia)
        existing_tx = db.query(WalletTransaction).filter(
            WalletTransaction.referencia_externa == f"PAYPAL_{order_id}"
        ).first()
        if existing_tx:
            logger.info(f"Topup PayPal #{order_id} ya acreditado previamente.")
            return existing_tx.wallet

        # 2. Tipo de cambio oficial 6.96 Bs/USD
        monto_credito = Decimal(str(round(monto_bs if monto_bs else (monto_usd * 6.96), 2)))

        # 3. Bloqueo pesimista de billetera
        wallet = db.query(UserWallet).filter(
            UserWallet.user_id == user_id,
            UserWallet.tenant_id == tenant_id
        ).with_for_update().first()

        if not wallet:
            wallet = WalletService.get_or_create_wallet(db, user_id, tenant_id)

        saldo_ant = wallet.saldo_actual
        saldo_pos = saldo_ant + monto_credito
        wallet.saldo_actual = saldo_pos
        wallet.nonce_secuencia += 1
        wallet.firma_balance = WalletService._compute_signature(wallet.user_id, saldo_pos, wallet.nonce_secuencia)
        wallet.ultimo_sync_at = datetime.now(timezone.utc)

        # 4. Registrar movimiento contable
        tx = WalletTransaction(
            wallet_id=wallet.id,
            tenant_id=tenant_id,
            tipo="RECARGA_PAYPAL",
            monto=monto_credito,
            saldo_anterior=saldo_ant,
            saldo_posterior=saldo_pos,
            referencia_externa=f"PAYPAL_{order_id}",
            origen="MOBILE",
            estado_sync="CONCILIADO",
            notas=f"Recarga con PayPal de ${monto_usd:.2f} USD (T/C 6.96 Bs/USD)",
            metadatos={"order_id": order_id, "monto_usd": monto_usd}
        )
        db.add(tx)
        db.commit()
        db.refresh(wallet)

        # 5. Notificación Push al cliente
        try:
            NotificationService.create(
                db=db,
                user_id=user_id,
                title="Fondos Acreditados a tu Billetera",
                message=f"Se han acreditado Bs. {monto_credito:.2f} (${monto_usd:.2f} USD) a tu saldo prepago. Nuevo saldo: Bs. {saldo_pos:.2f}.",
                type="BILLETERA",
                reference_id=str(tx.id)
            )
        except Exception as err:
            logger.warning(f"Error emitiendo notif de recarga: {err}")

        return wallet

    @staticmethod
    def topup_cash_admin(
        db: Session,
        admin_user: User,
        cliente_id: uuid.UUID,
        monto_bs: float,
        notas: Optional[str] = None
    ) -> UserWallet:
        tenant_id = admin_user.tenant_id
        if not tenant_id:
            raise HTTPException(status_code=400, detail="El usuario administrativo no tiene empresa asignada.")

        monto_decimal = Decimal(str(round(monto_bs, 2)))

        wallet = db.query(UserWallet).filter(
            UserWallet.user_id == cliente_id,
            UserWallet.tenant_id == tenant_id
        ).with_for_update().first()

        if not wallet:
            wallet = WalletService.get_or_create_wallet(db, cliente_id, tenant_id)

        saldo_ant = wallet.saldo_actual
        saldo_pos = saldo_ant + monto_decimal
        wallet.saldo_actual = saldo_pos
        wallet.nonce_secuencia += 1
        wallet.firma_balance = WalletService._compute_signature(wallet.user_id, saldo_pos, wallet.nonce_secuencia)
        wallet.ultimo_sync_at = datetime.now(timezone.utc)

        tx = WalletTransaction(
            wallet_id=wallet.id,
            tenant_id=tenant_id,
            tipo="RECARGA_EFECTIVO_MOSTRADOR",
            monto=monto_decimal,
            saldo_anterior=saldo_ant,
            saldo_posterior=saldo_pos,
            referencia_externa=f"CAJA_REC_{datetime.utcnow().strftime('%Y%m%d%H%M')}",
            origen="WEB_ADMIN",
            estado_sync="CONCILIADO",
            notas=notas or f"Depósito en efectivo en mostrador recibido por {admin_user.full_name}",
            metadatos={"cajero_id": str(admin_user.id), "cajero_nombre": admin_user.full_name}
        )
        db.add(tx)
        db.commit()
        db.refresh(wallet)

        try:
            NotificationService.create(
                db=db,
                user_id=cliente_id,
                title="Recarga de Fondos en Sucursal 🏢",
                message=f"Se acreditaron Bs. {monto_decimal:.2f} en efectivo en mostrador. Tu saldo disponible es Bs. {saldo_pos:.2f}.",
                type="BILLETERA",
                reference_id=str(tx.id)
            )
        except Exception as err:
            logger.warning(f"Error emitiendo notif de recarga en caja: {err}")

        return wallet

    @staticmethod
    def sync_offline_orders_batch(
        db: Session,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        orders: List[OfflineOrderPayload]
    ) -> Dict[str, Any]:
        """
        Procesa el lote de compras offline realizadas con Billetera Virtual en modo Store-and-Forward.
        Garantiza atomicidad con SELECT FOR UPDATE, idempotencia estricta por UUID y conciliación contable.
        """
        wallet = db.query(UserWallet).filter(
            UserWallet.user_id == user_id,
            UserWallet.tenant_id == tenant_id
        ).with_for_update().first()

        if not wallet:
            wallet = WalletService.get_or_create_wallet(db, user_id, tenant_id)

        exitosas = 0
        fallidas = 0
        detalles_resultado = []
        notificaciones_pendientes = []

        for ord_data in orders:
            # 1. Idempotencia: Verificar si esta operación offline ya fue sincronizada
            existing_tx = db.query(WalletTransaction).filter(
                WalletTransaction.client_operation_id == ord_data.client_operation_id
            ).first()

            if existing_tx:
                detalles_resultado.append({
                    "client_operation_id": str(ord_data.client_operation_id),
                    "status": "CONFIRMADA",
                    "estado": "YA_PROCESADA",
                    "receipt_number": existing_tx.referencia_externa,
                    "numero_recibo": existing_tx.referencia_externa,
                    "mensaje": "Operación offline previamente sincronizada.",
                    "referencia": existing_tx.referencia_externa
                })
                exitosas += 1
                continue

            monto_orden = Decimal(str(round(ord_data.monto_total, 2)))

            # 2. Control de Sobregiro: Validar saldo disponible en base de datos central
            if wallet.saldo_actual < monto_orden:
                detalles_resultado.append({
                    "client_operation_id": str(ord_data.client_operation_id),
                    "status": "RECHAZADA_POR_SOBREGIRO",
                    "estado": "RECHAZADA_POR_SOBREGIRO",
                    "error_detail": f"Saldo central insuficiente (Disponible: {wallet.saldo_actual} Bs vs Requerido: {monto_orden} Bs).",
                    "mensaje": f"Saldo central insuficiente (Disponible: {wallet.saldo_actual} Bs vs Requerido: {monto_orden} Bs).",
                })
                fallidas += 1
                continue

            # 3. Validar disponibilidad de stock en la sucursal seleccionada
            stock_insuficiente = False
            items_procesables = []

            for item in ord_data.items:
                inv = db.query(Inventory).filter(
                    Inventory.sucursal_id == ord_data.sucursal_id,
                    Inventory.variante_id == item.variante_id
                ).first()

                if not inv or inv.stock_actual < item.cantidad:
                    stock_insuficiente = True
                    break
                items_procesables.append((inv, item))

            # Caso de Borde: Prenda agotada mientras el cliente estaba offline (Compensación Automática)
            if stock_insuficiente:
                detalles_resultado.append({
                    "client_operation_id": str(ord_data.client_operation_id),
                    "status": "COMPENSADA_POR_STOCK",
                    "estado": "COMPENSADA_POR_STOCK",
                    "error_detail": "Prenda agotada en sucursal durante el periodo offline. Fondos no debitados.",
                    "mensaje": "Prenda agotada en sucursal durante el periodo offline. Fondos no debitados.",
                })
                notificaciones_pendientes.append((
                    user_id,
                    "Aviso de Compra Offline ⚠️",
                    "Una prenda de tu compra offline se agotó en tienda. Tu saldo de Billetera no fue debitado.",
                    "COMPENSACION_STOCK",
                    str(ord_data.client_operation_id)
                ))
                fallidas += 1
                continue

            # 4. Generar Venta Oficial y descontar Kardex
            fecha_recibo = datetime.utcnow().strftime("%Y%m%d")
            short_id = uuid.uuid4().hex[:6].upper()
            num_recibo = f"VTA-WAL-{fecha_recibo}-{short_id}"
            auth_code = ord_data.codigo_autorizacion_offline or ord_data.firma_cliente or f"OFF-{str(ord_data.client_operation_id)[:8].upper()}"

            iva = round(monto_orden * Decimal("0.13"), 2)
            neto = monto_orden - iva

            nueva_venta = Sale(
                tenant_id=tenant_id,
                numero_recibo=num_recibo,
                tipo=SaleType.DIGITAL,
                estado=SaleStatus.COMPLETADA,
                sucursal_id=ord_data.sucursal_id,
                cliente_id=user_id,
                cajero_id=None,
                metodo_pago=PaymentMethod.PAYPAL,  # Clasificada como cobro digital garantizado
                referencia_pago=f"BILLETERA_AUTH_{auth_code}",
                monto_total=monto_orden,
                impuesto_iva=iva,
                monto_neto=neto,
                monto_recibido=monto_orden,
                cambio=Decimal("0.00"),
                nota=f"Compra Offline reconciliada vía Billetera Virtual (Auth: {auth_code})"
            )
            db.add(nueva_venta)
            db.flush()

            # Descontar stock y registrar salidas
            for inv, itm in items_procesables:
                stock_antes = inv.stock_actual
                inv.stock_actual = max(0, inv.stock_actual - itm.cantidad)

                mov = InventoryMovement(
                    tenant_id=tenant_id,
                    inventario_id=inv.id,
                    tipo=MovementType.SALIDA_VENTA,
                    cantidad=-itm.cantidad,
                    stock_antes=stock_antes,
                    stock_despues=inv.stock_actual,
                    referencia=num_recibo,
                    nota=f"Salida por venta offline con billetera {num_recibo}",
                    usuario_id=user_id
                )
                db.add(mov)

                det = SaleDetail(
                    venta_id=nueva_venta.id,
                    variante_id=itm.variante_id,
                    cantidad=itm.cantidad,
                    precio_unitario=Decimal(str(itm.precio_unitario)),
                    subtotal=Decimal(str(round(itm.precio_unitario * itm.cantidad, 2)))
                )
                db.add(det)

            # 5. Débito atómico de saldo en la Billetera
            saldo_ant = wallet.saldo_actual
            saldo_pos = saldo_ant - monto_orden
            wallet.saldo_actual = saldo_pos
            wallet.nonce_secuencia += 1
            wallet.firma_balance = WalletService._compute_signature(wallet.user_id, saldo_pos, wallet.nonce_secuencia)
            wallet.ultimo_sync_at = datetime.now(timezone.utc)

            tx = WalletTransaction(
                wallet_id=wallet.id,
                tenant_id=tenant_id,
                tipo="PAGO_ORDEN_OFFLINE",
                monto=-monto_orden,
                saldo_anterior=saldo_ant,
                saldo_posterior=saldo_pos,
                referencia_externa=num_recibo,
                client_operation_id=ord_data.client_operation_id,
                origen="MOBILE",
                estado_sync="CONCILIADO",
                notas=f"Pago offline regularizado. Comprobante #{num_recibo}",
                metadatos={"auth_code": auth_code, "recibo": num_recibo}
            )
            db.add(tx)

            detalles_resultado.append({
                "client_operation_id": str(ord_data.client_operation_id),
                "status": "CONFIRMADA",
                "estado": "CONFIRMADA",
                "receipt_number": num_recibo,
                "numero_recibo": num_recibo,
                "monto_debitado": float(monto_orden),
                "nuevo_saldo": float(saldo_pos)
            })
            exitosas += 1

            notificaciones_pendientes.append((
                user_id,
                "¡Compra Offline Sincronizada! 🎉",
                f"Tu compra offline #{num_recibo} por Bs. {float(monto_orden):.2f} ha sido consolidada con tus fondos de Billetera.",
                "VENTA",
                str(nueva_venta.id)
            ))

        db.commit()
        db.refresh(wallet)

        # Disparar notificaciones DESPUÉS del commit seguro
        for n_uid, n_title, n_msg, n_type, n_ref in notificaciones_pendientes:
            try:
                NotificationService.create(
                    db=db,
                    user_id=n_uid,
                    title=n_title,
                    message=n_msg,
                    type=n_type,
                    reference_id=n_ref
                )
            except Exception:
                pass

        return {
            "procesadas": len(orders),
            "exitosas": exitosas,
            "fallidas": fallidas,
            "nuevo_saldo": float(wallet.saldo_actual),
            "nuevo_nonce": wallet.nonce_secuencia,
            "nueva_firma": wallet.firma_balance or "",
            "detalles": detalles_resultado,
            "results": detalles_resultado
        }

