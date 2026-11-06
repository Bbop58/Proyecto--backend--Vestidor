import uuid
import logging
from decimal import Decimal, ROUND_HALF_UP
from collections import Counter
from app.core.money import quote, money
from app.models.payment_intent import PaymentIntent
from app.core.commercial_transaction import commercial_transaction, lock_branch
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, case

from app.models.branch import Branch
from app.models.tenant import Tenant
from app.models.user import User
from app.models.product_variant import ProductVariant
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.reservation import Reservation, ReservationStatus
from app.models.sale import Sale, SaleType, SaleStatus, PaymentMethod, utc_now
from app.models.sale_detail import SaleDetail
from app.schemas.sale import (
    PresencialSaleCreate,
    DigitalSaleCreate,
    SaleResponse,
    SalesSummaryReport,
    TopProductReport,
)

logger = logging.getLogger(__name__)


class SaleService:

    @staticmethod
    def _attach_payment(sale, intent):
        if intent is None:
            return
        if (intent.state != 'CAPTURED' or intent.purpose != 'SALE' or not intent.capture_id
                or intent.tenant_id != sale.tenant_id or intent.sale_id is not None
                or intent.sucursal_id != sale.sucursal_id or intent.cliente_id != sale.cliente_id
                or money(intent.amount_bob) != money(sale.monto_total)):
            raise HTTPException(409, "El cobro no corresponde a esta venta")
        intent.sale_id = sale.id
        intent.state = 'APPLIED'

    @staticmethod
    def _actor_tenant(db: Session, actor_id: uuid.UUID):
        actor = db.query(User).filter(User.id == actor_id, User.is_active == True).first()
        if not actor or not actor.tenant_id:
            raise HTTPException(status_code=403, detail="La cuenta debe pertenecer a una empresa")
        if not db.query(Tenant.id).filter(Tenant.id == actor.tenant_id, Tenant.activo == True).first():
            proof=db.info.get('contract_reconciliation')
            if not proof or proof[:2]!=(actor.id,actor.tenant_id):
                raise HTTPException(status_code=403, detail="Empresa inactiva o no disponible")
        return actor.tenant_id

    @staticmethod
    def _validate_scope(db: Session, data, tenant_id, cliente_id=None, digital=False):
        branch = db.query(Branch).filter_by(id=data.sucursal_id, tenant_id=tenant_id, activa=True).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Sucursal no encontrada o inactiva")
        if cliente_id and not db.query(User.id).filter_by(id=cliente_id,tenant_id=tenant_id,is_active=True).first():
            raise HTTPException(status_code=404, detail="Cliente no encontrado o inactivo")
        lock_branch(db, branch.id)
        # Validate the whole payload before adding a header or changing any stock.
        for item in data.items:
            variant = db.query(ProductVariant).filter_by(id=item.variante_id,activo=True).first()
            if not variant or not variant.producto or variant.producto.tenant_id != tenant_id or not variant.producto.activo:
                raise HTTPException(status_code=404, detail="Variante no encontrada o inactiva")
            if not db.query(Inventory.id).filter_by(sucursal_id=branch.id,
                    variante_id=item.variante_id,tenant_id=tenant_id,activo=True).first():
                raise HTTPException(status_code=400, detail="Inventario no disponible en esta empresa")
            if item.reserva_id:
                if digital:
                    raise HTTPException(status_code=400, detail="La compra digital no liquida reservas")
                reservation = db.query(Reservation).filter_by(id=item.reserva_id,tenant_id=tenant_id,sucursal_id=branch.id).first()
                if not reservation or not db.query(User.id).filter_by(id=reservation.cliente_id,tenant_id=tenant_id).first():
                    raise HTTPException(status_code=404, detail="Reserva no encontrada")
                if cliente_id and reservation.cliente_id != cliente_id:
                    raise HTTPException(status_code=400, detail="La reserva pertenece a otro cliente")
        return branch

    @staticmethod
    def _format_sale_response(venta: Sale) -> dict:
        detalles_resp = []
        for d in venta.detalles:
            var = d.variante
            prod = var.producto if var else None
            detalles_resp.append({
                "id": d.id,
                "venta_id": d.venta_id,
                "variante_id": d.variante_id,
                "variante_sku": var.sku if var else None,
                "producto_nombre": prod.nombre if prod else None,
                "talla": var.talla if var else None,
                "color": var.color if var else None,
                "reserva_id": d.reserva_id,
                "cantidad": d.cantidad,
                "precio_unitario": float(d.precio_unitario),
                "subtotal": float(d.subtotal),
                "created_at": d.created_at,
            })

        return {
            "id": venta.id,
            "numero_recibo": venta.numero_recibo,
            "tipo": venta.tipo,
            "estado": venta.estado,
            "sucursal_id": venta.sucursal_id,
            "sucursal_nombre": venta.sucursal.nombre if venta.sucursal else None,
            "sucursal_ciudad": venta.sucursal.ciudad if venta.sucursal else None,
            "cliente_id": venta.cliente_id,
            "cliente_nombre": venta.cliente.full_name if venta.cliente else "Cliente Casual / Anónimo",
            "cajero_id": venta.cajero_id,
            "cajero_nombre": venta.cajero.full_name if venta.cajero else None,
            "metodo_pago": venta.metodo_pago,
            "referencia_pago": venta.referencia_pago,
            "monto_total": float(venta.monto_total),
            "total": float(venta.monto_total),
            "impuesto_iva": float(venta.impuesto_iva if venta.impuesto_iva is not None else round(float(venta.monto_total) * 0.13, 2)),
            "monto_neto": float(venta.monto_neto if venta.monto_neto is not None else round(float(venta.monto_total) * 0.87, 2)),
            "monto_recibido": float(venta.monto_recibido) if venta.monto_recibido is not None else None,
            "cambio": float(venta.cambio) if venta.cambio is not None else 0.0,
            "nota": venta.nota,
            "motivo_cancelacion": venta.motivo_cancelacion,
            "detalles": detalles_resp,
            "created_at": venta.created_at,
            "updated_at": venta.updated_at,
        }

    @staticmethod
    @commercial_transaction
    def create_presencial_sale(
        db: Session,
        data: PresencialSaleCreate,
        cajero_id: Optional[uuid.UUID] = None,
        actor_id: Optional[uuid.UUID] = None,
        _payment_intent: Optional[PaymentIntent] = None,
        _commit: bool = True,
        _created_at=None
    ) -> dict:
        tenant_id = SaleService._actor_tenant(db, actor_id or cajero_id)
        SaleService._validate_scope(db, data, tenant_id, data.cliente_id)
        if data.pago.metodo == PaymentMethod.BILLETERA:
            raise HTTPException(409, "Usa el flujo de billetera que valida y descuenta el saldo")
        if cajero_id and SaleService._actor_tenant(db, cajero_id) != tenant_id:
            raise HTTPException(status_code=403, detail="Cajero de otra empresa")
        if data.pago.metodo == PaymentMethod.PAYPAL and _payment_intent is None:
            raise HTTPException(409, "PayPal requiere una captura verificada por el servidor")
        pricing = quote(db, data.items, tenant_id, data.codigo_cupon, consume=True)
        # 1. Validar sucursal
        branch = db.query(Branch).filter(Branch.id == data.sucursal_id, Branch.activa == True).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Sucursal no encontrada o inactiva")

        # 2. Validar cliente si se especificó
        if data.cliente_id:
            cliente = db.query(User).filter(User.id == data.cliente_id, User.is_active == True).first()
            if not cliente:
                raise HTTPException(status_code=404, detail="Cliente no encontrado o inactivo")

        now = utc_now()
        prefix = "VTA-OFF" if not _commit else "VTA"
        numero_recibo = f"{prefix}-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        # Determinar tipo de venta (si no hay cajero y es PayPal/QR se considera DIGITAL)
        sale_type = SaleType.DIGITAL if not cajero_id and data.pago.metodo in [PaymentMethod.PAYPAL, PaymentMethod.QR] else SaleType.PRESENCIAL

        reservations = {}
        expected_client = data.cliente_id
        for reservation_id in sorted({i.reserva_id for i in data.items if i.reserva_id}, key=str):
            reservation = db.query(Reservation).filter_by(id=reservation_id).populate_existing().first()
            if (not reservation or not reservation.activo or
                    reservation.estado not in (ReservationStatus.PENDIENTE, ReservationStatus.PREPARADA) or
                    reservation.fecha_expiracion <= now):
                raise HTTPException(400, "La reserva no está vigente para su liquidación")
            if expected_client and reservation.cliente_id != expected_client:
                raise HTTPException(400, "Todas las reservas deben pertenecer al mismo cliente")
            expected_client = reservation.cliente_id
            requested = Counter()
            reserved = Counter()
            for item in data.items:
                if item.reserva_id == reservation_id:
                    requested[item.variante_id] += item.cantidad
            for detail in reservation.detalles:
                reserved[detail.variante_id] += detail.cantidad
            if requested != reserved:
                raise HTTPException(400, "La venta debe liquidar todos los detalles y cantidades de la reserva")
            reservations[reservation_id] = reservation

        # 3. Crear cabecera de venta
        venta = Sale(
            tenant_id=branch.tenant_id,
            numero_recibo=numero_recibo,
            tipo=sale_type,
            estado=SaleStatus.COMPLETADA,
            sucursal_id=data.sucursal_id,
            cliente_id=data.cliente_id,
            cajero_id=cajero_id,
            metodo_pago=data.pago.metodo,
            referencia_pago=data.pago.referencia,
            monto_total=0.0,
            monto_recibido=data.pago.monto_recibido,
            cambio=0.0,
            nota=data.nota
        )
        db.add(venta)
        db.flush()

        monto_total = Decimal("0.00")

        # 4. Procesar cada ítem
        prices = iter(pricing["items"])
        for item in data.items:
            variant = db.query(ProductVariant).filter(
                ProductVariant.id == item.variante_id,
                ProductVariant.activo == True
            ).first()
            if not variant:
                raise HTTPException(
                    status_code=400,
                    detail=f"Variante {item.variante_id} no encontrada o inactiva"
                )

            inv = db.query(Inventory).filter(
                Inventory.sucursal_id == data.sucursal_id,
                Inventory.variante_id == item.variante_id,
                Inventory.activo == True
            ).first()

            if not inv:
                raise HTTPException(
                    status_code=400,
                    detail=f"No hay inventario registrado para {variant.producto.nombre} en esta sucursal"
                )

            # Caso A: Viene de una Reserva
            if item.reserva_id:
                reserva = reservations[item.reserva_id]
                if not venta.cliente_id:
                    venta.cliente_id = reserva.cliente_id
                if inv.stock_reservado < item.cantidad or inv.stock_actual < item.cantidad:
                    raise HTTPException(409, "El inventario no concuerda con la reserva")
                inv.stock_reservado -= item.cantidad
            else:
                # Caso B: Venta directa de mostrador -> validar stock disponible
                if inv.stock_disponible < item.cantidad:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Stock insuficiente para '{variant.producto.nombre}' ({variant.talla}/{variant.color}). Solicitado: {item.cantidad}, Disponible: {inv.stock_disponible}"
                    )

            # Descontar stock actual
            stock_antes = inv.stock_actual
            inv.stock_actual -= item.cantidad

            # Registrar movimiento de salida por venta
            mov = InventoryMovement(
                tenant_id=tenant_id,
                inventario_id=inv.id,
                tipo=MovementType.SALIDA_VENTA,
                cantidad=item.cantidad,
                stock_antes=stock_antes,
                stock_despues=inv.stock_actual,
                referencia=numero_recibo,
                nota=f"Venta presencial ({venta.metodo_pago.value})",
                usuario_id=cajero_id
            )
            db.add(mov)

            # Determinar precio
            precio_unit = money(next(prices)["precio_unitario"])
            subtotal = precio_unit * item.cantidad
            monto_total += subtotal

            # Crear detalle de venta
            detalle = SaleDetail(
                venta_id=venta.id,
                variante_id=item.variante_id,
                reserva_id=item.reserva_id,
                cantidad=item.cantidad,
                precio_unitario=precio_unit,
                subtotal=subtotal
            )
            db.add(detalle)

        for reservation in reservations.values():
            reservation.estado = ReservationStatus.COMPLETADA

        # 5. Validar pago en efectivo, cambio y cálculo de impuestos (13% IVA y 87% Ganancia neta)
        monto_total = pricing["total"]
        venta.descuento = pricing["descuento"]
        venta.codigo_cupon = pricing["codigo_cupon"]
        iva = (monto_total * Decimal("0.13")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        neto = round(monto_total - iva, 2)
        venta.monto_subtotal = pricing["subtotal"]
        venta.monto_total = monto_total
        venta.impuesto_iva = iva
        venta.monto_neto = neto

        if data.pago.metodo == PaymentMethod.EFECTIVO and data.pago.monto_recibido is not None:
            recibido = Decimal(str(data.pago.monto_recibido)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if recibido < monto_total:
                raise HTTPException(
                    status_code=400,
                    detail=f"El monto recibido ({data.pago.monto_recibido} Bs.) es menor al total ({monto_total} Bs.)"
                )
            venta.monto_recibido = recibido
            venta.cambio = recibido - monto_total

        if _created_at is not None:
            venta.created_at = _created_at
        SaleService._attach_payment(venta, _payment_intent)
        if not _commit:
            db.flush()
            return SaleService._format_sale_response(venta)
        db.commit()
        db.refresh(venta)

        # Disparar notificación push interna si hay cliente asignado
        if venta.cliente_id:
            try:
                from app.services.notification_service import NotificationService
                NotificationService.create(
                    db=db,
                    user_id=venta.cliente_id,
                    title="¡Compra Presencial Exitosa!",
                    message=f"Tu compra #{venta.numero_recibo} por Bs {float(venta.monto_total):.2f} ha sido procesada con éxito en {venta.sucursal.nombre if venta.sucursal else 'tienda'}.",
                    type="VENTA",
                    reference_id=str(venta.id)
                )
            except Exception as notif_err:
                logger.warning(f"Aviso: no se pudo emitir notificación de venta presencial: {notif_err}")

        return SaleService._format_sale_response(venta)

    @staticmethod
    @commercial_transaction
    def create_digital_sale(
        db: Session,
        data: DigitalSaleCreate,
        cliente_id: uuid.UUID,
        _payment_intent: Optional[PaymentIntent] = None
    ) -> dict:
        tenant_id = SaleService._actor_tenant(db, cliente_id)
        SaleService._validate_scope(db, data, tenant_id, cliente_id, digital=True)
        if _payment_intent is None:
            raise HTTPException(409, "Completa el pago mediante la pasarela; una referencia no acredita el cobro")
        pricing = quote(db, data.items, tenant_id, data.codigo_cupon, consume=True)
        branch = db.query(Branch).filter(Branch.id == data.sucursal_id, Branch.activa == True).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Sucursal no encontrada o inactiva")

        now = utc_now()
        numero_recibo = f"DIG-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        venta = Sale(
            tenant_id=tenant_id,
            numero_recibo=numero_recibo,
            tipo=SaleType.DIGITAL,
            estado=SaleStatus.COMPLETADA,
            sucursal_id=data.sucursal_id,
            cliente_id=cliente_id,
            cajero_id=None,
            metodo_pago=PaymentMethod.PAYPAL,
            referencia_pago=f"PAYPAL_{_payment_intent.capture_id}",
            monto_total=0.0,
            nota=data.nota
        )
        db.add(venta)
        db.flush()

        monto_total = Decimal("0.00")
        prices = iter(pricing["items"])
        for item in data.items:
            variant = db.query(ProductVariant).filter(
                ProductVariant.id == item.variante_id,
                ProductVariant.activo == True
            ).first()
            if not variant:
                raise HTTPException(status_code=400, detail=f"Variante {item.variante_id} no encontrada o inactiva")

            inv = db.query(Inventory).filter(
                Inventory.sucursal_id == data.sucursal_id,
                Inventory.variante_id == item.variante_id,
                Inventory.activo == True
            ).first()

            if not inv or inv.stock_disponible < item.cantidad:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para {variant.producto.nombre}")

            stock_antes = inv.stock_actual
            inv.stock_actual -= item.cantidad

            mov = InventoryMovement(
                tenant_id=tenant_id,
                inventario_id=inv.id,
                tipo=MovementType.SALIDA_VENTA,
                cantidad=item.cantidad,
                stock_antes=stock_antes,
                stock_despues=inv.stock_actual,
                referencia=numero_recibo,
                nota="Venta digital / App Móvil",
                usuario_id=cliente_id
            )
            db.add(mov)

            precio_unit = money(next(prices)["precio_unitario"])
            subtotal = precio_unit * item.cantidad
            monto_total += subtotal

            detalle = SaleDetail(
                venta_id=venta.id,
                variante_id=item.variante_id,
                cantidad=item.cantidad,
                precio_unitario=precio_unit,
                subtotal=subtotal
            )
            db.add(detalle)

        monto_total = pricing["total"]
        venta.descuento = pricing["descuento"]
        venta.codigo_cupon = pricing["codigo_cupon"]
        iva = (monto_total * Decimal("0.13")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        neto = round(monto_total - iva, 2)
        venta.monto_subtotal = pricing["subtotal"]
        venta.monto_total = monto_total
        venta.impuesto_iva = iva
        venta.monto_neto = neto
        SaleService._attach_payment(venta, _payment_intent)
        db.commit()
        db.refresh(venta)

        # Disparar notificación push interna si hay cliente asignado
        if venta.cliente_id:
            try:
                from app.services.notification_service import NotificationService
                NotificationService.create(
                    db=db,
                    user_id=venta.cliente_id,
                    title="¡Compra Presencial Exitosa!",
                    message=f"Tu compra #{venta.numero_recibo} por Bs {float(venta.monto_total):.2f} ha sido procesada con éxito en {venta.sucursal.nombre if venta.sucursal else 'tienda'}.",
                    type="VENTA",
                    reference_id=str(venta.id)
                )
            except Exception as notif_err:
                logger.warning(f"Aviso: no se pudo emitir notificación de venta presencial: {notif_err}")

        return SaleService._format_sale_response(venta)

    @staticmethod
    def get_sale_by_id(db: Session, venta_id: uuid.UUID, tenant_id: Optional[uuid.UUID] = None) -> dict:
        q = db.query(Sale).filter(Sale.id == venta_id)
        if tenant_id:
            q = q.filter(Sale.tenant_id == tenant_id)
        venta = q.first()
        if not venta:
            raise HTTPException(status_code=404, detail="Venta no encontrada")
        return SaleService._format_sale_response(venta)

    @staticmethod
    def get_sales(
        db: Session,
        sucursal_id: Optional[uuid.UUID] = None,
        tipo: Optional[SaleType] = None,
        estado: Optional[SaleStatus] = None,
        cajero_id: Optional[uuid.UUID] = None,
        cliente_id: Optional[uuid.UUID] = None,
        limit: int = 100,
        offset: int = 0,
        tenant_id: Optional[uuid.UUID] = None
    ) -> List[dict]:
        q = db.query(Sale)
        if tenant_id:
            q = q.filter(Sale.tenant_id == tenant_id)
        if sucursal_id:
            q = q.filter(Sale.sucursal_id == sucursal_id)
        if tipo:
            q = q.filter(Sale.tipo == tipo)
        if estado:
            q = q.filter(Sale.estado == estado)
        if cajero_id:
            q = q.filter(Sale.cajero_id == cajero_id)
        if cliente_id:
            q = q.filter(Sale.cliente_id == cliente_id)

        ventas = q.order_by(Sale.created_at.desc()).offset(offset).limit(limit).all()
        return [SaleService._format_sale_response(v) for v in ventas]

    @staticmethod
    @commercial_transaction
    def cancel_sale(
        db: Session,
        venta_id: uuid.UUID,
        motivo: str,
        usuario_id: uuid.UUID
    ) -> dict:
        tenant_id = SaleService._actor_tenant(db, usuario_id)
        venta = db.query(Sale).filter(Sale.id == venta_id, Sale.tenant_id == tenant_id).first()
        if not venta:
            raise HTTPException(status_code=404, detail="Venta no encontrada")

        from app.models.wallet_transaction import WalletTransaction
        from app.services.wallet_service import WalletService
        wallet_tx = db.query(WalletTransaction).filter(
            WalletTransaction.tenant_id == tenant_id,
            WalletTransaction.tipo.in_(['PAGO_ORDEN_OFFLINE','PAGO_ORDEN_ONLINE']),
            WalletTransaction.referencia_externa == venta.numero_recibo).first()
        if wallet_tx:
            WalletService._lock_user(db, venta.cliente_id, tenant_id)
        elif venta.metodo_pago == PaymentMethod.BILLETERA:
            raise HTTPException(409, "La venta no tiene un débito de billetera conciliable")
        lock_branch(db, venta.sucursal_id)
        db.refresh(venta)
        intent = db.query(PaymentIntent).filter_by(sale_id=venta.id, tenant_id=tenant_id).first()
        if venta.metodo_pago == PaymentMethod.PAYPAL and not wallet_tx:
            if not intent or intent.state != 'REFUNDED':
                raise HTTPException(409, 'La venta requiere reembolso confirmado de la pasarela antes de anular')
        from app.models.product_exchange import ProductExchange
        if db.query(ProductExchange.id).filter_by(venta_id=venta.id).first():
            raise HTTPException(409, "No se puede anular una venta con cambios de prendas; requiere conciliación")

        if venta.estado != SaleStatus.COMPLETADA:
            raise HTTPException(
                status_code=400,
                detail=f"Solo se pueden anular ventas en estado COMPLETADA (estado actual: {venta.estado.value})"
            )

        # Revertir stock mediante DEVOLUCION
        for detalle in venta.detalles:
            inv = db.query(Inventory).filter(
                Inventory.sucursal_id == venta.sucursal_id,
                Inventory.variante_id == detalle.variante_id
            ).first()

            if not inv:
                raise HTTPException(409, "Falta el inventario necesario para anular la venta")
            if inv:
                stock_antes = inv.stock_actual
                inv.stock_actual += detalle.cantidad
                mov = InventoryMovement(
                    tenant_id=tenant_id,
                    inventario_id=inv.id,
                    tipo=MovementType.DEVOLUCION,
                    cantidad=detalle.cantidad,
                    stock_antes=stock_antes,
                    stock_despues=inv.stock_actual,
                    referencia=f"ANULACION-{venta.numero_recibo}",
                    nota=f"Anulación de venta: {motivo}",
                    usuario_id=usuario_id
                )
                db.add(mov)

        if wallet_tx:
            WalletService.refund_sale(db, venta, wallet_tx)
        for reservation_id in {d.reserva_id for d in venta.detalles if d.reserva_id}:
            reservation = db.query(Reservation).filter_by(id=reservation_id).first()
            if reservation:
                reservation.estado = ReservationStatus.CANCELADA
                reservation.fecha_recogida = None
        venta.estado = SaleStatus.CANCELADA
        venta.motivo_cancelacion = motivo
        db.commit()
        db.refresh(venta)

        # Disparar notificación push interna si hay cliente asignado
        if venta.cliente_id:
            try:
                from app.services.notification_service import NotificationService
                NotificationService.create(
                    db=db,
                    user_id=venta.cliente_id,
                    title="¡Compra Presencial Exitosa!",
                    message=f"Tu compra #{venta.numero_recibo} por Bs {float(venta.monto_total):.2f} ha sido procesada con éxito en {venta.sucursal.nombre if venta.sucursal else 'tienda'}.",
                    type="VENTA",
                    reference_id=str(venta.id)
                )
            except Exception as notif_err:
                logger.warning(f"Aviso: no se pudo emitir notificación de venta presencial: {notif_err}")

        return SaleService._format_sale_response(venta)

    @staticmethod
    def get_summary_report(
        db: Session,
        sucursal_id: Optional[uuid.UUID] = None,
        tenant_id: Optional[uuid.UUID] = None
    ) -> dict:
        q = db.query(
            func.count(Sale.id).label("total_ventas"),
            func.coalesce(func.sum(Sale.monto_total), 0).label("ingresos_totales"),
            func.coalesce(func.sum(case((Sale.metodo_pago == PaymentMethod.EFECTIVO, Sale.monto_total), else_=0)), 0).label("ventas_efectivo"),
            func.coalesce(func.sum(case((Sale.metodo_pago == PaymentMethod.QR, Sale.monto_total), else_=0)), 0).label("ventas_qr"),
            func.coalesce(func.sum(case((Sale.metodo_pago == PaymentMethod.TARJETA, Sale.monto_total), else_=0)), 0).label("ventas_tarjeta"),
            func.coalesce(func.sum(case((Sale.metodo_pago == PaymentMethod.PAYPAL, Sale.monto_total), else_=0)), 0).label("ventas_paypal"),
            func.coalesce(func.sum(case((Sale.metodo_pago == PaymentMethod.BILLETERA, Sale.monto_total), else_=0)), 0).label("ventas_billetera"),
            func.coalesce(func.sum(Sale.impuesto_iva), 0).label("total_iva"),
            func.coalesce(func.sum(Sale.monto_neto), 0).label("ganancia_neta"),
        ).filter(Sale.estado == SaleStatus.COMPLETADA)

        if sucursal_id:
            q = q.filter(Sale.sucursal_id == sucursal_id)
        if tenant_id:
            q = q.filter(Sale.tenant_id == tenant_id)

        row = q.first()
        m = row._mapping if row else {}

        total_ventas = int(m.get("total_ventas") or 0)
        ingresos_totales = float(m.get("ingresos_totales") or 0.0)
        ticket_promedio = (ingresos_totales / total_ventas) if total_ventas > 0 else 0.0

        return {
            "total_ventas": total_ventas,
            "ingresos_totales": round(ingresos_totales, 2),
            "ticket_promedio": round(ticket_promedio, 2),
            "ventas_efectivo": round(float(m.get("ventas_efectivo") or 0.0), 2),
            "ventas_paypal": round(float(m.get("ventas_paypal") or 0.0), 2),
            "ventas_billetera": round(float(m.get("ventas_billetera") or 0.0), 2),
            "ventas_tarjeta": round(float(m.get("ventas_tarjeta") or 0.0), 2),
            "ventas_qr": round(float(m.get("ventas_qr") or 0.0), 2),
            "total_iva": round(float(m.get("total_iva") or 0), 2),
            "ganancia_neta": round(float(m.get("ganancia_neta") or 0), 2),
        }

    @staticmethod
    def get_top_products(
        db: Session,
        sucursal_id: Optional[uuid.UUID] = None,
        limit: int = 10,
        tenant_id: Optional[uuid.UUID] = None
    ) -> List[dict]:
        q = (
            db.query(
                SaleDetail.variante_id,
                func.sum(SaleDetail.cantidad).label("total_cantidad"),
                func.sum(SaleDetail.subtotal).label("total_ingresos")
            )
            .join(Sale, SaleDetail.venta_id == Sale.id)
            .filter(Sale.estado == SaleStatus.COMPLETADA)
        )
        if tenant_id:
            q = q.filter(Sale.tenant_id == tenant_id)
        if sucursal_id:
            q = q.filter(Sale.sucursal_id == sucursal_id)

        rows = (
            q.group_by(SaleDetail.variante_id)
            .order_by(desc("total_cantidad"))
            .limit(limit)
            .all()
        )

        results = []
        for row in rows:
            var = db.query(ProductVariant).filter(ProductVariant.id == row.variante_id).first()
            if var:
                prod = var.producto
                results.append({
                    "producto_nombre": prod.nombre,
                    "variante_sku": var.sku,
                    "talla": var.talla,
                    "color": var.color,
                    "unidades_vendidas": int(row.total_cantidad),
                    "ingresos_generados": float(row.total_ingresos),
                })
        return results
