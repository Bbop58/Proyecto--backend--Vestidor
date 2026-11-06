import uuid
from decimal import Decimal
from app.core.commercial_transaction import commercial_transaction, lock_branch
from app.models.user import User
from app.models.sale import Sale, SaleStatus
from app.models.sale_detail import SaleDetail
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.branch import Branch
from app.models.product_variant import ProductVariant
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.reservation import Reservation, ReservationStatus, utc_now
from app.models.reservation_detail import ReservationDetail
from app.schemas.reservation import (
    ReservationCreate,
    ReservationResponse,
    ReservationDetailResponse,
)


class ReservationService:

    @staticmethod
    def _format_reservation_response(reserva: Reservation) -> dict:
        detalles_resp = []
        for d in reserva.detalles:
            var = d.variante
            prod = var.producto if var else None
            detalles_resp.append({
                "id": d.id,
                "reserva_id": d.reserva_id,
                "variante_id": d.variante_id,
                "variante_sku": var.sku if var else None,
                "producto_nombre": prod.nombre if prod else None,
                "talla": var.talla if var else None,
                "color": var.color if var else None,
                "cantidad": d.cantidad,
                "precio_unitario": float(d.precio_unitario),
                "subtotal": float(d.subtotal),
                "created_at": d.created_at,
            })

        return {
            "id": reserva.id,
            "codigo": reserva.codigo,
            "cliente_id": reserva.cliente_id,
            "cliente_nombre": reserva.cliente.full_name if reserva.cliente else None,
            "cliente_email": reserva.cliente.email if reserva.cliente else None,
            "sucursal_id": reserva.sucursal_id,
            "sucursal_nombre": reserva.sucursal.nombre if reserva.sucursal else None,
            "sucursal_ciudad": reserva.sucursal.ciudad if reserva.sucursal else None,
            "estado": reserva.estado,
            "fecha_hora_esperada": reserva.fecha_hora_esperada,
            "fecha_expiracion": reserva.fecha_expiracion,
            "fecha_recogida": reserva.fecha_recogida,
            "total_estimado": float(reserva.total_estimado),
            "nota": reserva.nota,
            "activo": reserva.activo,
            "detalles": detalles_resp,
            "created_at": reserva.created_at,
            "updated_at": reserva.updated_at,
        }

    @staticmethod
    @commercial_transaction
    def create_reservation(
        db: Session,
        data: ReservationCreate,
        cliente_id: uuid.UUID,
        _commit: bool = True,
        _expires_at=None
    ) -> dict:
        # Serialize the per-customer active limit across different branches.
        # PostgreSQL NO KEY UPDATE still serializes the customer's reservation
        # limit, while allowing FK KEY SHARE checks by concurrent cash sales.
        user = db.query(User).filter_by(id=cliente_id, is_active=True).with_for_update(of=User, key_share=True).first()
        if not user:
            raise HTTPException(404, "Cliente no disponible")
        lock_branch(db, data.sucursal_id)
        # 1. Validar sucursal
        branch = db.query(Branch).filter(Branch.id == data.sucursal_id, Branch.activa == True).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Sucursal no encontrada o inactiva")

        # 2. Validar límite de reservas activas (máximo 5)
        active_count = db.query(Reservation).filter(
            Reservation.cliente_id == cliente_id,
            Reservation.estado.in_([ReservationStatus.PENDIENTE, ReservationStatus.PREPARADA]),
            Reservation.activo == True
        ).count()
        if active_count >= 5:
            raise HTTPException(
                status_code=400,
                detail="Has alcanzado el límite máximo de 5 reservas activas simultáneas."
            )

        now = utc_now()

        # 3. Validar fecha_hora_esperada
        # Asegurar compatibilidad de zonas horarias
        esperada = data.fecha_hora_esperada
        if esperada.tzinfo is None:
            esperada = esperada.replace(tzinfo=timezone.utc)
        
        if esperada <= now:
            raise HTTPException(
                status_code=400,
                detail="La fecha y hora esperada de recogida debe ser en el futuro."
            )

        if esperada > now + timedelta(hours=24):
            raise HTTPException(400, "La recogida debe estar dentro de las próximas 24 horas")

        totals = {}
        for item in data.items:
            totals[item.variante_id] = totals.get(item.variante_id, 0) + item.cantidad
        if any(quantity > 3 for quantity in totals.values()):
            raise HTTPException(400, "Máximo 3 unidades por variante en una reserva")

        # 4. Generar código único
        codigo = f"RES-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        fecha_expiracion = min(_expires_at, now+timedelta(hours=24)) if _expires_at else now+timedelta(hours=24)
        if fecha_expiracion <= now or esperada > fecha_expiracion:
            raise HTTPException(400, "La reserva venció o la recogida supera su plazo original")

        # 5. Crear cabecera de reserva
        reserva = Reservation(
            tenant_id=branch.tenant_id,
            codigo=codigo,
            cliente_id=cliente_id,
            sucursal_id=data.sucursal_id,
            estado=ReservationStatus.PENDIENTE,
            fecha_hora_esperada=esperada,
            fecha_expiracion=fecha_expiracion,
            total_estimado=0.0,
            nota=data.nota,
            activo=True
        )
        db.add(reserva)
        db.flush()

        total_estimado = Decimal("0.00")

        # 6. Validar y procesar cada ítem
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

            # Buscar inventario en sucursal
            inv = db.query(Inventory).filter(
                Inventory.sucursal_id == data.sucursal_id,
                Inventory.variante_id == item.variante_id,
                Inventory.activo == True
            ).first()
#FIFOOOOOOO
            if not inv or inv.stock_disponible < item.cantidad:
                disponible = inv.stock_disponible if inv else 0
                raise HTTPException(
                    status_code=400,
                    detail=f"Stock insuficiente para '{variant.producto.nombre}' ({variant.talla}/{variant.color}). Solicitado: {item.cantidad}, Disponible: {disponible}"
                )

            # Precio
            from app.core.money import catalog_price
            precio_unitario = catalog_price(variant)
            subtotal = precio_unitario * item.cantidad
            total_estimado += subtotal

            # Bloquear stock
            inv.stock_reservado += item.cantidad

            # Registrar movimiento tipo SALIDA_RESERVA
            mov = InventoryMovement(
                tenant_id=inv.tenant_id,
                inventario_id=inv.id,
                tipo=MovementType.SALIDA_RESERVA,
                cantidad=item.cantidad,
                stock_antes=inv.stock_actual,
                stock_despues=inv.stock_actual,
                referencia=codigo,
                nota=f"Reserva de {item.cantidad} unidad(es)",
                usuario_id=cliente_id
            )
            db.add(mov)

            # Crear detalle
            detalle = ReservationDetail(
                reserva_id=reserva.id,
                variante_id=item.variante_id,
                cantidad=item.cantidad,
                precio_unitario=precio_unitario,
                subtotal=subtotal
            )
            db.add(detalle)

        reserva.total_estimado = total_estimado
        if not _commit:
            db.flush()
            return ReservationService._format_reservation_response(reserva)
        db.commit()
        db.refresh(reserva)

        try:
            from app.services.notification_service import NotificationService
            NotificationService.create(
                db=db,
                user_id=cliente_id,
                title="Reserva Registrada ⏳",
                message=f"Tu reserva #{reserva.codigo} fue registrada con éxito por Bs {total_estimado:.2f}. La reserva vence en 24 horas desde su creación.",
                type="RESERVATION_EXPIRING",
                reference_id=str(reserva.id)
            )
        except Exception:
            pass

        return ReservationService._format_reservation_response(reserva)


    @staticmethod
    def get_reservation_by_id(db: Session, reserva_id: uuid.UUID) -> dict:
        reserva = db.query(Reservation).filter(Reservation.id == reserva_id).first()
        if not reserva:
            raise HTTPException(status_code=404, detail="Reserva no encontrada")
        return ReservationService._format_reservation_response(reserva)

    @staticmethod
    def get_client_reservations(db: Session, cliente_id: uuid.UUID) -> List[dict]:
        reservas = (
            db.query(Reservation)
            .filter(Reservation.cliente_id == cliente_id)
            .order_by(Reservation.created_at.desc())
            .all()
        )
        return [ReservationService._format_reservation_response(r) for r in reservas]

    @staticmethod
    def get_reservations(
        db: Session,
        sucursal_id: Optional[uuid.UUID] = None,
        estado: Optional[ReservationStatus] = None,
        cliente_id: Optional[uuid.UUID] = None,
        limit: int = 100,
        offset: int = 0,
        tenant_id: Optional[uuid.UUID] = None
    ) -> List[dict]:
        q = db.query(Reservation)
        if tenant_id:
            q = q.filter(Reservation.tenant_id == tenant_id)
        if sucursal_id:
            q = q.filter(Reservation.sucursal_id == sucursal_id)
        if estado:
            q = q.filter(Reservation.estado == estado)
        if cliente_id:
            q = q.filter(Reservation.cliente_id == cliente_id)

        reservas = q.order_by(Reservation.created_at.desc()).offset(offset).limit(limit).all()
        return [ReservationService._format_reservation_response(r) for r in reservas]

    @staticmethod
    @commercial_transaction
    def cancel_reservation(
        db: Session,
        reserva_id: uuid.UUID,
        user_id: uuid.UUID,
        is_staff: bool = False
    ) -> dict:
        reserva = db.query(Reservation).filter(Reservation.id == reserva_id).first()
        if not reserva:
            raise HTTPException(status_code=404, detail="Reserva no encontrada")
        lock_branch(db, reserva.sucursal_id)
        db.refresh(reserva)

        if not is_staff and reserva.cliente_id != user_id:
            raise HTTPException(status_code=403, detail="No tienes permiso para cancelar esta reserva")

        if reserva.estado not in [ReservationStatus.PENDIENTE, ReservationStatus.PREPARADA]:
            raise HTTPException(
                status_code=400,
                detail=f"Solo se pueden cancelar reservas en estado PENDIENTE o PREPARADA (estado actual: {reserva.estado.value})"
            )

        # Liberar stock reservado
        for detalle in reserva.detalles:
            inv = db.query(Inventory).filter(
                Inventory.sucursal_id == reserva.sucursal_id,
                Inventory.variante_id == detalle.variante_id
            ).first()
            if not inv:
                raise HTTPException(409, "Falta el inventario de la reserva")
            if inv:
                if inv.stock_reservado < detalle.cantidad:
                    raise HTTPException(409, "El inventario no concuerda con la reserva")
                inv.stock_reservado -= detalle.cantidad
                mov = InventoryMovement(
                    tenant_id=getattr(reserva, "tenant_id", None),
                    inventario_id=inv.id,
                    tipo=MovementType.LIBERACION_RESERVA,
                    cantidad=detalle.cantidad,
                    stock_antes=inv.stock_actual,
                    stock_despues=inv.stock_actual,
                    referencia=reserva.codigo,
                    nota="Liberación de stock por cancelación de reserva",
                    usuario_id=user_id
                )
                db.add(mov)

        reserva.estado = ReservationStatus.CANCELADA
        db.commit()
        db.refresh(reserva)
        return ReservationService._format_reservation_response(reserva)

    @staticmethod
    @commercial_transaction
    def prepare_reservation(
        db: Session,
        reserva_id: uuid.UUID,
        user_id: uuid.UUID
    ) -> dict:
        reserva = db.query(Reservation).filter(Reservation.id == reserva_id).first()
        if not reserva:
            raise HTTPException(status_code=404, detail="Reserva no encontrada")
        lock_branch(db, reserva.sucursal_id)
        db.refresh(reserva)

        if reserva.estado != ReservationStatus.PENDIENTE:
            raise HTTPException(
                status_code=400,
                detail=f"Solo se pueden preparar reservas en estado PENDIENTE (estado actual: {reserva.estado.value})"
            )

        now = utc_now()
        if reserva.fecha_expiracion < now:
            raise HTTPException(
                status_code=400,
                detail="No se puede preparar una reserva expirada"
            )

        reserva.estado = ReservationStatus.PREPARADA
        db.commit()
        db.refresh(reserva)

        try:
            from app.services.notification_service import NotificationService
            sucursal_nombre = reserva.sucursal.nombre if reserva.sucursal else "la sucursal"
            NotificationService.create(
                db=db,
                user_id=reserva.cliente_id,
                title="¡Tu reserva está lista para recoger! 📦",
                message=f"Tu reserva #{reserva.codigo} ya está preparada en {sucursal_nombre}. Puedes pasar a retirarla.",
                type="RESERVATION_READY",
                reference_id=str(reserva.id)
            )
        except Exception:
            pass

        return ReservationService._format_reservation_response(reserva)


    @staticmethod
    @commercial_transaction
    def pickup_reservation(
        db: Session,
        reserva_id: uuid.UUID,
        user_id: uuid.UUID
    ) -> dict:
        reserva = db.query(Reservation).filter(Reservation.id == reserva_id).first()
        if not reserva:
            raise HTTPException(status_code=404, detail="Reserva no encontrada")
        lock_branch(db, reserva.sucursal_id)
        db.refresh(reserva)

        if reserva.estado != ReservationStatus.COMPLETADA:
            raise HTTPException(
                status_code=400,
                detail="La reserva debe liquidarse en caja antes de confirmar su recogida"
            )

        paid_sale = (db.query(Sale.id).join(SaleDetail).filter(
            SaleDetail.reserva_id == reserva.id, Sale.estado == SaleStatus.COMPLETADA).first())
        if not paid_sale:
            raise HTTPException(409, "La reserva no tiene una venta vigente")
        reserva.estado = ReservationStatus.RECOGIDA
        reserva.fecha_recogida = utc_now()
        db.commit()
        db.refresh(reserva)
        return ReservationService._format_reservation_response(reserva)

    @staticmethod
    @commercial_transaction
    def expire_pending_reservations(db: Session) -> dict:
        now = utc_now()
        expired_list = (db.query(Reservation).filter(
            Reservation.estado.in_([ReservationStatus.PENDIENTE, ReservationStatus.PREPARADA]),
            Reservation.fecha_expiracion <= now).order_by(Reservation.id).all())
        for branch_id in sorted({r.sucursal_id for r in expired_list}, key=str):
            lock_branch(db, branch_id)
        count = 0
        for reserva in expired_list:
            db.refresh(reserva)
            if reserva.estado not in (ReservationStatus.PENDIENTE, ReservationStatus.PREPARADA):
                continue
            for detalle in reserva.detalles:
                inv = db.query(Inventory).filter_by(sucursal_id=reserva.sucursal_id,
                                                    variante_id=detalle.variante_id).first()
                if not inv or inv.stock_reservado < detalle.cantidad:
                    raise HTTPException(409, "El inventario no concuerda con la reserva")
                inv.stock_reservado -= detalle.cantidad
                db.add(InventoryMovement(tenant_id=inv.tenant_id, inventario_id=inv.id,
                    tipo=MovementType.LIBERACION_RESERVA, cantidad=detalle.cantidad,
                    stock_antes=inv.stock_actual, stock_despues=inv.stock_actual,
                    referencia=reserva.codigo, nota="Expiración de reserva", usuario_id=None))
            reserva.estado = ReservationStatus.EXPIRADA
            count += 1
        db.commit()
        return {"total_expiradas": count, "message": f"Se procesaron {count} reserva(s) expirada(s)."}
