import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.models.branch import Branch
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


class SaleService:

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
            "impuesto_iva": float(getattr(venta, "impuesto_iva", 0) or round(float(venta.monto_total) * 0.13, 2)),
            "monto_neto": float(getattr(venta, "monto_neto", 0) or round(float(venta.monto_total) * 0.87, 2)),
            "monto_recibido": float(venta.monto_recibido) if venta.monto_recibido is not None else None,
            "cambio": float(venta.cambio) if venta.cambio is not None else 0.0,
            "nota": venta.nota,
            "motivo_cancelacion": venta.motivo_cancelacion,
            "detalles": detalles_resp,
            "created_at": venta.created_at,
            "updated_at": venta.updated_at,
        }

    @staticmethod
    def create_presencial_sale(
        db: Session,
        data: PresencialSaleCreate,
        cajero_id: uuid.UUID
    ) -> dict:
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
        numero_recibo = f"VTA-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        # 3. Crear cabecera de venta
        venta = Sale(
            numero_recibo=numero_recibo,
            tipo=SaleType.PRESENCIAL,
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

        monto_total = 0.0

        # 4. Procesar cada ítem
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
                reserva = db.query(Reservation).filter(Reservation.id == item.reserva_id).first()
                if not reserva:
                    raise HTTPException(status_code=404, detail="Reserva asociada no encontrada")
                if reserva.sucursal_id != data.sucursal_id:
                    raise HTTPException(status_code=400, detail="La reserva no pertenece a esta sucursal")
                if reserva.estado not in [ReservationStatus.PREPARADA, ReservationStatus.PENDIENTE]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"La reserva {reserva.codigo} no está lista para ser cobrada (estado: {reserva.estado.value})"
                    )

                # Descontar de stock_reservado
                inv.stock_reservado = max(0, inv.stock_reservado - item.cantidad)
                reserva.estado = ReservationStatus.COMPLETADA
            else:
                # Caso B: Venta directa de mostrador -> validar stock disponible
                if inv.stock_disponible < item.cantidad:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Stock insuficiente para '{variant.producto.nombre}' ({variant.talla}/{variant.color}). Solicitado: {item.cantidad}, Disponible: {inv.stock_disponible}"
                    )

            # Descontar stock actual
            stock_antes = inv.stock_actual
            inv.stock_actual = max(0, inv.stock_actual - item.cantidad)

            # Registrar movimiento de salida por venta
            mov = InventoryMovement(
                inventario_id=inv.id,
                tipo=MovementType.SALIDA_VENTA,
                cantidad=-item.cantidad,
                stock_antes=stock_antes,
                stock_despues=inv.stock_actual,
                referencia=numero_recibo,
                nota=f"Venta presencial ({venta.metodo_pago.value})",
                usuario_id=cajero_id
            )
            db.add(mov)

            # Determinar precio
            precio_unit = item.precio_unitario
            if precio_unit is None:
                precio_unit = float(variant.producto.precio_base) + float(variant.precio_extra)

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

        # 5. Validar pago en efectivo, cambio y cálculo de impuestos (13% IVA y 87% Ganancia neta)
        iva = round(monto_total * 0.13, 2)
        neto = round(monto_total - iva, 2)
        venta.monto_total = monto_total
        venta.impuesto_iva = iva
        venta.monto_neto = neto

        if data.pago.metodo == PaymentMethod.EFECTIVO and data.pago.monto_recibido is not None:
            if data.pago.monto_recibido < monto_total:
                raise HTTPException(
                    status_code=400,
                    detail=f"El monto recibido ({data.pago.monto_recibido} Bs.) es menor al total ({monto_total} Bs.)"
                )
            venta.cambio = data.pago.monto_recibido - monto_total

        db.commit()
        db.refresh(venta)
        return SaleService._format_sale_response(venta)

    @staticmethod
    def create_digital_sale(
        db: Session,
        data: DigitalSaleCreate,
        cliente_id: uuid.UUID
    ) -> dict:
        branch = db.query(Branch).filter(Branch.id == data.sucursal_id, Branch.activa == True).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Sucursal no encontrada o inactiva")

        now = utc_now()
        numero_recibo = f"DIG-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        venta = Sale(
            numero_recibo=numero_recibo,
            tipo=SaleType.DIGITAL,
            estado=SaleStatus.COMPLETADA,
            sucursal_id=data.sucursal_id,
            cliente_id=cliente_id,
            cajero_id=None,
            metodo_pago=PaymentMethod.TARJETA,
            referencia_pago=data.token_pago or "PAY-DIGITAL-AUTH",
            monto_total=0.0,
            nota=data.nota
        )
        db.add(venta)
        db.flush()

        monto_total = 0.0
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
            inv.stock_actual = max(0, inv.stock_actual - item.cantidad)

            mov = InventoryMovement(
                inventario_id=inv.id,
                tipo=MovementType.SALIDA_VENTA,
                cantidad=-item.cantidad,
                stock_antes=stock_antes,
                stock_despues=inv.stock_actual,
                referencia=numero_recibo,
                nota="Venta digital / App Móvil",
                usuario_id=cliente_id
            )
            db.add(mov)

            precio_unit = float(variant.producto.precio_base) + float(variant.precio_extra)
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

        iva = round(monto_total * 0.13, 2)
        neto = round(monto_total - iva, 2)
        venta.monto_total = monto_total
        venta.impuesto_iva = iva
        venta.monto_neto = neto
        db.commit()
        db.refresh(venta)
        return SaleService._format_sale_response(venta)

    @staticmethod
    def get_sale_by_id(db: Session, venta_id: uuid.UUID) -> dict:
        venta = db.query(Sale).filter(Sale.id == venta_id).first()
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
        offset: int = 0
    ) -> List[dict]:
        q = db.query(Sale)
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
    def cancel_sale(
        db: Session,
        venta_id: uuid.UUID,
        motivo: str,
        usuario_id: uuid.UUID
    ) -> dict:
        venta = db.query(Sale).filter(Sale.id == venta_id).first()
        if not venta:
            raise HTTPException(status_code=404, detail="Venta no encontrada")

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

            if inv:
                stock_antes = inv.stock_actual
                inv.stock_actual += detalle.cantidad
                mov = InventoryMovement(
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

        venta.estado = SaleStatus.CANCELADA
        venta.motivo_cancelacion = motivo
        db.commit()
        db.refresh(venta)
        return SaleService._format_sale_response(venta)

    @staticmethod
    def get_summary_report(
        db: Session,
        sucursal_id: Optional[uuid.UUID] = None
    ) -> dict:
        q = db.query(Sale).filter(Sale.estado == SaleStatus.COMPLETADA)
        if sucursal_id:
            q = q.filter(Sale.sucursal_id == sucursal_id)

        ventas = q.all()
        total_ventas = len(ventas)
        ingresos_totales = sum(float(v.monto_total) for v in ventas)
        ticket_promedio = (ingresos_totales / total_ventas) if total_ventas > 0 else 0.0

        ventas_efectivo = sum(float(v.monto_total) for v in ventas if v.metodo_pago == PaymentMethod.EFECTIVO)
        ventas_tarjeta = sum(float(v.monto_total) for v in ventas if v.metodo_pago == PaymentMethod.TARJETA)
        ventas_qr = sum(float(v.monto_total) for v in ventas if v.metodo_pago == PaymentMethod.QR)

        total_iva = sum(float(getattr(v, "impuesto_iva", 0) or round(float(v.monto_total) * 0.13, 2)) for v in ventas)
        ganancia_neta = sum(float(getattr(v, "monto_neto", 0) or round(float(v.monto_total) - round(float(v.monto_total) * 0.13, 2), 2)) for v in ventas)

        return {
            "total_ventas": total_ventas,
            "ingresos_totales": round(ingresos_totales, 2),
            "ticket_promedio": round(ticket_promedio, 2),
            "ventas_efectivo": round(ventas_efectivo, 2),
            "ventas_tarjeta": round(ventas_tarjeta, 2),
            "ventas_qr": round(ventas_qr, 2),
            "total_iva": round(total_iva, 2),
            "ganancia_neta": round(ganancia_neta, 2),
        }

    @staticmethod
    def get_top_products(
        db: Session,
        sucursal_id: Optional[uuid.UUID] = None,
        limit: int = 10
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
