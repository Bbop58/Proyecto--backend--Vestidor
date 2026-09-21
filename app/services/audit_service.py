import logging
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.audit_log import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)


class AuditService:
    @staticmethod
    def log(
        db: Session,
        action: str,
        module: str,
        user: Optional[User] = None,
        user_name: Optional[str] = None,
        user_email: Optional[str] = None,
        user_role: Optional[str] = None
    ) -> Optional[AuditLog]:
        """
        Registra una entrada en la bitácora del sistema de forma segura.
        """
        try:
            u_id = None
            u_name = user_name
            u_email = user_email
            u_role = user_role

            if user:
                u_id = user.id
                if not u_name:
                    u_name = user.full_name
                if not u_email:
                    u_email = user.email
                if not u_role:
                    u_role = user.role.name if user.role else "sin_rol"

            audit_entry = AuditLog(
                user_id=u_id,
                user_name=u_name or "Desconocido",
                user_email=u_email or "N/A",
                user_role=u_role or "N/A",
                action=action,
                module=module
            )
            db.add(audit_entry)
            db.commit()
            db.refresh(audit_entry)
            return audit_entry
        except Exception as e:
            db.rollback()
            logger.error(f"Error al registrar en bitácora: {e}")
            return None

    @staticmethod
    def get_logs(
        db: Session,
        query: Optional[str] = None,
        module: Optional[str] = None,
        role: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[AuditLog]:
        """
        Obtiene los registros de bitácora ordenados del más reciente al más antiguo.
        """
        q = db.query(AuditLog)

        if query:
            search = f"%{query.strip()}%"
            q = q.filter(
                or_(
                    AuditLog.user_name.ilike(search),
                    AuditLog.user_email.ilike(search),
                    AuditLog.action.ilike(search),
                    AuditLog.module.ilike(search)
                )
            )

        if module and module.lower() != "all":
            q = q.filter(AuditLog.module.ilike(module.strip()))

        if role and role.lower() != "all":
            q = q.filter(AuditLog.user_role.ilike(role.strip()))

        return q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()
