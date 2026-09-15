"""
inventory_service.py — Fase 3: Lógica de negocio de Inventario y Stock

Responsabilidades:
  - Gestión de stock por sucursal/variante
  - Recepción de productos (ENTRADA)
  - Ajuste manual de stock
  - Registro de movimientos
  - Consulta de disponibilidad pública
  - Generación de alertas de stock
"""
from typing import List, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.product_variant import ProductVariant
from app.models.branch import Branch
from app.schemas.inventory import (
    InventoryUpdate,
    ReceptionCreate,
    StockAdjustment,
    MovementResponse,
    AvailabilityResponse,
    AlertResponse,
)


class InventoryService:

    # ─────────────────────────────── HELPERS ──────────────────────────────────

    @staticmethod
    def _get_or_create_inventory(
        db: Session,
        sucursal_id: uuid.UUID,
        variante_id: uuid.UUID
    ) -> Inventory:
        """Retorna el registro de inventario existente o crea uno vacío."""
        inv = (
            db.query(Inventory)
            .filter(
                Inventory.sucursal_id == sucursal_id,
                Inventory.variante_id == variante_id
            )
            .first()
        )
        if not inv:
            inv = Inventory(
                sucursal_id=sucursal_id,
                variante_id=variante_id,
                stock_actual=0,
                stock_reservado=0,
                stock_minimo=5,
            )
            db.add(inv)
            db.flush()
        return inv

    @staticmethod
    def _register_movement(
        db: Session,
        inventario: Inventory,
        tipo: MovementType,
        cantidad: int,
        usuario_id: Optional[uuid.UUID] = None,
        referencia: Optional[str] = None,
        nota: Optional[str] = None,
    ) -> InventoryMovement:
        """Crea y persiste un movimiento de inventario."""
        mov = InventoryMovement(
            inventario_id=inventario.id,
            tipo=tipo,
            cantidad=cantidad,
            stock_antes=inventario.stock_actual,
            stock_despues=inventario.stock_actual,   # Se actualiza abajo
            usuario_id=usuario_id,
            referencia=referencia,
            nota=nota,
        )
        # stock_despues se calcula después de modificar el inventario
        db.add(mov)
        return mov

    # ─────────────────────────────── STOCK QUERIES ────────────────────────────

    @staticmethod
    def get_stock_by_branch(
        db: Session,
        sucursal_id: uuid.UUID,
        solo_con_stock: bool = False
    ) -> List[Inventory]:
        branch = db.query(Branch).filter(Branch.id == sucursal_id, Branch.activa == True).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Sucursal no encontrada o inactiva")

        q = db.query(Inventory).filter(Inventory.sucursal_id == sucursal_id, Inventory.activo == True)
        if solo_con_stock:
            q = q.filter(Inventory.stock_actual > 0)
        return q.all()

    @staticmethod
    def get_inventory_item(db: Session, inventario_id: uuid.UUID) -> Inventory:
        inv = db.query(Inventory).filter(Inventory.id == inventario_id).first()
        if not inv:
            raise HTTPException(status_code=404, detail="Registro de inventario no encontrado")
        return inv

    @staticmethod
    def update_inventory_settings(
        db: Session,
        inventario_id: uuid.UUID,
        data: InventoryUpdate
    ) -> Inventory:
        """Actualiza stock_minimo y/o ubicación (no el stock directamente)."""
        inv = InventoryService.get_inventory_item(db, inventario_id)
        if data.stock_minimo is not None:
            inv.stock_minimo = data.stock_minimo
        if data.ubicacion is not None:
            inv.ubicacion = data.ubicacion
        db.commit()
        db.refresh(inv)
        return inv

    # ─────────────────────────────── RECEPTION ────────────────────────────────

    @staticmethod
    def receive_products(
        db: Session,
        data: ReceptionCreate,
        usuario_id: Optional[uuid.UUID] = None
    ) -> dict:
        """
        Recibe productos en una sucursal:
          - Crea registro de inventario si no existe
          - Suma cantidad a stock_actual
          - Registra movimiento ENTRADA
        """
        # Validar sucursal
        branch = db.query(Branch).filter(Branch.id == data.sucursal_id, Branch.activa == True).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Sucursal no encontrada o inactiva")

        items_procesados = 0
        for item in data.productos:
            # Validar variante
            variante = db.query(ProductVariant).filter(
                ProductVariant.id == item.variante_id,
                ProductVariant.activo == True
            ).first()
            if not variante:
                raise HTTPException(
                    status_code=400,
                    detail=f"Variante {item.variante_id} no encontrada o inactiva"
                )

            inv = InventoryService._get_or_create_inventory(db, data.sucursal_id, item.variante_id)
            stock_antes = inv.stock_actual
            inv.stock_actual += item.cantidad

            mov = InventoryMovement(
                inventario_id=inv.id,
                tipo=MovementType.ENTRADA,
                cantidad=item.cantidad,
                stock_antes=stock_antes,
                stock_despues=inv.stock_actual,
                usuario_id=usuario_id,
                referencia=data.factura,
                nota=data.nota,
            )
            db.add(mov)
            items_procesados += 1

        db.commit()
        return {
            "sucursal_id": data.sucursal_id,
            "items_procesados": items_procesados,
            "message": f"Se recibieron {items_procesados} variante(s) en la sucursal."
        }

    # ─────────────────────────────── ADJUSTMENT ───────────────────────────────

    @staticmethod
    def adjust_stock(
        db: Session,
        data: StockAdjustment,
        usuario_id: Optional[uuid.UUID] = None
    ) -> Inventory:
        inv = InventoryService.get_inventory_item(db, data.inventario_id)
        nuevo_stock = inv.stock_actual + data.cantidad

        if nuevo_stock < 0:
            raise HTTPException(
                status_code=400,
                detail=f"El ajuste resultaría en stock negativo ({nuevo_stock})"
            )
        if nuevo_stock < inv.stock_reservado:
            raise HTTPException(
                status_code=400,
                detail="El stock ajustado no puede ser menor al stock reservado"
            )

        stock_antes = inv.stock_actual
        inv.stock_actual = nuevo_stock

        mov = InventoryMovement(
            inventario_id=inv.id,
            tipo=MovementType.AJUSTE,
            cantidad=data.cantidad,
            stock_antes=stock_antes,
            stock_despues=nuevo_stock,
            usuario_id=usuario_id,
            nota=data.nota,
        )
        db.add(mov)
        db.commit()
        db.refresh(inv)
        return inv

    # ─────────────────────────────── MOVEMENTS ────────────────────────────────

    @staticmethod
    def get_movements(
        db: Session,
        sucursal_id: Optional[uuid.UUID] = None,
        tipo: Optional[MovementType] = None,
        fecha_desde=None,
        fecha_hasta=None,
        limit: int = 100,
        offset: int = 0
    ) -> List[InventoryMovement]:
        q = db.query(InventoryMovement)

        if sucursal_id:
            q = q.join(Inventory).filter(Inventory.sucursal_id == sucursal_id)
        if tipo:
            q = q.filter(InventoryMovement.tipo == tipo)
        if fecha_desde:
            q = q.filter(InventoryMovement.created_at >= fecha_desde)
        if fecha_hasta:
            q = q.filter(InventoryMovement.created_at <= fecha_hasta)

        return q.order_by(InventoryMovement.created_at.desc()).offset(offset).limit(limit).all()

    # ─────────────────────────────── ALERTS ───────────────────────────────────

    @staticmethod
    def get_alerts(
        db: Session,
        sucursal_id: Optional[uuid.UUID] = None
    ) -> List[dict]:
        q = db.query(Inventory).filter(Inventory.activo == True)
        if sucursal_id:
            q = q.filter(Inventory.sucursal_id == sucursal_id)

        alerts = []
        for inv in q.all():
            nivel = inv.alerta
            if nivel:
                alerts.append({
                    "inventario_id": inv.id,
                    "sucursal": inv.sucursal.nombre,
                    "variante_sku": inv.variante.sku,
                    "producto_nombre": inv.variante.producto.nombre,
                    "talla": inv.variante.talla,
                    "color": inv.variante.color,
                    "stock_actual": inv.stock_actual,
                    "stock_minimo": inv.stock_minimo,
                    "stock_disponible": inv.stock_disponible,
                    "nivel": nivel,
                })
        return alerts

    # ─────────────────────────────── AVAILABILITY (público) ──────────────────

    @staticmethod
    def get_availability(
        db: Session,
        producto_id: Optional[uuid.UUID] = None,
        sucursal_id: Optional[uuid.UUID] = None,
        categoria_id: Optional[uuid.UUID] = None,
        talla: Optional[str] = None,
        color: Optional[str] = None,
        ciudad: Optional[str] = None,
    ) -> List[dict]:
        from app.models.product import Product

        q = (
            db.query(Inventory)
            .join(Inventory.variante)
            .join(ProductVariant.producto)
            .join(Inventory.sucursal)
            .filter(
                Inventory.activo == True,
                ProductVariant.activo == True,
            )
        )

        if producto_id:
            q = q.filter(ProductVariant.producto_id == producto_id)
        if sucursal_id:
            q = q.filter(Inventory.sucursal_id == sucursal_id)
        if categoria_id:
            q = q.filter(Product.categoria_id == categoria_id)
        if talla:
            q = q.filter(ProductVariant.talla == talla.upper())
        if color:
            q = q.filter(ProductVariant.color.ilike(f"%{color}%"))
        if ciudad:
            q = q.filter(Branch.ciudad.ilike(f"%{ciudad}%"))

        results = []
        for inv in q.all():
            if inv.stock_disponible <= 0:
                continue
            variante = inv.variante
            producto = variante.producto
            sucursal = inv.sucursal
            precio = float(producto.precio_base) + float(variante.precio_extra)
            results.append({
                "producto_id": producto.id,
                "producto_nombre": producto.nombre,
                "talla": variante.talla,
                "color": variante.color,
                "sku": variante.sku,
                "sucursal_id": sucursal.id,
                "sucursal": sucursal.nombre,
                "ciudad": sucursal.ciudad,
                "stock_disponible": inv.stock_disponible,
                "precio": precio,
            })
        return results
