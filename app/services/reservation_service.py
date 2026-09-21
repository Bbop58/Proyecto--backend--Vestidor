import uuid
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
    def create_reservation(
        db: Session,
        data: ReservationCreate,
        cliente_id: uuid.UUID
    ) -> dict:
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

        # 4. Generar código único
        codigo = f"RES-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        fecha_expiracion = now + timedelta(hours=24)

        # 5. Crear cabecera de reserva
        reserva = Reservation(
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

        total_estimado = 0.0

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

            if not inv or inv.stock_disponible < item.cantidad:
                disponible = inv.stock_disponible if inv else 0
                raise HTTPException(
                    status_code=400,
                    detail=f"Stock insuficiente para '{variant.producto.nombre}' ({variant.talla}/{variant.color}). Solicitado: {item.cantidad}, Disponible: {disponible}"
                )

            # Precio
            precio_unitario = float(variant.producto.precio_base) + float(variant.precio_extra)
            subtotal = precio_unitario * item.cantidad
            total_estimado += subtotal

            # Bloquear stock
            inv.stock_reservado += item.cantidad

            # Registrar movimiento tipo SALIDA_RESERVA
            mov = InventoryMovement(
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
        db.commit()
        db.refresh(reserva)

        try:
            from app.services.notification_service import NotificationService
            NotificationService.create(
                db=db,
                user_id=cliente_id,
                title="Reserva Registrada ⏳",
                message=f"Tu reserva #{reserva.codigo_reserva} fue registrada con éxito por Bs {total_estimado:.2f}. Tienes 48h de vigencia una vez que esté preparada.",
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
        offset: int = 0
    ) -> List[dict]:
        q = db.query(Reservation)
        if sucursal_id:
            q = q.filter(Reservation.sucursal_id == sucursal_id)
        if estado:
            q = q.filter(Reservation.estado == estado)
        if cliente_id:
            q = q.filter(Reservation.cliente_id == cliente_id)

        reservas = q.order_by(Reservation.created_at.desc()).offset(offset).limit(limit).all()
        return [ReservationService._format_reservation_response(r) for r in reservas]

    @staticmethod
    def cancel_reservation(
        db: Session,
        reserva_id: uuid.UUID,
        user_id: uuid.UUID,
        is_staff: bool = False
    ) -> dict:
        reserva = db.query(Reservation).filter(Reservation.id == reserva_id).first()
        if not reserva:
            raise HTTPException(status_code=404, detail="Reserva no encontrada")

        if not is_staff and reserva.cliente_id != user_id:
            raise HTTPException(status_code=403, detail="No tienes permiso para cancelar esta reserva")

        if reserva.estado != ReservationStatus.PENDIENTE:
            raise HTTPException(
                status_code=400,
                detail=f"Solo se pueden cancelar reservas en estado PENDIENTE (estado actual: {reserva.estado.value})"
            )

        # Liberar stock reservado
        for detalle in reserva.detalles:
            inv = db.query(Inventory).filter(
                Inventory.sucursal_id == reserva.sucursal_id,
                Inventory.variante_id == detalle.variante_id
            ).first()
            if inv:
                inv.stock_reservado = max(0, inv.stock_reservado - detalle.cantidad)
                mov = InventoryMovement(
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
    def prepare_reservation(
        db: Session,
        reserva_id: uuid.UUID,
        user_id: uuid.UUID
    ) -> dict:
        reserva = db.query(Reservation).filter(Reservation.id == reserva_id).first()
        if not reserva:
            raise HTTPException(status_code=404, detail="Reserva no encontrada")

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
                message=f"Tu reserva #{reserva.codigo_reserva} ya está preparada en {sucursal_nombre}. Puedes pasar a retirarla.",
                type="RESERVATION_READY",
                reference_id=str(reserva.id)
            )
        except Exception:
            pass

        return ReservationService._format_reservation_response(reserva)


    @staticmethod
    def pickup_reservation(
        db: Session,
        reserva_id: uuid.UUID,
        user_id: uuid.UUID
    ) -> dict:
        reserva = db.query(Reservation).filter(Reservation.id == reserva_id).first()
        if not reserva:
            raise HTTPException(status_code=404, detail="Reserva no encontrada")

        if reserva.estado != ReservationStatus.PREPARADA:
            raise HTTPException(
                status_code=400,
                detail=f"Solo se pueden recoger reservas en estado PREPARADA (estado actual: {reserva.estado.value})"
            )

        reserva.estado = ReservationStatus.RECOGIDA
        reserva.fecha_recogida = utc_now()
        db.commit()
        db.refresh(reserva)
        return ReservationService._format_reservation_response(reserva)

    @staticmethod
    def expire_pending_reservations(db: Session) -> dict:
        now = utc_now()
        expired_list = (
            db.query(Reservation)
            .filter(
                Reservation.estado == ReservationStatus.PENDIENTE,
                Reservation.fecha_expiracion < now
            )
            .all()
        )

        count = 0
        for reserva in expired_list:
            for detalle in reserva.detalles:
                inv = db.query(Inventory).filter(
                    Inventory.sucursal_id == reserva.sucursal_id,
                    Inventory.variante_id == detalle.variante_id
                ).first()
                if inv:
                    inv.stock_reservado = max(0, inv.stock_reservado - detalle.cantidad)
                    mov = InventoryMovement(
                        inventario_id=inv.id,
                        tipo=MovementType.LIBERACION_RESERVA,
                        cantidad=detalle.cantidad,
                        stock_antes=inv.stock_actual,
                        stock_despues=inv.stock_actual,
                        referencia=reserva.codigo,
                        nota="Liberación automática por expiración de reserva",
                        usuario_id=None
                    )
                    db.add(mov)

            reserva.estado = ReservationStatus.EXPIRADA
            count += 1

        db.commit()
        return {
            "total_expiradas": count,
            "message": f"Se procesaron {count} reserva(s) expirada(s)."
        }
