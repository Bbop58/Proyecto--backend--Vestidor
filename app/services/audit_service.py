import logging
from typing import List, Optional, Any
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
        user_role: Optional[str] = None,
        user_id: Optional[Any] = None,
        tenant_id: Optional[Any] = None,
        details: Optional[Any] = None,
        request: Optional[Any] = None,
        *args,
        **kwargs
    ) -> Optional[AuditLog]:
        """
        Registra una entrada en la bitácora del sistema de forma segura.
        """
        try:
            u_id = user_id
            t_id = tenant_id
            u_name = user_name
            u_email = user_email
            u_role = user_role

            if user:
                u_id = user.id
                t_id = getattr(user, "tenant_id", t_id)
                if not u_name:
                    u_name = user.full_name
                if not u_email:
                    u_email = user.email
                if not u_role:
                    u_role = user.role.name if getattr(user, "role", None) else "sin_rol"

            action_text = action
            if details:
                action_text = f"{action}: {details}"[:100]

            # Extraer dirección IP real del cliente
            client_ip = kwargs.get("ip_address")
            if not client_ip and request:
                forwarded = getattr(request, "headers", {}).get("x-forwarded-for") if hasattr(request, "headers") else None
                if forwarded:
                    client_ip = forwarded.split(",")[0].strip()
                elif getattr(request, "client", None):
                    client_ip = request.client.host
            if not client_ip:
                client_ip = "127.0.0.1"

            audit_entry = AuditLog(
                tenant_id=t_id,
                user_id=u_id,
                user_name=u_name or "Desconocido",
                user_email=u_email or "N/A",
                user_role=u_role or "N/A",
                action=action_text,
                module=module,
                ip_address=client_ip
            )
            db.add(audit_entry)
            db.commit()
            db.refresh(audit_entry)

            # Registro concurrente en archivo físico confidencial audit_secure.log
            try:
                import os
                base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
                log_dir = os.path.join(base_dir, "logs")
                os.makedirs(log_dir, exist_ok=True)
                log_file = os.path.join(log_dir, "audit_secure.log")
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(
                        f"[{audit_entry.created_at.isoformat()}] IP: {client_ip} | "
                        f"USER: {audit_entry.user_email} ({audit_entry.user_role}) | "
                        f"MODULE: {audit_entry.module} | ACTION: {audit_entry.action}\n"
                    )
            except Exception as file_err:
                logger.warning(f"No se pudo escribir en audit_secure.log: {file_err}")

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


audit_service = AuditService()

