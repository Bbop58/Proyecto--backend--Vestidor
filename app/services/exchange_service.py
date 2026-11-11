import uuid
import hashlib
import json
import copy
import logging
from types import SimpleNamespace
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select,func
from app.models.sync_operation import SyncOperation
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
from collections import defaultdict, Counter
from app.core.commercial_transaction import commercial_transaction, lock_branch
from app.core.money import catalog_price, money
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

logger=logging.getLogger(__name__)
MAX_EXCHANGE_DAYS = 30


class ExchangeService:

    @staticmethod
    def _paypal_refund_claims(payment):
        payload = payment.payload if payment and isinstance(payment.payload, dict) else {}
        claims = payload.get('exchange_refunds', {})
        return claims if isinstance(claims, dict) else {}

    @classmethod
    def _paypal_refund_reserved(cls, payment, *, exclude_operation_id=None):
        reserved = Counter()
        excluded = str(exclude_operation_id) if exclude_operation_id else None
        for key, claim in cls._paypal_refund_claims(payment).items():
            if key == excluded or not isinstance(claim, dict) or claim.get('state') not in ('PENDING', 'COMPLETED', 'REVIEW') or claim.get('exchange_id'):
                continue
            request = claim.get('request') if isinstance(claim.get('request'), dict) else {}
            for item in request.get('items', []):
                try:
                    reserved[uuid.UUID(str(item['variante_devuelta_id']))] += int(item['cantidad_devuelta'])
                except (KeyError, TypeError, ValueError):
                    # A malformed durable claim must not release the units.
                    continue
        return reserved

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
                "variante_entregada_nombre": p_ent.nombre if p_ent else None,
                "variante_entregada_talla": v_ent.talla if v_ent else None,
                "variante_entregada_color": v_ent.color if v_ent else None,
                "variante_entregada_sku": v_ent.sku if v_ent else None,
                "cantidad_entregada": d.cantidad_entregada,
                "precio_unitario_entregado": float(d.precio_unitario_entregado or 0),
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
            "tipo_operacion": cambio.tipo_operacion or "CAMBIO",
            "estado_liquidacion": cambio.estado_liquidacion or "CONFIRMADO",
            "referencia_reembolso": cambio.referencia_reembolso,
            "motivo": cambio.motivo,
            "estado_prenda": cambio.estado_prenda,
            "nota": cambio.nota,
            "detalles": detalles_resp,
        }

    @staticmethod
    def verify_receipt(db: Session, numero_recibo: str, tenant_id: Optional[uuid.UUID] = None) -> VerifyReceiptResponse:
        recibo_limpio = numero_recibo.strip().upper()
        q = db.query(Sale).filter(func.upper(Sale.numero_recibo)==recibo_limpio)
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
                motivo_invalido=f"La venta se encuentra en estado '{venta.estado.value}'. Solo se permiten cambios o devoluciones de ventas COMPLETADAS.",
                venta_id=venta.id,
                numero_recibo=venta.numero_recibo,
                fecha_venta=venta.created_at,
                sucursal_id=venta.sucursal_id,
                sucursal_nombre=venta.sucursal.nombre if venta.sucursal else None,
                cliente_nombre=venta.cliente.full_name if venta.cliente else "Cliente Casual",
                cliente_id=venta.cliente_id,
                metodo_pago_original=getattr(venta.metodo_pago, 'value', venta.metodo_pago),
                dentro_del_plazo=False,
                items=[]
            )

        # Calcular días transcurridos desde la emisión de la venta
        ahora = datetime.now(timezone.utc)
        fecha_creacion = venta.created_at
        if fecha_creacion.tzinfo is None:
            fecha_creacion = fecha_creacion.replace(tzinfo=timezone.utc)
        
        dias_transcurridos = (ahora - fecha_creacion).days
        dentro_plazo = ahora <= fecha_creacion + timedelta(days=MAX_EXCHANGE_DAYS)

        if not dentro_plazo:
            return VerifyReceiptResponse(
                valido=False,
                motivo_invalido=f"El plazo máximo para cambios o devoluciones es de {MAX_EXCHANGE_DAYS} días. Han transcurrido {dias_transcurridos} días desde la compra.",
                venta_id=venta.id,
                numero_recibo=venta.numero_recibo,
                fecha_venta=venta.created_at,
                dias_transcurridos=dias_transcurridos,
                dentro_del_plazo=False,
                sucursal_id=venta.sucursal_id,
                sucursal_nombre=venta.sucursal.nombre if venta.sucursal else None,
                cliente_nombre=venta.cliente.full_name if venta.cliente else "Cliente Casual",
                cliente_id=venta.cliente_id,
                metodo_pago_original=getattr(venta.metodo_pago, 'value', venta.metodo_pago),
                items=[]
            )

        # Calcular cantidades ya cambiadas previamente en esta venta
        cambios_previos = db.query(ProductExchange).filter(ProductExchange.venta_id == venta.id).all()
        cantidades_cambiadas = defaultdict(int)
        for cp in cambios_previos:
            for det in cp.detalles:
                cantidades_cambiadas[det.variante_devuelta_id] += det.cantidad_devuelta
        from app.models.payment_intent import PaymentIntent
        original_payment = db.query(PaymentIntent).filter_by(sale_id=venta.id, tenant_id=venta.tenant_id).first()
        pending_paypal = ExchangeService._paypal_refund_reserved(original_payment)

        items_resp: List[VerifyReceiptItemResponse] = []
        hay_disponibles = False

        sold = Counter()
        for detail in venta.detalles:
            sold[detail.variante_id] += detail.cantidad
        for det in {d.variante_id: d for d in venta.detalles}.values():
            var = det.variante
            prod = var.producto if var else None
            ya_cambiada = cantidades_cambiadas[det.variante_id]
            pendiente = pending_paypal[det.variante_id]
            disponible = max(0, sold[det.variante_id] - ya_cambiada - pendiente)

            if disponible > 0:
                hay_disponibles = True

            items_resp.append(VerifyReceiptItemResponse(
                variante_id=det.variante_id,
                producto_nombre=prod.nombre if prod else "Producto",
                talla=var.talla if var else "-",
                color=var.color if var else "-",
                sku=var.sku if var else "-",
                cantidad_comprada=sold[det.variante_id],
                cantidad_ya_cambiada=ya_cambiada,
                cantidad_pendiente_devolucion=pendiente,
                cantidad_disponible_cambio=disponible,
                precio_unitario=float(det.precio_unitario)
            ))

        if not hay_disponibles:
            return VerifyReceiptResponse(
                valido=False,
                motivo_invalido="Todas las unidades de este recibo ya fueron cambiadas o devueltas.",
                venta_id=venta.id,
                numero_recibo=venta.numero_recibo,
                fecha_venta=venta.created_at,
                dias_transcurridos=dias_transcurridos,
                dentro_del_plazo=True,
                sucursal_id=venta.sucursal_id,
                sucursal_nombre=venta.sucursal.nombre if venta.sucursal else None,
                cliente_nombre=venta.cliente.full_name if venta.cliente else "Cliente Casual",
                cliente_id=venta.cliente_id,
            metodo_pago_original=getattr(venta.metodo_pago, 'value', venta.metodo_pago),
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
            cliente_id=venta.cliente_id,
            metodo_pago_original=getattr(venta.metodo_pago, 'value', venta.metodo_pago),
            items=items_resp
        )

    @staticmethod
    def _fingerprint(data):
        return hashlib.sha256(json.dumps(data.model_dump(mode='json',exclude={'client_operation_id','quote_hash'}),sort_keys=True).encode()).hexdigest()

    @staticmethod
    def quote_exchange(db,data,cajero_id):
        prepared=ExchangeService._prepare_exchange(db,data,cajero_id,validate_payment=False)
        result={key:format(prepared[key],'.2f') for key in ('monto_total_devuelto','monto_total_entregado','diferencia_total')}
        result['diferencia']=result.pop('diferencia_total')
        result['quote_hash']=ExchangeService._quote_hash(prepared)
        return result

    @staticmethod
    def _quote_hash(prepared):
        value=[str(prepared['venta'].id),prepared['tipo_operacion'],str(prepared['monto_total_devuelto']),str(prepared['monto_total_entregado']),
          [[str(p['item'].variante_devuelta_id),p['item'].cantidad_devuelta,str(p['item'].variante_entregada_id or ''),p['item'].cantidad_entregada,
            str(p['precio_devuelto']),str(p['precio_entregado'])] for p in prepared['items_a_procesar']]]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()

    @staticmethod
    def _prepare_exchange(db,data,cajero_id,validate_payment=True,paypal_payment_intent_id=None,paypal_refund_operation_id=None):
        # 1. Validar recibo de venta
        recibo_limpio = data.numero_recibo.strip().upper()
        actor=db.get(User,cajero_id) if cajero_id else None
        if not actor or not actor.is_active or not actor.tenant_id or actor.is_platform_admin:
            raise HTTPException(403,'Se requiere una identidad comercial')
        venta = db.query(Sale).filter(func.upper(Sale.numero_recibo)==recibo_limpio,Sale.tenant_id==actor.tenant_id).first()

        if not venta:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Recibo de venta '{data.numero_recibo}' no encontrado."
            )

        if venta.cliente_id:
            from app.services.wallet_service import WalletService
            WalletService._lock_user(db, venta.cliente_id, venta.tenant_id)
        lock_branch(db, venta.sucursal_id)
        db.refresh(venta)
        from app.models.payment_intent import PaymentIntent
        payment = db.query(PaymentIntent).filter_by(sale_id=venta.id, tenant_id=venta.tenant_id).first()
        if payment and payment.state != 'APPLIED':
            raise HTTPException(409, "La venta tiene un reembolso en conciliación")
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

        if ahora > fecha_creacion + timedelta(days=MAX_EXCHANGE_DAYS):
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
        source_payment = db.query(PaymentIntent).filter_by(sale_id=venta.id, tenant_id=venta.tenant_id).first()
        pending_paypal = ExchangeService._paypal_refund_reserved(source_payment, exclude_operation_id=data.client_operation_id)

        # Mapear precios y cantidades de la venta original
        detalles_venta_map = {d.variante_id: d for d in venta.detalles}
        quantities_sold = Counter()
        for detail in venta.detalles:
            quantities_sold[detail.variante_id] += detail.cantidad
        tipo_operacion=data.tipo_operacion
        for item in data.items:
            if tipo_operacion == 'CAMBIO' and (not item.variante_entregada_id or item.cantidad_entregada < 1):
                raise HTTPException(422, 'Selecciona la prenda y cantidad que se entregará en el cambio')
            if tipo_operacion == 'DEVOLUCION' and (item.variante_entregada_id or item.cantidad_entregada):
                raise HTTPException(422, 'Una devolución no debe incluir una prenda de reemplazo')

        # 4. Procesar las prendas devueltas y, solo en un cambio, sus reemplazos.
        items_a_procesar = []
        monto_total_devuelto = Decimal("0.0")
        monto_total_entregado = Decimal("0.0")

        # Agrupar cantidades devueltas en la solicitud actual para evitar trampas de duplicación en el payload
        cantidades_solicitadas_dev = defaultdict(int)
        allocated = Counter(cantidades_cambiadas)
        allocated.update(pending_paypal)
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
            disponible = quantities_sold[var_id] - ya_cambiada - pending_paypal[var_id]

            if cant_solic > disponible:
                var_db = det_orig.variante
                sku = var_db.sku if var_db else str(var_id)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cantidad solicitada para cambio de {sku} ({cant_solic}) excede el remanente disponible ({disponible})."
                )

        outgoing = Counter()
        if tipo_operacion == 'CAMBIO':
            for item in data.items:
                outgoing[item.variante_entregada_id] += item.cantidad_entregada

        # 5. Validar disponibilidad de stock de las prendas entregadas en la sucursal
        for item in data.items:
            det_orig = detalles_venta_map[item.variante_devuelta_id]
            remaining = item.cantidad_devuelta
            skip = allocated[item.variante_devuelta_id]
            subtotal_devuelto = Decimal('0.00')
            originals = sorted((d for d in venta.detalles if d.variante_id == item.variante_devuelta_id),
                               key=lambda d: (d.created_at, str(d.id)))
            nominal_total = sum(d.subtotal for d in venta.detalles)
            discount_factor = ((nominal_total-Decimal(str(venta.descuento or 0)))/nominal_total
                               if nominal_total else Decimal('0'))
            prefix_by_id = {}
            prefix = Decimal('0')
            for line in sorted(venta.detalles, key=lambda d: (d.created_at, str(d.id))):
                prefix_by_id[line.id] = prefix
                prefix += line.subtotal
            for original in originals:
                skipped = min(skip, original.cantidad); skip -= skipped
                take = min(remaining, original.cantidad-skipped)
                start = prefix_by_id[original.id] + original.precio_unitario*skipped
                subtotal_devuelto += money((start+original.precio_unitario*take)*discount_factor)-money(start*discount_factor)
                remaining -= take
            if remaining:
                raise HTTPException(409, "No quedan unidades elegibles para cambio")
            allocated[item.variante_devuelta_id] += item.cantidad_devuelta
            # Reject a fraction of a cent that cannot fit the current per-unit schema.
            precio_devuelto = subtotal_devuelto/Decimal(item.cantidad_devuelta)
            if precio_devuelto != precio_devuelto.quantize(Decimal('.01')):
                raise HTTPException(409, "Divide el cambio por precio original para conservar los centavos")
            monto_total_devuelto += subtotal_devuelto

            inv_entregada=None
            precio_entregado=Decimal('0.00')
            if tipo_operacion == 'CAMBIO':
                var_entregada = db.query(ProductVariant).filter(
                    ProductVariant.id == item.variante_entregada_id,
                    ProductVariant.activo == True
                ).first()
                if not var_entregada:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Variante a entregar con ID '{item.variante_entregada_id}' no encontrada o inactiva.")

                # Reservas existentes no pueden consumirse como reemplazo.
                inv_entregada = db.query(Inventory).filter(
                    Inventory.sucursal_id == venta.sucursal_id,
                    Inventory.variante_id == item.variante_entregada_id
                ).first()
                stock_disponible = inv_entregada.stock_disponible if inv_entregada else 0
                if not inv_entregada or not inv_entregada.activo or stock_disponible < outgoing[item.variante_entregada_id]:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(f"Stock insuficiente en la sucursal para entregar: "
                            f"{var_entregada.producto.nombre} ({var_entregada.talla}/{var_entregada.color}). "
                            f"Disponible: {stock_disponible}, Requerido: {outgoing[item.variante_entregada_id]}."))
                precio_entregado = catalog_price(var_entregada)
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
        if validate_payment and diferencia_total > 0 and data.metodo_pago_diferencia not in ('EFECTIVO','PAYPAL'):
            raise HTTPException(400, "Para cobrar una diferencia selecciona efectivo o PayPal")
        if validate_payment and diferencia_total < 0 and data.metodo_pago_diferencia not in ('EFECTIVO','BILLETERA','PAYPAL'):
            raise HTTPException(400, "Para devolver una diferencia selecciona efectivo, billetera o PayPal")
        if diferencia_total < 0 and data.metodo_pago_diferencia == 'BILLETERA' and not venta.cliente_id:
            raise HTTPException(409, "Identifica al cliente para acreditar la diferencia a su billetera")

        exchange_payment = None
        paypal_refund_id = None
        if validate_payment and data.metodo_pago_diferencia == 'PAYPAL':
            from app.models.payment_intent import PaymentIntent
            if diferencia_total > 0:
                payment=db.query(PaymentIntent).filter_by(id=paypal_payment_intent_id,tenant_id=venta.tenant_id,
                    user_id=cajero_id,purpose='EXCHANGE',state='CAPTURED').first() if paypal_payment_intent_id else None
                if not payment or not payment.capture_id or money(payment.amount_bob) != money(diferencia_total):
                    raise HTTPException(409, "El cobro PayPal debe aprobarse por el importe cotizado antes de confirmar el cambio")
                exchange_payment = payment
            elif diferencia_total < 0:
                original_payment=source_payment
                if (getattr(venta.metodo_pago,'value',venta.metodo_pago) != 'PAYPAL'
                        or not original_payment or not original_payment.capture_id or original_payment.state != 'APPLIED'):
                    raise HTTPException(409, "PayPal solo puede reembolsar a la transacción PayPal original")
                claim = ExchangeService._paypal_refund_claims(original_payment).get(str(paypal_refund_operation_id)) if paypal_refund_operation_id else None
                if not claim or claim.get('state') != 'COMPLETED':
                    refund_state=claim.get('state','SIN_SOLICITUD') if isinstance(claim,dict) else 'SIN_SOLICITUD'
                    raise HTTPException(409, f"PayPal no confirmó el reembolso (estado: {refund_state}); no se registró la devolución. Consulta la misma operación antes de reintentar.")
                if money(claim.get('amount_bob', 0)) != money(-diferencia_total):
                    raise HTTPException(409, "El importe de la devolución cambió tras la confirmación PayPal; requiere conciliación")
                paypal_refund_id = claim.get('refund_id')
            else:
                raise HTTPException(400, "Un cambio exacto no necesita liquidación PayPal")

        return dict(venta=venta,tipo_operacion=tipo_operacion,dias_transcurridos=dias_transcurridos,items_a_procesar=items_a_procesar,
            monto_total_devuelto=monto_total_devuelto,monto_total_entregado=monto_total_entregado,diferencia_total=diferencia_total,
            source_payment=source_payment,exchange_payment=exchange_payment,paypal_refund_id=paypal_refund_id)

    @staticmethod
    @commercial_transaction
    def create_exchange(db:Session,data:ProductExchangeCreate,cajero_id=None,paypal_payment_intent_id=None,paypal_refund_operation_id=None):
        from app.services.sync_service import SyncService
        actor=db.get(User,cajero_id) if cajero_id else None
        if not actor or not actor.is_active or not actor.tenant_id or actor.is_platform_admin:
            raise HTTPException(403,'Se requiere una identidad comercial')
        SyncService.lock_keys(db,SimpleNamespace(client_operation_id=data.client_operation_id,supersedes_operation_id=None))
        digest=ExchangeService._fingerprint(data)
        op_type='DEVOLUCION_PRENDA' if data.tipo_operacion=='DEVOLUCION' else 'CAMBIO_PRENDA'
        old=db.execute(select(SyncOperation.__table__).where(SyncOperation.__table__.c.client_operation_id==data.client_operation_id)).mappings().first()
        if old:
            if old['tenant_id']!=actor.tenant_id or old['user_id']!=actor.id or old['operation_type']!=op_type or old['request_fingerprint']!=digest:
                raise HTTPException(409,'El identificador ya pertenece a otra operación')
            result=ExchangeService.get_exchange_by_id(db,old['entity_id'],actor.tenant_id)
            db.commit()
            return result
        prepared=ExchangeService._prepare_exchange(db,data,cajero_id,paypal_payment_intent_id=paypal_payment_intent_id,
            paypal_refund_operation_id=paypal_refund_operation_id)
        if data.quote_hash and data.quote_hash!=ExchangeService._quote_hash(prepared):
            raise HTTPException(409,'La cotización cambió. Revisa los importes antes de confirmar nuevamente.')
        venta=prepared['venta'];dias_transcurridos=prepared['dias_transcurridos'];items_a_procesar=prepared['items_a_procesar']
        monto_total_devuelto=prepared['monto_total_devuelto'];monto_total_entregado=prepared['monto_total_entregado'];diferencia_total=prepared['diferencia_total']
        tipo_operacion=prepared['tipo_operacion']
        source_payment=prepared['source_payment'];exchange_payment=prepared['exchange_payment']
        from app.services.wallet_service import WalletService
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
            metodo_pago_diferencia=data.metodo_pago_diferencia if diferencia_total != 0 else "NINGUNO",
            tipo_operacion=tipo_operacion,
            estado_liquidacion='CONFIRMADO',
            referencia_reembolso=prepared['paypal_refund_id'],
            motivo=data.motivo,
            estado_prenda=data.estado_prenda,
            nota=data.nota,
        )
        db.add(cambio)
        db.flush()

        if diferencia_total < 0 and data.metodo_pago_diferencia == 'BILLETERA':
            WalletService.credit_exchange(db, venta.cliente_id, venta.tenant_id, -diferencia_total, cambio.id)
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
                nota=f"Reingreso por {'devolución' if tipo_operacion == 'DEVOLUCION' else 'cambio'} de prenda: {data.motivo}",
                usuario_id=cajero_id
            )
            db.add(mov_dev)

            if data.estado_prenda=='DEFECTO_FABRICA':
                # Physical return is traced, then removed from sellable stock atomically.
                before_merma=inv_devuelta.stock_actual
                inv_devuelta.stock_actual-=item.cantidad_devuelta
                db.add(InventoryMovement(tenant_id=venta.tenant_id,inventario_id=inv_devuelta.id,
                    tipo=MovementType.AJUSTE,cantidad=-item.cantidad_devuelta,
                    stock_antes=before_merma,stock_despues=inv_devuelta.stock_actual,
                    referencia=numero_cambio,nota='Merma por defecto de fábrica; prenda no vendible',usuario_id=cajero_id))

            if tipo_operacion == 'DEVOLUCION':
                db.add(ProductExchangeDetail(
                    cambio_id=cambio.id,
                    variante_devuelta_id=item.variante_devuelta_id,
                    cantidad_devuelta=item.cantidad_devuelta,
                    precio_unitario_devuelto=proc['precio_devuelto'],
                    variante_entregada_id=None,
                    cantidad_entregada=0,
                    precio_unitario_entregado=Decimal('0.00'),
                    subtotal_diferencia=-money(proc['precio_devuelto'] * item.cantidad_devuelta)))
                continue

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

        db.add(SyncOperation(client_operation_id=data.client_operation_id,tenant_id=venta.tenant_id,
            branch_id=venta.sucursal_id,user_id=cajero_id,request_fingerprint=digest,operation_type=op_type,
            client_created_at=now,sync_status='CONFIRMADA',entity_id=cambio.id,response_payload={'receipt_number':numero_cambio}))
        if exchange_payment:
            exchange_payment.state='APPLIED'
            exchange_payment.payload={**(exchange_payment.payload or {}),'exchange_id':str(cambio.id)}
        if prepared['paypal_refund_id'] and source_payment:
            claims=dict(ExchangeService._paypal_refund_claims(source_payment))
            claim=dict(claims[str(paypal_refund_operation_id)])
            claim.update(state='APPLIED',exchange_id=str(cambio.id))
            claims[str(paypal_refund_operation_id)]=claim
            source_payment.payload={**(source_payment.payload or {}),'exchange_refunds':claims}
        db.commit()
        db.refresh(cambio)

        # Disparar notificación push interna al cliente
        if cambio.cliente_id:
            try:
                from app.services.notification_service import NotificationService
                NotificationService.create(
                    db=db,
                    user_id=cambio.cliente_id,
                    title="¡Operación de prendas procesada!",
                    message=f"Tu {'devolución' if tipo_operacion == 'DEVOLUCION' else 'cambio'} #{cambio.numero_cambio} del recibo {venta.numero_recibo} fue registrada.",
                    type="DEVOLUCION_PRENDA" if tipo_operacion == 'DEVOLUCION' else "CAMBIO_PRENDA",
                    reference_id=str(cambio.id)
                )
            except Exception as notif_err:
                db.rollback()
                logger.warning("Notificación de cambio no enviada: %s",type(notif_err).__name__)

        return ExchangeService._format_exchange_response(cambio)

    @staticmethod
    def create_paypal_exchange_order(db:Session,data:ProductExchangeCreate,cajero_id):
        from app.config import settings
        from app.models.payment_intent import PaymentIntent
        from app.services.payment_flow_service import PaymentFlowService,request_key
        from app.services.paypal_service import PayPalService
        prepared=ExchangeService._prepare_exchange(db,data,cajero_id,validate_payment=False)
        if data.metodo_pago_diferencia!='PAYPAL' or data.tipo_operacion!='CAMBIO' or prepared['diferencia_total']<=0:
            raise HTTPException(400,'PayPal solo crea una orden para cobrar una diferencia de cambio')
        amount=money(prepared['diferencia_total'],positive=True)
        if not data.quote_hash or data.quote_hash!=ExchangeService._quote_hash(prepared):
            raise HTTPException(409,'Actualiza la cotización antes de crear la orden PayPal')
        actor=db.get(User,cajero_id)
        creds=PaymentFlowService.credentials(db,actor)
        credential_id=creds.pop('_credential_id',None)
        key=data.client_operation_id
        digest=ExchangeService._fingerprint(data)
        advisory=int.from_bytes(hashlib.sha256(key.bytes).digest()[:8],'big',signed=True)
        db.execute(select(func.pg_advisory_xact_lock(advisory)))
        unresolved=(db.query(PaymentIntent).filter(PaymentIntent.tenant_id==actor.tenant_id,
            PaymentIntent.user_id==actor.id,PaymentIntent.purpose=='EXCHANGE',
            PaymentIntent.state.in_(['CREATING','CREATED','CAPTURING','CAPTURED','REFUND_PENDING','REVIEW']))
            .order_by(PaymentIntent.created_at).with_for_update().first())
        if unresolved and unresolved.id!=key:
            raise HTTPException(409,'Hay un pago PayPal de cambio pendiente. Reintenta esa misma operación antes de iniciar otra.')
        intent=db.query(PaymentIntent).filter_by(id=key).with_for_update().first()
        if intent:
            if (intent.tenant_id!=actor.tenant_id or intent.user_id!=actor.id or intent.purpose!='EXCHANGE'
                    or intent.payload.get('request_fingerprint')!=digest or intent.payload.get('quote_hash')!=data.quote_hash
                    or intent.merchant_key!=PaymentFlowService.merchant(creds)):
                raise HTTPException(409,'La clave de esta operación PayPal ya pertenece a otra solicitud')
            if intent.state in ('APPLIED','CAPTURED','CAPTURING','REFUND_PENDING','REVIEW','REFUNDED'):
                if intent.state in ('APPLIED','CAPTURED','CAPTURING','REFUND_PENDING') and intent.provider_order_id:
                    return dict(order_id=intent.provider_order_id,status=intent.state,monto_bob=float(intent.amount_bob),
                        monto_usd=float(intent.amount_usd),exchange_rate=float(intent.exchange_rate),
                        approve_url=intent.payload.get('approve_url') if intent.state=='CREATED' else None)
                raise HTTPException(409,'Esta orden requiere conciliación; no crees una operación duplicada')
        else:
            rate=Decimal(str(settings.PAYPAL_EXCHANGE_RATE))
            amount_usd=money(PayPalService.convert_bob_to_usd(amount,rate=rate),positive=True)
            intent=PaymentIntent(id=key,tenant_id=actor.tenant_id,user_id=actor.id,cliente_id=prepared['venta'].cliente_id,
                sucursal_id=prepared['venta'].sucursal_id,purpose='EXCHANGE',state='CREATING',amount_bob=amount,
                amount_usd=amount_usd,exchange_rate=rate,merchant_key=PaymentFlowService.merchant(creds),
                provider_mode=creds['mode'],payload={'request':data.model_dump(mode='json'),'request_fingerprint':digest,
                    'quote_hash':data.quote_hash,'paypal_credential_id':credential_id})
            db.add(intent);db.commit();intent=db.query(PaymentIntent).filter_by(id=key).with_for_update().one()
        if not intent.provider_order_id:
            order=PayPalService.create_order(monto_bob=float(intent.amount_bob),sucursal_id=str(intent.sucursal_id),
                descripcion='Diferencia por cambio de prenda FashionStore',request_id=request_key(intent,'create'),
                custom_id=str(intent.id),amount_usd=intent.amount_usd,**creds)
            intent.provider_order_id=order['order_id']
            intent.payload={**intent.payload,'approve_url':order.get('approve_url')}
            intent.state='CREATED';db.commit()
        return dict(order_id=intent.provider_order_id,status=intent.state,monto_bob=float(intent.amount_bob),
            monto_usd=float(intent.amount_usd),exchange_rate=float(intent.exchange_rate),approve_url=intent.payload.get('approve_url'))

    @staticmethod
    def capture_paypal_exchange_order(db:Session,order_id:str,operation_id:uuid.UUID,cajero_id):
        from app.models.payment_intent import PaymentIntent
        from app.services.payment_flow_service import PaymentFlowService,request_key
        from app.services.paypal_service import PayPalService
        actor=db.get(User,cajero_id)
        if not actor or not actor.is_active or not actor.tenant_id or actor.is_platform_admin:
            raise HTTPException(403,'Se requiere una identidad comercial activa')
        intent=db.query(PaymentIntent).filter_by(id=operation_id,provider_order_id=order_id,tenant_id=actor.tenant_id,
            user_id=actor.id,purpose='EXCHANGE').with_for_update().first()
        if not intent:raise HTTPException(404,'Orden PayPal de cambio no disponible')
        if intent.state=='APPLIED':
            exchange_id=(intent.payload or {}).get('exchange_id')
            if not exchange_id:raise HTTPException(409,'El pago aplicado requiere conciliación')
            return ExchangeService.get_exchange_by_id(db,uuid.UUID(exchange_id),actor.tenant_id)
        if intent.state=='REFUNDED':
            raise HTTPException(409,'PayPal reembolsó el cobro porque el cambio no se pudo aplicar. Cotiza de nuevo antes de iniciar otra operación.')
        creds=PaymentFlowService.credentials(db,actor,intent)
        creds.pop('_credential_id',None)
        if PaymentFlowService.merchant(creds)!=intent.merchant_key:
            raise HTTPException(409,'La cuenta PayPal original requiere conciliación')
        if intent.state in ('CREATED','CAPTURING'):
            intent.state='CAPTURING';db.commit()
            api={k:v for k,v in creds.items() if k!='brand_name'}
            order=PayPalService.get_order_details(order_id,**api)
            if order.get('status')=='COMPLETED':
                raw=order
            elif order.get('status')=='APPROVED':
                captured=PayPalService.capture_order(order_id,request_id=request_key(intent,'capture'),**api)
                raw=captured.get('raw_response',{})
            else:
                raise HTTPException(409,'El cliente todavía no aprobó la orden PayPal')
            capture_id=PaymentFlowService.verify_capture(intent,raw)
            from app.core.payment_evidence import capture_claimed_elsewhere
            if capture_claimed_elsewhere(db,capture_id,commercial_id=intent.id):
                intent.state='REVIEW';db.commit()
                raise HTTPException(409,'La captura ya está vinculada a otra operación; requiere conciliación')
            intent.capture_id=capture_id;intent.state='CAPTURED';db.commit()
        if intent.state=='REFUND_PENDING':
            return PaymentFlowService.compensate(db,intent,creds)
        if intent.state!='CAPTURED':raise HTTPException(409,'La orden PayPal requiere conciliación antes de continuar')
        try:
            request=ProductExchangeCreate.model_validate(intent.payload['request'])
            if ExchangeService._fingerprint(request)!=intent.payload.get('request_fingerprint'):
                raise HTTPException(409,'La solicitud asociada a la orden cambió')
            return ExchangeService.create_exchange(db,request,cajero_id,paypal_payment_intent_id=intent.id)
        except Exception:
            db.rollback()
            current=db.query(PaymentIntent).filter_by(id=operation_id,tenant_id=actor.tenant_id).with_for_update().first()
            if current and current.state=='APPLIED':
                return ExchangeService.get_exchange_by_id(db,uuid.UUID(current.payload['exchange_id']),actor.tenant_id)
            if current and current.state=='CAPTURED':
                return PaymentFlowService.compensate(db,current,{k:v for k,v in creds.items() if k!='brand_name'})
            raise

    @staticmethod
    @commercial_transaction
    def refund_paypal_exchange(db:Session,data:ProductExchangeCreate,cajero_id):
        from app.models.payment_intent import PaymentIntent
        from app.services.payment_flow_service import PaymentFlowService,request_key
        from app.services.paypal_service import PayPalService,ProviderError
        from app.config import settings
        actor=db.get(User,cajero_id) if cajero_id else None
        if not actor or not actor.is_active or not actor.tenant_id or actor.is_platform_admin:
            raise HTTPException(403,'Se requiere una identidad comercial')
        # Return an already-applied result before checking receipt availability:
        # the successful return itself has consumed the original units.
        if data.client_operation_id:
            receipt_key=data.numero_recibo.strip().upper()
            replay_sale=(db.query(Sale).filter(func.upper(Sale.numero_recibo)==receipt_key,
                Sale.tenant_id==actor.tenant_id).first())
            if replay_sale:
                replay_payment=(db.query(PaymentIntent).filter_by(sale_id=replay_sale.id,
                    tenant_id=actor.tenant_id).first())
                replay_claim=ExchangeService._paypal_refund_claims(replay_payment).get(str(data.client_operation_id))
                if replay_claim:
                    digest=ExchangeService._fingerprint(data)
                    if replay_claim.get('request_fingerprint')!=digest:
                        raise HTTPException(409,'El identificador ya pertenece a otra devolución PayPal')
                    if replay_claim.get('state')=='APPLIED':
                        operation=db.execute(select(SyncOperation.__table__).where(
                            SyncOperation.__table__.c.client_operation_id==data.client_operation_id)).mappings().first()
                        if (operation and operation['tenant_id']==actor.tenant_id and operation['user_id']==actor.id
                                and operation['operation_type']=='DEVOLUCION_PRENDA'
                                and operation['request_fingerprint']==digest):
                            return ExchangeService.get_exchange_by_id(db,operation['entity_id'],actor.tenant_id)
                        raise HTTPException(409,'El reembolso requiere conciliación con su nota; no repitas la devolución')
        prepared=ExchangeService._prepare_exchange(db,data,cajero_id,validate_payment=False)
        amount_bob=money(-prepared['diferencia_total'],positive=True)
        if data.metodo_pago_diferencia!='PAYPAL' or prepared['diferencia_total']>=0:
            raise HTTPException(400,'PayPal solo reembolsa una devolución o diferencia a favor')
        if not data.quote_hash or data.quote_hash!=ExchangeService._quote_hash(prepared):
            raise HTTPException(409,'Actualiza la cotización antes de reembolsar')
        sale=prepared['venta']
        payment=(db.query(PaymentIntent).filter_by(sale_id=sale.id,tenant_id=sale.tenant_id)
            .populate_existing().with_for_update().first())
        if (getattr(sale.metodo_pago,'value',sale.metodo_pago)!='PAYPAL' or not payment or not payment.capture_id
                or payment.state!='APPLIED'):
            raise HTTPException(409,'PayPal solo puede devolver a la cuenta de la compra original')
        rate=Decimal(str(payment.exchange_rate))
        amount_usd=money(PayPalService.convert_bob_to_usd(amount_bob,rate=rate),positive=True)
        actor=db.get(User,cajero_id)
        creds=PaymentFlowService.credentials(db,actor,payment)
        creds.pop('_credential_id',None)
        claims=copy.deepcopy(ExchangeService._paypal_refund_claims(payment))
        key=str(data.client_operation_id);digest=ExchangeService._fingerprint(data)
        claim=claims.get(key)
        if claim and claim.get('request_fingerprint')!=digest:
            raise HTTPException(409,'El identificador ya pertenece a otra devolución PayPal')
        if claim and claim.get('state')=='APPLIED':
            operation=db.execute(select(SyncOperation.__table__).where(SyncOperation.__table__.c.client_operation_id==data.client_operation_id)).mappings().first()
            if operation:return ExchangeService.get_exchange_by_id(db,operation['entity_id'],sale.tenant_id)
            raise HTTPException(409,'El reembolso requiere conciliación con su nota')
        if claim and (money(claim.get('amount_bob',0))!=amount_bob or money(claim.get('amount_usd',0))!=amount_usd):
            raise HTTPException(409,'El importe de la cotización cambió; genera una nueva solicitud')
        if not claim:
            reserved=sum((money(row.get('amount_usd',0)) for row in claims.values()
                if isinstance(row,dict) and row.get('state') in ('PENDING','COMPLETED','APPLIED','REVIEW')),Decimal('0.00'))
            if reserved+amount_usd>money(payment.amount_usd):
                raise HTTPException(409,'El reembolso excede el saldo aún reembolsable de la compra PayPal')
            claim={'state':'PENDING','amount_bob':str(amount_bob),'amount_usd':str(amount_usd),
                'request_fingerprint':digest,'request':data.model_dump(mode='json'),
                'request_id':request_key(SimpleNamespace(id=data.client_operation_id),'refund')}
            claims[key]=claim;payment.payload={**(payment.payload or {}),'exchange_refunds':claims};db.commit()
        # Do not hold the payment row lock while waiting on PayPal. The durable
        # PENDING claim already reserves these units and the refund idempotency key.
        capture_id=payment.capture_id
        db.commit()
        payment_id=payment.id
        refund_id_to_check=claim.get('refund_id')
        try:
            if refund_id_to_check:
                result=PayPalService.get_refund_details(refund_id_to_check,**{k:v for k,v in creds.items() if k!='brand_name'})
            else:
                result=PayPalService.refund_capture(capture_id,request_id=claim['request_id'],amount_usd=amount_usd,
                    **{k:v for k,v in creds.items() if k!='brand_name'})
        except ProviderError as error:
            payment=(db.query(PaymentIntent).filter_by(id=payment_id,tenant_id=sale.tenant_id)
                .populate_existing().with_for_update().first())
            claims=copy.deepcopy(ExchangeService._paypal_refund_claims(payment))
            claim=claims.get(key)
            if not claim or claim.get('request_fingerprint')!=digest:
                raise HTTPException(409,'No se encontró la solicitud PayPal original para conciliarla')
            if error.status_code in (502,504):
                db.commit()
                return {'estado':'PENDIENTE','numero_recibo':sale.numero_recibo,'mensaje':'PayPal no confirmó aún; conserva la misma operación y vuelve a consultar antes de reintentar.'}
            claim.update(state='FAILED');claims[key]=claim;payment.payload={**(payment.payload or {}),'exchange_refunds':claims};db.commit()
            raise
        # Merge PayPal's answer into the latest locked JSON value. Other refunds
        # can finish while this request is in flight, so never write a stale copy.
        payment=(db.query(PaymentIntent).filter_by(id=payment_id,tenant_id=sale.tenant_id)
            .populate_existing().with_for_update().first())
        claims=copy.deepcopy(ExchangeService._paypal_refund_claims(payment))
        claim=claims.get(key)
        if not claim or claim.get('request_fingerprint')!=digest:
            raise HTTPException(409,'No se encontró la solicitud PayPal original para conciliarla')
        if claim.get('state')=='APPLIED':
            operation=db.execute(select(SyncOperation.__table__).where(
                SyncOperation.__table__.c.client_operation_id==data.client_operation_id)).mappings().first()
            if operation:return ExchangeService.get_exchange_by_id(db,operation['entity_id'],sale.tenant_id)
            raise HTTPException(409,'El reembolso requiere conciliación con su nota')
        if claim.get('state')=='FAILED':
            raise HTTPException(409,'PayPal rechazó esta solicitud. Genera una nueva cotización para volver a intentarlo.')
        if money(claim.get('amount_bob',0))!=amount_bob or money(claim.get('amount_usd',0))!=amount_usd:
            raise HTTPException(409,'El importe de la cotización cambió; genera una nueva solicitud')
        refund_id=result.get('id') or claim.get('refund_id')
        provider_status=result.get('status')
        if provider_status in ('FAILED','CANCELLED','DENIED'):
            claim.update(state='FAILED',refund_id=refund_id);claims[key]=claim
            payment.payload={**(payment.payload or {}),'exchange_refunds':claims};db.commit()
            raise HTTPException(409,'PayPal rechazó el reembolso; no se registró la devolución. Verifica PayPal o selecciona otro medio de reembolso.')
        if provider_status=='COMPLETED':
            if (not refund_id or result.get('amount',{}).get('currency_code')!='USD'
                    or money(result.get('amount',{}).get('value',0))!=amount_usd):
                claim.update(state='REVIEW',refund_id=refund_id);claims[key]=claim
                payment.payload={**(payment.payload or {}),'exchange_refunds':claims};db.commit()
                return {'estado':'PENDIENTE','numero_recibo':sale.numero_recibo,
                    'mensaje':'PayPal confirmó un importe que no coincide con la cotización. La operación quedó bloqueada para conciliación; no repitas ni pagues por otro medio.'}
        elif provider_status!='PENDING':
            claim.update(state='REVIEW',refund_id=refund_id);claims[key]=claim
            payment.payload={**(payment.payload or {}),'exchange_refunds':claims};db.commit()
            return {'estado':'PENDIENTE','numero_recibo':sale.numero_recibo,
                'mensaje':'PayPal devolvió un estado que requiere conciliación. Las unidades siguen reservadas; no repitas por otro medio.'}
        if provider_status=='PENDING':
            claim.update(state='PENDING',refund_id=refund_id);claims[key]=claim
            payment.payload={**(payment.payload or {}),'exchange_refunds':claims};db.commit()
            return {'estado':'PENDIENTE','numero_recibo':sale.numero_recibo,'mensaje':'PayPal aún no confirma el reembolso. Las unidades quedan reservadas para no duplicar la devolución.'}
        claim.update(state='COMPLETED',refund_id=refund_id);claims[key]=claim
        payment.payload={**(payment.payload or {}),'exchange_refunds':claims};db.commit()
        return ExchangeService.create_exchange(db,data,cajero_id,paypal_refund_operation_id=data.client_operation_id)

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
