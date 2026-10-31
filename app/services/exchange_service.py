import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from collections import defaultdict
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc
from fastapi import HTTPException, status

from app.models.sale import Sale, SaleStatus
from app.models.sale_detail import SaleDetail
from app.models.product_variant import ProductVariant
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.product_exchange import ProductExchange, utc_now
from app.models.product_exchange_detail import ProductExchangeDetail
from app.models.user import User
from app.models.branch import Branch
from app.schemas.product_exchange import (
    ProductExchangeCreate,
    ProductExchangeResponse,
    ExchangeDetailResponse,
    VerifyReceiptResponse,
    VerifyReceiptItemResponse,
)

MAX_EXCHANGE_DAYS = 30


class ExchangeService:

    @staticmethod
    def _format_exchange_response(cambio: ProductExchange) -> dict:
        detalles_resp = []
        for d in cambio.detalles:
            v_dev = d.variante_devuelta
            p_dev = v_dev.producto if v_dev else None

            v_ent = d.variante_entregada
            p_ent = v_ent.producto if v_ent else None

            detalles_resp.append({
                "id": d.id,
                "variante_devuelta_id": d.variante_devuelta_id,
                "variante_devuelta_nombre": p_dev.nombre if p_dev else "Desconocido",
                "variante_devuelta_talla": v_dev.talla if v_dev else "-",
                "variante_devuelta_color": v_dev.color if v_dev else "-",
                "variante_devuelta_sku": v_dev.sku if v_dev else "-",
                "cantidad_devuelta": d.cantidad_devuelta,
                "precio_unitario_devuelto": float(d.precio_unitario_devuelto),
                "variante_entregada_id": d.variante_entregada_id,
                "variante_entregada_nombre": p_ent.nombre if p_ent else "Desconocido",
                "variante_entregada_talla": v_ent.talla if v_ent else "-",
                "variante_entregada_color": v_ent.color if v_ent else "-",
                "variante_entregada_sku": v_ent.sku if v_ent else "-",
                "cantidad_entregada": d.cantidad_entregada,
                "precio_unitario_entregado": float(d.precio_unitario_entregado),
                "subtotal_diferencia": float(d.subtotal_diferencia),
            })

        return {
            "id": cambio.id,
            "numero_cambio": cambio.numero_cambio,
            "venta_id": cambio.venta_id,
            "numero_recibo_original": cambio.venta.numero_recibo if cambio.venta else "N/A",
            "sucursal_id": cambio.sucursal_id,
            "sucursal_nombre": cambio.sucursal.nombre if cambio.sucursal else "Sucursal",
            "cajero_id": cambio.cajero_id,
            "cajero_nombre": cambio.cajero.full_name if cambio.cajero else "Cajero",
            "cliente_id": cambio.cliente_id,
            "cliente_nombre": cambio.cliente.full_name if cambio.cliente else "Cliente Casual",
            "fecha_cambio": cambio.fecha_cambio,
            "dias_transcurridos": cambio.dias_transcurridos,
            "monto_total_devuelto": float(cambio.monto_total_devuelto),
            "monto_total_entregado": float(cambio.monto_total_entregado),
            "diferencia": float(cambio.diferencia),
            "metodo_pago_diferencia": cambio.metodo_pago_diferencia or "NINGUNO",
            "motivo": cambio.motivo,
            "estado_prenda": cambio.estado_prenda,
            "nota": cambio.nota,
            "detalles": detalles_resp,
        }

    @staticmethod
    def verify_receipt(db: Session, numero_recibo: str, tenant_id: Optional[uuid.UUID] = None) -> VerifyReceiptResponse:
        recibo_limpio = numero_recibo.strip().upper()
        q = db.query(Sale).filter(Sale.numero_recibo.ilike(recibo_limpio))
        if tenant_id:
            q = q.filter(Sale.tenant_id == tenant_id)
        venta = q.first()

        if not venta:
            return VerifyReceiptResponse(
                valido=False,
                motivo_invalido=f"No se encontró ninguna venta registrada con el recibo '{numero_recibo}'",
                numero_recibo=numero_recibo,
                dentro_del_plazo=False,
                items=[]
            )

        if venta.estado != SaleStatus.COMPLETADA:
            return VerifyReceiptResponse(
                valido=False,
                motivo_invalido=f"La venta se encuentra en estado '{venta.estado.value}'. Solo se permiten cambios sobre ventas COMPLETADAS.",
                venta_id=venta.id,
                numero_recibo=venta.numero_recibo,
                fecha_venta=venta.created_at,
                sucursal_id=venta.sucursal_id,
                sucursal_nombre=venta.sucursal.nombre if venta.sucursal else None,
                cliente_nombre=venta.cliente.full_name if venta.cliente else "Cliente Casual",
                dentro_del_plazo=False,
                items=[]
            )

        # Calcular días transcurridos desde la emisión de la venta
        ahora = datetime.now(timezone.utc)
        fecha_creacion = venta.created_at
        if fecha_creacion.tzinfo is None:
            fecha_creacion = fecha_creacion.replace(tzinfo=timezone.utc)
        
        dias_transcurridos = (ahora - fecha_creacion).days
        dentro_plazo = dias_transcurridos <= MAX_EXCHANGE_DAYS

        if not dentro_plazo:
            return VerifyReceiptResponse(
                valido=False,
                motivo_invalido=f"El plazo máximo permitido para cambios es de {MAX_EXCHANGE_DAYS} días. Han transcurrido {dias_transcurridos} días desde la compra.",
                venta_id=venta.id,
                numero_recibo=venta.numero_recibo,
                fecha_venta=venta.created_at,
                dias_transcurridos=dias_transcurridos,
                dentro_del_plazo=False,
                sucursal_id=venta.sucursal_id,
                sucursal_nombre=venta.sucursal.nombre if venta.sucursal else None,
                cliente_nombre=venta.cliente.full_name if venta.cliente else "Cliente Casual",
                items=[]
            )

        # Calcular cantidades ya cambiadas previamente en esta venta
        cambios_previos = db.query(ProductExchange).filter(ProductExchange.venta_id == venta.id).all()
        cantidades_cambiadas = defaultdict(int)
        for cp in cambios_previos:
            for det in cp.detalles:
                cantidades_cambiadas[det.variante_devuelta_id] += det.cantidad_devuelta

        items_resp: List[VerifyReceiptItemResponse] = []
        hay_disponibles = False

        for det in venta.detalles:
            var = det.variante
            prod = var.producto if var else None
            ya_cambiada = cantidades_cambiadas[det.variante_id]
            disponible = max(0, det.cantidad - ya_cambiada)

            if disponible > 0:
                hay_disponibles = True

            items_resp.append(VerifyReceiptItemResponse(
                variante_id=det.variante_id,
                producto_nombre=prod.nombre if prod else "Producto",
                talla=var.talla if var else "-",
                color=var.color if var else "-",
                sku=var.sku if var else "-",
                cantidad_comprada=det.cantidad,
                cantidad_ya_cambiada=ya_cambiada,
                cantidad_disponible_cambio=disponible,
                precio_unitario=float(det.precio_unitario)
            ))

        if not hay_disponibles:
            return VerifyReceiptResponse(
                valido=False,
                motivo_invalido="Todos los artículos de este recibo ya han sido cambiados en su totalidad previamente.",
                venta_id=venta.id,
                numero_recibo=venta.numero_recibo,
                fecha_venta=venta.created_at,
                dias_transcurridos=dias_transcurridos,
                dentro_del_plazo=True,
                sucursal_id=venta.sucursal_id,
                sucursal_nombre=venta.sucursal.nombre if venta.sucursal else None,
                cliente_nombre=venta.cliente.full_name if venta.cliente else "Cliente Casual",
                items=items_resp
            )

        return VerifyReceiptResponse(
            valido=True,
            venta_id=venta.id,
            numero_recibo=venta.numero_recibo,
            fecha_venta=venta.created_at,
            dias_transcurridos=dias_transcurridos,
            dentro_del_plazo=True,
            sucursal_id=venta.sucursal_id,
            sucursal_nombre=venta.sucursal.nombre if venta.sucursal else None,
            cliente_nombre=venta.cliente.full_name if venta.cliente else "Cliente Casual",
            items=items_resp
        )

    @staticmethod
    def create_exchange(
        db: Session,
        data: ProductExchangeCreate,
        cajero_id: Optional[uuid.UUID] = None
    ) -> dict:
        # 1. Validar recibo de venta
        recibo_limpio = data.numero_recibo.strip().upper()
        venta = db.query(Sale).filter(Sale.numero_recibo.ilike(recibo_limpio)).first()

        if not venta:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Recibo de venta '{data.numero_recibo}' no encontrado."
            )

        if venta.estado != SaleStatus.COMPLETADA:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Solo se pueden registrar cambios sobre ventas COMPLETADAS (estado actual: {venta.estado.value})"
            )

        # 2. Validar ventana de tiempo (máximo 30 días)
        ahora = datetime.now(timezone.utc)
        fecha_creacion = venta.created_at
        if fecha_creacion.tzinfo is None:
            fecha_creacion = fecha_creacion.replace(tzinfo=timezone.utc)
        dias_transcurridos = (ahora - fecha_creacion).days

        if dias_transcurridos > MAX_EXCHANGE_DAYS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El plazo máximo legal de {MAX_EXCHANGE_DAYS} días para cambio de prendas ha expirado ({dias_transcurridos} días transcurridos desde la compra)."
            )

        # 3. Validar cantidades ya cambiadas previamente
        cambios_previos = db.query(ProductExchange).filter(ProductExchange.venta_id == venta.id).all()
        cantidades_cambiadas = defaultdict(int)
        for cp in cambios_previos:
            for det in cp.detalles:
                cantidades_cambiadas[det.variante_devuelta_id] += det.cantidad_devuelta

        # Mapear precios y cantidades de la venta original
        detalles_venta_map = {d.variante_id: d for d in venta.detalles}

        # 4. Procesar y validar cada par de prenda (devuelta vs entregada)
        items_a_procesar = []
        monto_total_devuelto = Decimal("0.0")
        monto_total_entregado = Decimal("0.0")

        # Agrupar cantidades devueltas en la solicitud actual para evitar trampas de duplicación en el payload
        cantidades_solicitadas_dev = defaultdict(int)
        for item in data.items:
            cantidades_solicitadas_dev[item.variante_devuelta_id] += item.cantidad_devuelta

        for var_id, cant_solic in cantidades_solicitadas_dev.items():
            if var_id not in detalles_venta_map:
                var_db = db.query(ProductVariant).filter(ProductVariant.id == var_id).first()
                sku = var_db.sku if var_db else str(var_id)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La prenda con SKU {sku} no pertenece al recibo de venta original."
                )

            det_orig = detalles_venta_map[var_id]
            ya_cambiada = cantidades_cambiadas[var_id]
            disponible = det_orig.cantidad - ya_cambiada

            if cant_solic > disponible:
                var_db = det_orig.variante
                sku = var_db.sku if var_db else str(var_id)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cantidad solicitada para cambio de {sku} ({cant_solic}) excede el remanente disponible ({disponible})."
                )

        # 5. Validar disponibilidad de stock de las prendas entregadas en la sucursal
        for item in data.items:
            det_orig = detalles_venta_map[item.variante_devuelta_id]
            precio_devuelto = Decimal(str(det_orig.precio_unitario))
            subtotal_devuelto = precio_devuelto * Decimal(str(item.cantidad_devuelta))
            monto_total_devuelto += subtotal_devuelto

            # Obtener datos de la variante entregada
            var_entregada = db.query(ProductVariant).filter(
                ProductVariant.id == item.variante_entregada_id,
                ProductVariant.activo == True
            ).first()

            if not var_entregada:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Variante a entregar con ID '{item.variante_entregada_id}' no encontrada o inactiva."
                )

            # Validar stock físico en la sucursal de la venta
            inv_entregada = db.query(Inventory).filter(
                Inventory.sucursal_id == venta.sucursal_id,
                Inventory.variante_id == item.variante_entregada_id
            ).first()

            stock_disponible = (inv_entregada.stock_actual - inv_entregada.stock_reservado) if inv_entregada else 0
            if stock_disponible < item.cantidad_entregada:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Stock insuficiente en la sucursal para entregar: "
                        f"{var_entregada.producto.nombre} ({var_entregada.talla}/{var_entregada.color}). "
                        f"Disponible: {stock_disponible}, Requerido: {item.cantidad_entregada}."
                    )
                )

            # Precio de la prenda entregada (precio base del producto + precio extra de variante)
            prod_entregado = var_entregada.producto
            precio_entregado = Decimal(str(prod_entregado.precio_base)) + Decimal(str(var_entregada.precio_extra))
            subtotal_entregado = precio_entregado * Decimal(str(item.cantidad_entregada))
            monto_total_entregado += subtotal_entregado

            subtotal_diferencia = subtotal_entregado - subtotal_devuelto

            items_a_procesar.append({
                "item": item,
                "precio_devuelto": precio_devuelto,
                "precio_entregado": precio_entregado,
                "subtotal_diferencia": subtotal_diferencia,
                "inv_entregada": inv_entregada,
            })

        diferencia_total = monto_total_entregado - monto_total_devuelto

        # 6. Generar número único de comprobante de cambio
        now = utc_now()
        numero_cambio = f"CAM-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        # 7. Crear cabecera del cambio
        cambio = ProductExchange(
            tenant_id=venta.tenant_id,
            numero_cambio=numero_cambio,
            venta_id=venta.id,
            sucursal_id=venta.sucursal_id,
            cajero_id=cajero_id,
            cliente_id=venta.cliente_id,
            fecha_cambio=now,
            dias_transcurridos=dias_transcurridos,
            monto_total_devuelto=monto_total_devuelto,
            monto_total_entregado=monto_total_entregado,
            diferencia=diferencia_total,
            metodo_pago_diferencia=data.metodo_pago_diferencia if diferencia_total > 0 else "NINGUNO",
            motivo=data.motivo,
            estado_prenda=data.estado_prenda,
            nota=data.nota,
        )
        db.add(cambio)
        db.flush()

        # 8. Procesar movimientos de inventario y detalles
        for proc in items_a_procesar:
            item = proc["item"]

            # A. Reingreso de la prenda devuelta al stock
            inv_devuelta = db.query(Inventory).filter(
                Inventory.sucursal_id == venta.sucursal_id,
                Inventory.variante_id == item.variante_devuelta_id
            ).first()

            if not inv_devuelta:
                inv_devuelta = Inventory(
                    tenant_id=venta.tenant_id,
                    sucursal_id=venta.sucursal_id,
                    variante_id=item.variante_devuelta_id,
                    stock_actual=0,
                    stock_reservado=0,
                    stock_minimo=5
                )
                db.add(inv_devuelta)
                db.flush()

            stock_antes_dev = inv_devuelta.stock_actual
            inv_devuelta.stock_actual += item.cantidad_devuelta

            mov_dev = InventoryMovement(
                tenant_id=venta.tenant_id,
                inventario_id=inv_devuelta.id,
                tipo=MovementType.DEVOLUCION,
                cantidad=item.cantidad_devuelta,
                stock_antes=stock_antes_dev,
                stock_despues=inv_devuelta.stock_actual,
                referencia=numero_cambio,
                nota=f"Reingreso por cambio de prenda: {data.motivo}",
                usuario_id=cajero_id
            )
            db.add(mov_dev)

            # B. Salida de la prenda entregada del stock
            inv_ent = proc["inv_entregada"]
            stock_antes_ent = inv_ent.stock_actual
            inv_ent.stock_actual -= item.cantidad_entregada

            mov_ent = InventoryMovement(
                tenant_id=venta.tenant_id,
                inventario_id=inv_ent.id,
                tipo=MovementType.SALIDA_VENTA,
                cantidad=item.cantidad_entregada,
                stock_antes=stock_antes_ent,
                stock_despues=inv_ent.stock_actual,
                referencia=numero_cambio,
                nota=f"Salida por entrega de prenda en cambio: {data.motivo}",
                usuario_id=cajero_id
            )
            db.add(mov_ent)

            # C. Crear detalle de cambio
            detalle = ProductExchangeDetail(
                cambio_id=cambio.id,
                variante_devuelta_id=item.variante_devuelta_id,
                cantidad_devuelta=item.cantidad_devuelta,
                precio_unitario_devuelto=proc["precio_devuelto"],
                variante_entregada_id=item.variante_entregada_id,
                cantidad_entregada=item.cantidad_entregada,
                precio_unitario_entregado=proc["precio_entregado"],
                subtotal_diferencia=proc["subtotal_diferencia"]
            )
            db.add(detalle)

        db.commit()
        db.refresh(cambio)

        # Disparar notificación push interna al cliente
        if cambio.cliente_id:
            try:
                from app.services.notification_service import NotificationService
                NotificationService.create(
                    db=db,
                    user_id=cambio.cliente_id,
                    title="¡Nota de Cambio Procesada!",
                    message=f"Tu cambio de prenda #{cambio.numero_cambio} correspondiente al recibo {venta.numero_recibo} ha sido registrado exitosamente.",
                    type="CAMBIO_PRENDA",
                    reference_id=str(cambio.id)
                )
            except Exception as notif_err:
                logger.warning(f"Aviso: no se pudo emitir notificación de cambio: {notif_err}")

        return ExchangeService._format_exchange_response(cambio)

    @staticmethod
    def list_exchanges(
        db: Session,
        sucursal_id: Optional[uuid.UUID] = None,
        fecha_inicio: Optional[date] = None,
        fecha_fin: Optional[date] = None,
        tenant_id: Optional[uuid.UUID] = None
    ) -> List[dict]:
        query = db.query(ProductExchange)

        if tenant_id:
            query = query.filter(ProductExchange.tenant_id == tenant_id)

        if sucursal_id:
            query = query.filter(ProductExchange.sucursal_id == sucursal_id)

        if fecha_inicio:
            query = query.filter(ProductExchange.fecha_cambio >= datetime.combine(fecha_inicio, datetime.min.time(), tzinfo=timezone.utc))

        if fecha_fin:
            query = query.filter(ProductExchange.fecha_cambio <= datetime.combine(fecha_fin, datetime.max.time(), tzinfo=timezone.utc))

        cambios = query.order_by(desc(ProductExchange.created_at)).all()
        return [ExchangeService._format_exchange_response(c) for c in cambios]

    @staticmethod
    def get_exchange_by_id(db: Session, cambio_id: uuid.UUID, tenant_id: Optional[uuid.UUID] = None) -> dict:
        q = db.query(ProductExchange).filter(ProductExchange.id == cambio_id)
        if tenant_id:
            q = q.filter(ProductExchange.tenant_id == tenant_id)
        cambio = q.first()
        if not cambio:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comprobante de cambio no encontrado.")
        return ExchangeService._format_exchange_response(cambio)
