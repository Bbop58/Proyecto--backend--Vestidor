"""Versioned company backup facade. Full-database recovery uses PostgreSQL tooling."""
from datetime import datetime, timezone
from fastapi import HTTPException
from app.models.tenant import Tenant
from app.models.role import Role
from app.models.user import User
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.sale import Sale
from app.models.user_wallet import UserWallet
from app.models.reservation import Reservation
from app.models.audit_log import AuditLog
from app.services.audit_service import AuditService
from app.services.backup_recovery import export_snapshot, restore_snapshot, serialize as json_serializer


class BackupService:
    @classmethod
    def get_system_stats(cls, db):
        if not db.info.get('tenant_scope'):
            raise HTTPException(403, 'Las estadísticas requieren una sesión de empresa')
        models={'tenants':Tenant,'users':User,'roles':Role,'productos':Product,'inventario':Inventory,
                'ventas':Sale,'billeteras':UserWallet,'reservas':Reservation,'bitacora_logs':AuditLog}
        return {**{name:db.query(model).count() for name,model in models.items()},
                'server_time':datetime.now(timezone.utc).isoformat()}

    @classmethod
    def create_backup(cls, db, current_user=None):
        from app.core.access_policy import resolve_access_scope, AccessScope
        if current_user and resolve_access_scope(current_user) != AccessScope.COMPANY:
            raise HTTPException(403, 'Solo una identidad comercial puede exportar su empresa')
        payload=export_snapshot(db)
        if current_user:
            AuditService.record(db,'Copia de seguridad de empresa generada','Backups',current_user)
        db.commit()
        return payload

    @classmethod
    def restore_backup(cls, db, backup_content, current_user=None):
        return restore_snapshot(db,backup_content,current_user)
