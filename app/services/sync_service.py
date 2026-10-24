import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.models.sync_operation import SyncOperation
from app.models.branch import Branch
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.sale import Sale, SaleType, SaleStatus, PaymentMethod
from app.models.sale_detail import SaleDetail
from app.models.user import User
from app.schemas.sync import (
    SyncBatchRequest,
    SyncBatchResponse,
    SyncOperationResult,
    SyncHistoryItemResponse
)
from app.services.audit_service import AuditService


def utc_now():
    return datetime.now(timezone.utc)


class SyncService:
    @staticmethod
    def process_sync_batch(
        db: Session,
        batch_request: SyncBatchRequest,
        current_user: User,
        tenant_id: uuid.UUID
    ) -> SyncBatchResponse:
        """
        Procesa un lote de operaciones offline garantizando:
        1. Idempotencia estricta por client_operation_id (RNF05).
        2. Integridad transaccional e inventario atómico (RNF04).
        3. Detección y registro de conflictos de stock.
        """
        results: List[SyncOperationResult] = []
        total_confirmed = 0
        total_rejected = 0
        total_duplicates = 0

        for op in batch_request.operations:
            # ── PASO 1: Comprobar Idempotencia ──
            existing_sync = db.query(SyncOperation).filter(
                SyncOperation.client_operation_id == op.client_operation_id
            ).first()

            if existing_sync:
                # IDEMPOTENCIA: Operación ya procesada con anterioridad
                payload = existing_sync.response_payload or {}
                results.append(SyncOperationResult(
                    client_operation_id=op.client_operation_id,
                    status=existing_sync.sync_status,
                    is_duplicate=True,
                    receipt_number=payload.get("receipt_number"),
                    server_entity_id=existing_sync.entity_id,
                    message=f"[IDEMPOTENCIA] Operación ya procesada previamente con estado '{existing_sync.sync_status}'. No se duplicó inventario ni venta.",
                    error_detail=existing_sync.error_detail
                ))
                total_duplicates += 1
                if existing_sync.sync_status == "CONFIRMADA":
                    total_confirmed += 1
                else:
                    total_rejected += 1
                continue

            # ── PASO 2: Validar Sucursal y Tenant ──
            branch = db.query(Branch).filter(
                Branch.id == op.sucursal_id,
                Branch.tenant_id == tenant_id,
                Branch.activa == True
            ).first()

            if not branch:
                error_msg = f"La sucursal {op.sucursal_id} no existe, está inactiva o no pertenece a su empresa."
                sync_op = SyncOperation(
                    client_operation_id=op.client_operation_id,
                    tenant_id=tenant_id,
                    branch_id=op.sucursal_id,
                    user_id=current_user.id,
                    operation_type=op.operation_type,
                    client_created_at=op.fecha_local,
                    sync_status="CONFLICTO",
                    error_detail=error_msg,
                    response_payload={"status": "CONFLICTO", "error": error_msg}
                )
                db.add(sync_op)
                db.commit()

                results.append(SyncOperationResult(
                    client_operation_id=op.client_operation_id,
                    status="CONFLICTO",
                    is_duplicate=False,
                    message="Operación rechazada por conflicto de sucursal.",
                    error_detail=error_msg
                ))
                total_rejected += 1
                continue

            branch_id = branch.id
            branch_nombre = branch.nombre

            # ── PASO 3: Validar Disponibilidad de Stock Físico ──
            insufficient_stock_error: Optional[str] = None
            inventories_to_update = []

            for item in op.items:
                inv = db.query(Inventory).filter(
                    Inventory.variante_id == item.variante_id,
                    Inventory.sucursal_id == branch_id
                ).with_for_update().first()

                if not inv or inv.stock_disponible < item.cantidad:
                    disponible = inv.stock_disponible if inv else 0
                    insufficient_stock_error = (
                        f"Stock insuficiente para la prenda variante {item.variante_id}: "
                        f"disponible {disponible} unidad(es), solicitado {item.cantidad} unidad(es)."
                    )
                    break
                inventories_to_update.append((inv, item))

            if insufficient_stock_error:
                # Conflicto de inventario: Se registra el intento para auditoría sin abortar el lote
                sync_op = SyncOperation(
                    client_operation_id=op.client_operation_id,
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    user_id=current_user.id,
                    operation_type=op.operation_type,
                    client_created_at=op.fecha_local,
                    sync_status="RECHAZADA_POR_STOCK",
                    error_detail=insufficient_stock_error,
                    response_payload={"status": "RECHAZADA_POR_STOCK", "error": insufficient_stock_error}
                )
                db.add(sync_op)

                try:
                    AuditService.log_event(
                        db=db,
                        user_id=current_user.id,
                        action="CONFLICTO_STOCK_OFFLINE",
                        module="Sincronización Offline",
                        details={
                            "client_operation_id": str(op.client_operation_id),
                            "sucursal": branch_nombre,
                            "error": insufficient_stock_error,
                            "monto_total": float(op.monto_total)
                        }
                    )
                except Exception:
                    pass

                db.commit()

                results.append(SyncOperationResult(
                    client_operation_id=op.client_operation_id,
                    status="RECHAZADA_POR_STOCK",
                    is_duplicate=False,
                    message="Venta offline rechazada por falta de stock central.",
                    error_detail=insufficient_stock_error
                ))
                total_rejected += 1
                continue

            # ── PASO 4: Confirmar Venta, Descontar Stock y Crear Kardex ──
            try:
                # 4.1 Descontar stock
                for inv, item in inventories_to_update:
                    inv.stock_actual -= item.cantidad

                # 4.2 Generar número de recibo oficial
                short_uuid = str(uuid.uuid4())[:6].upper()
                fecha_str = op.fecha_local.strftime("%Y%m%d")
                numero_recibo = f"VTA-OFF-{fecha_str}-{short_uuid}"

                # 4.3 Impuestos
                monto_total = Decimal(str(op.monto_total))
                impuesto_iva = round(monto_total * Decimal("0.13"), 2)
                monto_neto = round(monto_total - impuesto_iva, 2)

                # Método de pago
                metodo = PaymentMethod.EFECTIVO
                if op.metodo_pago and op.metodo_pago.upper() in PaymentMethod.__members__:
                    metodo = PaymentMethod[op.metodo_pago.upper()]

                # 4.4 Crear Venta
                sale = Sale(
                    tenant_id=tenant_id,
                    sucursal_id=branch.id,
                    cajero_id=current_user.id,
                    cliente_id=op.cliente_id,
                    tipo=SaleType.PRESENCIAL,
                    estado=SaleStatus.COMPLETADA,
                    metodo_pago=metodo,
                    monto_total=monto_total,
                    impuesto_iva=impuesto_iva,
                    monto_neto=monto_neto,
                    monto_recibido=Decimal(str(op.monto_efectivo_recibido)) if op.monto_efectivo_recibido is not None else None,
                    cambio=Decimal(str(op.cambio_entregado)) if op.cambio_entregado is not None else None,
                    numero_recibo=numero_recibo,
                    nota=f"[SYNC OFFLINE] {op.notas or ''}".strip(),
                    created_at=op.fecha_local
                )
                db.add(sale)
                db.flush()

                # 4.5 Detalles de Venta y Movimientos de Inventario (Kardex)
                for inv, item in inventories_to_update:
                    item_precio = Decimal(str(item.precio_unitario))
                    subtotal = item_precio * item.cantidad
                    det = SaleDetail(
                        venta_id=sale.id,
                        variante_id=item.variante_id,
                        cantidad=item.cantidad,
                        precio_unitario=item_precio,
                        subtotal=subtotal
                    )
                    db.add(det)

                    mov = InventoryMovement(
                        tenant_id=tenant_id,
                        inventario_id=inv.id,
                        tipo=MovementType.SALIDA_VENTA,
                        cantidad=-item.cantidad,
                        stock_antes=inv.stock_actual + item.cantidad,
                        stock_despues=inv.stock_actual,
                        referencia=numero_recibo,
                        nota=f"Sincronización de venta offline {numero_recibo}",
                        usuario_id=current_user.id,
                        created_at=op.fecha_local
                    )
                    db.add(mov)

                # 4.6 Registrar SyncOperation para Idempotencia
                cached_payload = {
                    "receipt_number": numero_recibo,
                    "server_sale_id": str(sale.id),
                    "status": "CONFIRMADA",
                    "monto_total": float(monto_total)
                }

                sync_op = SyncOperation(
                    client_operation_id=op.client_operation_id,
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    user_id=current_user.id,
                    operation_type=op.operation_type,
                    client_created_at=op.fecha_local,
                    sync_status="CONFIRMADA",
                    entity_id=sale.id,
                    response_payload=cached_payload
                )
                db.add(sync_op)

                # Auditoría
                try:
                    AuditService.log_event(
                        db=db,
                        user_id=current_user.id,
                        action="SYNC_VENTA_OFFLINE_CONFIRMADA",
                        module="Sincronización Offline",
                        details={
                            "client_operation_id": str(op.client_operation_id),
                            "numero_recibo": numero_recibo,
                            "sucursal": branch_nombre,
                            "monto_total": float(monto_total),
                            "items_count": len(op.items)
                        }
                    )
                except Exception:
                    pass

                db.commit()

                results.append(SyncOperationResult(
                    client_operation_id=op.client_operation_id,
                    status="CONFIRMADA",
                    is_duplicate=False,
                    receipt_number=numero_recibo,
                    server_entity_id=sale.id,
                    message="Venta offline sincronizada y confirmada exitosamente."
                ))
                total_confirmed += 1

            except Exception as e:
                db.rollback()
                error_msg = f"Error transaccional al confirmar venta offline: {str(e)}"
                sync_op = SyncOperation(
                    client_operation_id=op.client_operation_id,
                    tenant_id=tenant_id,
                    branch_id=branch.id,
                    user_id=current_user.id,
                    operation_type=op.operation_type,
                    client_created_at=op.fecha_local,
                    sync_status="CONFLICTO",
                    error_detail=error_msg,
                    response_payload={"status": "CONFLICTO", "error": error_msg}
                )
                db.add(sync_op)
                db.commit()

                results.append(SyncOperationResult(
                    client_operation_id=op.client_operation_id,
                    status="CONFLICTO",
                    is_duplicate=False,
                    message="Error al procesar la venta offline.",
                    error_detail=error_msg
                ))
                total_rejected += 1

        return SyncBatchResponse(
            processed_at=utc_now(),
            total_received=len(batch_request.operations),
            total_confirmed=total_confirmed,
            total_rejected=total_rejected,
            total_duplicates=total_duplicates,
            results=results
        )

    @staticmethod
    def get_sync_history(
        db: Session,
        tenant_id: uuid.UUID,
        limit: int = 50
    ) -> List[SyncOperation]:
        """Devuelve el historial de sincronizaciones offline del tenant actual"""
        return db.query(SyncOperation).filter(
            SyncOperation.tenant_id == tenant_id
        ).order_by(desc(SyncOperation.created_at)).limit(limit).all()
