import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import desc, asc

from app.models.sale import Sale
from app.models.inventory import Inventory
from app.models.user import User
from app.models.user_wallet import UserWallet
from app.models.reservation import Reservation
from app.models.branch import Branch
from app.models.category import Category
from app.models.product import Product

logger = logging.getLogger("custom_report_service")


class CustomReportService:

    @classmethod
    def get_available_entities(cls) -> List[Dict[str, Any]]:
        """Retorna las entidades soportadas y sus columnas seleccionables para el constructor visual."""
        return [
            {
                "id": "ventas",
                "name": "Ventas y Facturación",
                "description": "Transacciones comerciales, cobros en mostrador y compras con billetera/PayPal",
                "columns": [
                    {"id": "numero_recibo", "label": "Nº Recibo / Nota", "type": "string"},
                    {"id": "fecha", "label": "Fecha y Hora", "type": "datetime"},
                    {"id": "cliente_nombre", "label": "Cliente", "type": "string"},
                    {"id": "sucursal_nombre", "label": "Sucursal", "type": "string"},
                    {"id": "cajero_nombre", "label": "Cajero / Operador", "type": "string"},
                    {"id": "metodo_pago", "label": "Método de Pago", "type": "string"},
                    {"id": "monto_total", "label": "Monto Total (Bs.)", "type": "currency"},
                    {"id": "impuesto_iva", "label": "Impuesto IVA (Bs.)", "type": "currency"},
                    {"id": "monto_neto", "label": "Monto Neto (Bs.)", "type": "currency"},
                    {"id": "estado", "label": "Estado de Venta", "type": "string"}
                ],
                "sort_options": [
                    {"id": "fecha", "label": "Fecha"},
                    {"id": "monto_total", "label": "Monto Total"},
                    {"id": "numero_recibo", "label": "Número de Recibo"}
                ]
            },
            {
                "id": "inventario",
                "name": "Inventario y Stock por Sucursal",
                "description": "Existencias por prenda, SKU, variante de color y talla",
                "columns": [
                    {"id": "producto_nombre", "label": "Prenda / Producto", "type": "string"},
                    {"id": "sku", "label": "Código SKU", "type": "string"},
                    {"id": "categoria_nombre", "label": "Categoría", "type": "string"},
                    {"id": "talla", "label": "Talla", "type": "string"},
                    {"id": "color", "label": "Color", "type": "string"},
                    {"id": "sucursal_nombre", "label": "Sucursal", "type": "string"},
                    {"id": "stock_actual", "label": "Stock Físico", "type": "number"},
                    {"id": "stock_minimo", "label": "Stock Mínimo", "type": "number"},
                    {"id": "precio_base", "label": "Precio Unitario (Bs.)", "type": "currency"}
                ],
                "sort_options": [
                    {"id": "producto_nombre", "label": "Nombre de Producto"},
                    {"id": "stock_actual", "label": "Stock Actual"},
                    {"id": "precio_base", "label": "Precio Base"}
                ]
            },
            {
                "id": "clientes",
                "name": "Clientes y Billeteras Virtuales",
                "description": "Cuentas de clientes con saldos prepago y estado de cuenta",
                "columns": [
                    {"id": "full_name", "label": "Nombre del Cliente", "type": "string"},
                    {"id": "email", "label": "Correo Electrónico", "type": "string"},
                    {"id": "saldo_billetera", "label": "Saldo en Billetera (Bs.)", "type": "currency"},
                    {"id": "is_active", "label": "Cuenta Activa", "type": "boolean"},
                    {"id": "created_at", "label": "Fecha de Registro", "type": "datetime"}
                ],
                "sort_options": [
                    {"id": "full_name", "label": "Nombre Completo"},
                    {"id": "saldo_billetera", "label": "Saldo de Billetera"},
                    {"id": "created_at", "label": "Fecha de Registro"}
                ]
            },
            {
                "id": "reservas",
                "name": "Reservas Click & Collect",
                "description": "Prendas apartadas para recojo en tienda física dentro de las 48 horas",
                "columns": [
                    {"id": "codigo_reserva", "label": "Código Reserva", "type": "string"},
                    {"id": "cliente_nombre", "label": "Cliente", "type": "string"},
                    {"id": "sucursal_nombre", "label": "Sucursal Recojo", "type": "string"},
                    {"id": "fecha_creacion", "label": "Fecha Solicitud", "type": "datetime"},
                    {"id": "fecha_limite", "label": "Vencimiento (48 hrs)", "type": "datetime"},
                    {"id": "monto_total", "label": "Monto Estimado (Bs.)", "type": "currency"},
                    {"id": "estado", "label": "Estado de Reserva", "type": "string"}
                ],
                "sort_options": [
                    {"id": "fecha_creacion", "label": "Fecha de Creación"},
                    {"id": "monto_total", "label": "Monto"},
                    {"id": "codigo_reserva", "label": "Código"}
                ]
            }
        ]

    @classmethod
    def execute_custom_report(
        cls,
        db: Session,
        entity: str,
        columns: List[str],
        filters: Dict[str, Any],
        sort_by: str = "fecha",
        sort_order: str = "desc",
        tenant_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """Ejecuta la consulta estructurada multi-columna con filtrado y ordenamiento dinámico."""
        
        entities_meta = {e["id"]: e for e in cls.get_available_entities()}
        if entity not in entities_meta:
            entity = "ventas"

        selected_meta = entities_meta[entity]
        all_cols_dict = {c["id"]: c for c in selected_meta["columns"]}
        
        # Si no enviaron columnas, seleccionar las 5 primeras por defecto
        valid_columns = [col for col in columns if col in all_cols_dict]
        if not valid_columns:
            valid_columns = [c["id"] for c in selected_meta["columns"][:5]]

        rows = []
        totals = {}

        if entity == "ventas":
            query = db.query(Sale)
            if tenant_id:
                query = query.filter(Sale.tenant_id == tenant_id)

            # Filtros
            if filters.get("fecha_desde"):
                try:
                    f_d = datetime.fromisoformat(filters["fecha_desde"])
                    query = query.filter(Sale.created_at >= f_d)
                except Exception:
                    pass
            if filters.get("fecha_hasta"):
                try:
                    f_h = datetime.fromisoformat(filters["fecha_hasta"])
                    query = query.filter(Sale.created_at <= f_h)
                except Exception:
                    pass
            if filters.get("sucursal_id"):
                query = query.filter(Sale.sucursal_id == UUID(str(filters["sucursal_id"])))
            if filters.get("metodo_pago"):
                query = query.filter(Sale.metodo_pago == filters["metodo_pago"])
            if filters.get("monto_min"):
                query = query.filter(Sale.monto_total >= float(filters["monto_min"]))
            if filters.get("monto_max"):
                query = query.filter(Sale.monto_total <= float(filters["monto_max"]))

            # Orden
            if sort_by == "monto_total":
                query = query.order_by(desc(Sale.monto_total) if sort_order == "desc" else asc(Sale.monto_total))
            elif sort_by == "numero_recibo":
                query = query.order_by(desc(Sale.numero_recibo) if sort_order == "desc" else asc(Sale.numero_recibo))
            else:
                query = query.order_by(desc(Sale.created_at) if sort_order == "desc" else asc(Sale.created_at))

            results = query.limit(200).all()
            total_monto = 0.0

            for s in results:
                row = {}
                for col in valid_columns:
                    if col == "numero_recibo":
                        row[col] = s.numero_recibo
                    elif col == "fecha":
                        row[col] = s.created_at.strftime("%d/%m/%Y %H:%M") if s.created_at else ""
                    elif col == "cliente_nombre":
                        row[col] = s.cliente.full_name if s.cliente else "Cliente Casual"
                    elif col == "cajero_nombre":
                        row[col] = s.cajero.full_name if s.cajero else "Sistema"
                    elif col == "sucursal_nombre":
                        row[col] = s.sucursal.nombre if s.sucursal else "Central"
                    elif col == "metodo_pago":
                        row[col] = s.metodo_pago.value if hasattr(s.metodo_pago, "value") else str(s.metodo_pago)
                    elif col == "monto_total":
                        val = float(s.monto_total)
                        row[col] = val
                        total_monto += val
                    elif col == "impuesto_iva":
                        row[col] = float(s.impuesto_iva or 0.0)
                    elif col == "monto_neto":
                        row[col] = float(s.monto_neto or 0.0)
                    elif col == "estado":
                        row[col] = s.estado.value if hasattr(s.estado, "value") else str(s.estado)
                rows.append(row)

            totals["monto_total"] = round(total_monto, 2)

        elif entity == "inventario":
            query = db.query(Inventory)
            if tenant_id:
                query = query.filter(Inventory.tenant_id == tenant_id)

            if filters.get("sucursal_id"):
                query = query.filter(Inventory.sucursal_id == UUID(str(filters["sucursal_id"])))

            results = query.limit(200).all()
            for inv in results:
                row = {}
                prod = inv.variante.producto if inv.variante else None
                for col in valid_columns:
                    if col == "producto_nombre":
                        row[col] = prod.nombre if prod else "N/A"
                    elif col == "sku":
                        row[col] = inv.variante.sku if inv.variante else "N/A"
                    elif col == "categoria_nombre":
                        row[col] = prod.categoria.nombre if (prod and prod.categoria) else "General"
                    elif col == "talla":
                        row[col] = inv.variante.talla if inv.variante else "Única"
                    elif col == "color":
                        row[col] = inv.variante.color if inv.variante else "Estándar"
                    elif col == "sucursal_nombre":
                        row[col] = inv.sucursal.nombre if inv.sucursal else "Central"
                    elif col == "stock_actual":
                        row[col] = inv.stock_actual
                    elif col == "stock_minimo":
                        row[col] = inv.stock_minimo
                    elif col == "precio_base":
                        row[col] = float(prod.precio_base) if prod else 0.0
                rows.append(row)

        elif entity == "clientes":
            query = db.query(User)
            if tenant_id:
                query = query.filter(User.tenant_id == tenant_id)

            results = query.limit(200).all()
            for u in results:
                row = {}
                wallet = db.query(UserWallet).filter(UserWallet.user_id == u.id).first()
                saldo = float(wallet.saldo) if wallet else 0.0
                for col in valid_columns:
                    if col == "full_name":
                        row[col] = u.full_name
                    elif col == "email":
                        row[col] = u.email
                    elif col == "saldo_billetera":
                        row[col] = saldo
                    elif col == "is_active":
                        row[col] = u.is_active
                    elif col == "created_at":
                        row[col] = u.created_at.strftime("%d/%m/%Y") if u.created_at else ""
                rows.append(row)

        elif entity == "reservas":
            query = db.query(Reservation)
            if tenant_id:
                query = query.filter(Reservation.tenant_id == tenant_id)

            results = query.limit(200).all()
            for res in results:
                row = {}
                for col in valid_columns:
                    if col == "codigo_reserva":
                        row[col] = res.codigo_reserva
                    elif col == "cliente_nombre":
                        row[col] = res.usuario.full_name if res.usuario else "Cliente"
                    elif col == "sucursal_nombre":
                        row[col] = res.sucursal.nombre if res.sucursal else "Central"
                    elif col == "fecha_creacion":
                        row[col] = res.created_at.strftime("%d/%m/%Y %H:%M") if res.created_at else ""
                    elif col == "fecha_limite":
                        row[col] = res.fecha_limite.strftime("%d/%m/%Y %H:%M") if res.fecha_limite else ""
                    elif col == "monto_total":
                        row[col] = float(res.monto_total) if res.monto_total else 0.0
                    elif col == "estado":
                        row[col] = res.estado.value if hasattr(res.estado, "value") else str(res.estado)
                rows.append(row)

        return {
            "entity": entity,
            "entity_name": selected_meta["name"],
            "columns": [{"id": c_id, "label": all_cols_dict[c_id]["label"], "type": all_cols_dict[c_id]["type"]} for c_id in valid_columns],
            "total_rows": len(rows),
            "rows": rows,
            "totals": totals,
            "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        }

