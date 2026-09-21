import uuid
import logging
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.models.notification import Notification

logger = logging.getLogger(__name__)


class NotificationService:
    @staticmethod
    def create(
        db: Session,
        user_id: uuid.UUID,
        title: str,
        message: str,
        type: str = "GENERAL",
        reference_id: Optional[str] = None
    ) -> Optional[Notification]:
        try:
            notif = Notification(
                user_id=user_id,
                title=title,
                message=message,
                type=type,
                reference_id=reference_id,
                is_read=False
            )
            db.add(notif)
            db.commit()
            db.refresh(notif)
            logger.info(f"Notificación creada para usuario {user_id}: {title}")
            return notif
        except Exception as e:
            db.rollback()
            logger.error(f"Error creando notificación para usuario {user_id}: {e}")
            return None

    @staticmethod
    def get_by_user(
        db: Session,
        user_id: uuid.UUID,
        limit: int = 50
    ) -> List[Notification]:
        return (
            db.query(Notification)
            .filter(Notification.user_id == user_id)
            .order_by(desc(Notification.created_at))
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_unread_count(db: Session, user_id: uuid.UUID) -> int:
        return (
            db.query(Notification)
            .filter(Notification.user_id == user_id, Notification.is_read == False)
            .count()
        )

    @staticmethod
    def mark_as_read(
        db: Session,
        notification_id: uuid.UUID,
        user_id: uuid.UUID
    ) -> bool:
        notif = (
            db.query(Notification)
            .filter(Notification.id == notification_id, Notification.user_id == user_id)
            .first()
        )
        if not notif:
            return False
        notif.is_read = True
        db.commit()
        return True

    @staticmethod
    def mark_all_as_read(db: Session, user_id: uuid.UUID) -> int:
        updated = (
            db.query(Notification)
            .filter(Notification.user_id == user_id, Notification.is_read == False)
            .update({Notification.is_read: True})
        )
        db.commit()
        return updated
