import hashlib
import json
import logging
from datetime import datetime, timezone, date
from typing import Dict, Any, List, Optional
from uuid import UUID
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import HTTPException, status

from app.models.tenant import Tenant
from app.models.role import Role
from app.models.permission import Permission
from app.models.user import User
from app.models.branch import Branch
from app.models.category import Category
from app.models.season import Season
from app.models.supplier import Supplier
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.inventory import Inventory
from app.models.sale import Sale
from app.models.sale_detail import SaleDetail
from app.models.user_wallet import UserWallet
from app.models.wallet_transaction import WalletTransaction
from app.models.reservation import Reservation
from app.models.delivery_rate import DeliveryRate
from app.models.audit_log import AuditLog
from app.services.audit_service import AuditService

logger = logging.getLogger("backup_service")


def json_serializer(obj):
    if isinstance(obj, (datetime,date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


class BackupService:

    @classmethod
    def get_system_stats(cls, db: Session) -> Dict[str, Any]:
        """Retorna estadísticas generales de las tablas del sistema para el panel de respaldo."""
        stats = {
            "tenants": db.query(Tenant).count(),
            "users": db.query(User).count(),
            "roles": db.query(Role).count(),
            "productos": db.query(Product).count(),
            "inventario": db.query(Inventory).count(),
            "ventas": db.query(Sale).count(),
            "billeteras": db.query(UserWallet).count(),
            "reservas": db.query(Reservation).count(),
            "bitacora_logs": db.query(AuditLog).count(),
            "server_time": datetime.now(timezone.utc).isoformat()
        }
        return stats

    @classmethod
    def create_backup(cls, db: Session, current_user: Optional[User] = None) -> Dict[str, Any]:
        """
        Genera un snapshot completo consistente y serializado de todas las entidades del sistema
        con sellado criptográfico SHA-256 de verificación.
        """
        from app.core.tenant_scope import predicates
        from app.database import Base
        tenant_id = db.info.get("tenant_scope")
        if not tenant_id:
            raise HTTPException(403,"El respaldo requiere una sesión de empresa")
        aliases = {"variantes_producto":"variantes", "user_wallets":"billeteras"}
        data = {}
        for model in predicates(tenant_id):
            rows = db.query(model).all()
            key = aliases.get(model.__tablename__,model.__tablename__)
            data[key] = [{column.key:getattr(row,column.key) for column in model.__table__.columns} for row in rows]
        payload = {"system":"FICCT STORE SaaS Enterprise", "version":"3.0.0", "scope":"tenant",
                   "tenant_id":str(tenant_id), "created_at":datetime.now(timezone.utc).isoformat(), "data":data}
        payload["checksum_sha256"] = hashlib.sha256(json.dumps(data,default=json_serializer,sort_keys=True).encode()).hexdigest()
        payload["total_records"] = sum(len(rows) for rows in data.values())
        AuditService.log(db,action="Copia de seguridad de empresa generada",module="Backups",user=current_user)
        return payload

    @classmethod
    def restore_backup(cls, db: Session, backup_content: Dict[str, Any], current_user: Optional[User] = None) -> Dict[str, Any]:
        """
        Restaura el sistema a partir de un archivo JSON de respaldo.
        Valida integridad criptográfica y restaura tablas en transacción atómica.
        """
        if not isinstance(backup_content, dict) or "data" not in backup_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Estructura de archivo de respaldo no válida. Debe contener la clave 'data'."
            )

        data = backup_content.get("data", {})
        data_string = json.dumps(data, default=json_serializer, sort_keys=True)
        calculated_checksum = hashlib.sha256(data_string.encode("utf-8")).hexdigest()
        provided_checksum = backup_content.get("checksum_sha256")

        if provided_checksum and provided_checksum != calculated_checksum:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Integridad de respaldo comprometida: El Checksum SHA-256 no coincide con los datos del archivo."
            )

        logger.info(f"Iniciando Restauración del Sistema por {current_user.email if current_user else 'Admin'}...")

        # Ejecutar en transacción atómica
        restored_counts = {}
        try:
            # 1. Tenants (Upsert)
            for t_item in data.get("tenants", []):
                t_id = UUID(t_item["id"])
                t = db.query(Tenant).filter(Tenant.id == t_id).first()
                if not t:
                    t = Tenant(id=t_id, nombre=t_item["nombre"], slug=t_item["slug"])
                    db.add(t)
                t.nombre = t_item.get("nombre", t.nombre)
                t.slug = t_item.get("slug", t.slug)
                t.color_primario = t_item.get("color_primario", t.color_primario)
                t.color_secundario = t_item.get("color_secundario", t.color_secundario)
                t.eslogan = t_item.get("eslogan", t.eslogan)
                t.logo_url = t_item.get("logo_url", t.logo_url)
                t.activo = t_item.get("activo", True)
            db.flush()
            restored_counts["tenants"] = len(data.get("tenants", []))

            # 2. Categorías
            for c_item in data.get("categorias", []):
                c_id = UUID(c_item["id"])
                c = db.query(Category).filter(Category.id == c_id).first()
                if not c:
                    c = Category(id=c_id, nombre=c_item["nombre"])
                    db.add(c)
                c.nombre = c_item.get("nombre", c.nombre)
                c.descripcion = c_item.get("descripcion", c.descripcion)
                c.activa = c_item.get("activa", c_item.get("activo", True))
            db.flush()
            restored_counts["categorias"] = len(data.get("categorias", []))

            # 3. Sucursales
            for b_item in data.get("sucursales", []):
                b_id = UUID(b_item["id"])
                b = db.query(Branch).filter(Branch.id == b_id).first()
                if not b:
                    b = Branch(id=b_id, nombre=b_item["nombre"], ciudad=b_item.get("ciudad", "Tarija"), direccion=b_item.get("direccion", "Centro"))
                    db.add(b)
                b.nombre = b_item.get("nombre", b.nombre)
                b.ciudad = b_item.get("ciudad", b.ciudad)
                b.direccion = b_item.get("direccion", b.direccion)
                b.activa = b_item.get("activa", True)
            db.flush()
            restored_counts["sucursales"] = len(data.get("sucursales", []))

            # 4. Productos
            for p_item in data.get("productos", []):
                p_id = UUID(p_item["id"])
                p = db.query(Product).filter(Product.id == p_id).first()
                if not p:
                    p = Product(id=p_id, nombre=p_item["nombre"], precio_base=Decimal(str(p_item["precio_base"])))
                    db.add(p)
                p.nombre = p_item.get("nombre", p.nombre)
                p.descripcion = p_item.get("descripcion", p.descripcion)
                p.precio_base = Decimal(str(p_item.get("precio_base", 0.0)))
                p.temporada = p_item.get("temporada", p.temporada)
                p.activo = p_item.get("activo", True)
            db.flush()
            restored_counts["productos"] = len(data.get("productos", []))

            # 5. Billeteras (Restauración de balances y firmas HMAC)
            for w_item in data.get("billeteras", []):
                w_id = UUID(w_item["id"])
                w = db.query(UserWallet).filter(UserWallet.id == w_id).first()
                if not w:
                    w = UserWallet(id=w_id, user_id=UUID(w_item["user_id"]))
                    db.add(w)
                w.saldo = Decimal(str(w_item.get("saldo", 0.0)))
                w.firma_integridad = w_item.get("firma_integridad")
                w.nonce = w_item.get("nonce", 0)
                if hasattr(w, "moneda"):
                    w.moneda = w_item.get("moneda", "BOB")
            db.flush()
            restored_counts["billeteras"] = len(data.get("billeteras", []))

            db.commit()

            AuditService.log(
                db=db,
                action=f"Restauración completa del sistema ejecutada con éxito ({sum(restored_counts.values())} entidades procesadas)",
                module="Backups",
                user=current_user
            )

            return {
                "success": True,
                "message": "Sistema restaurado con éxito a partir de la copia de seguridad.",
                "restored_entities": restored_counts,
                "restored_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            db.rollback()
            logger.error(f"Error restaurando base de datos: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error durante el proceso de restauración: {str(e)}"
            )

