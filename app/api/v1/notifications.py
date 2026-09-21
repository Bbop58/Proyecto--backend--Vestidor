import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.notification import NotificationResponse, UnreadCountResponse
from app.schemas.common import MessageResponse
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["Notificaciones"])


@router.get(
    "",
    response_model=List[NotificationResponse],
    summary="Listar notificaciones del usuario autenticado"
)
def get_my_notifications(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    notifs = NotificationService.get_by_user(db, current_user.id, limit=limit)
    return notifs


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Obtener cantidad de notificaciones no leídas"
)
def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    count = NotificationService.get_unread_count(db, current_user.id)
    return UnreadCountResponse(unread_count=count)


@router.patch(
    "/{notification_id}/read",
    response_model=MessageResponse,
    summary="Marcar notificación como leída"
)
def mark_notification_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    success = NotificationService.mark_as_read(db, notification_id, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notificación no encontrada"
        )
    return MessageResponse(message="Notificación marcada como leída")


@router.post(
    "/mark-all-read",
    response_model=MessageResponse,
    summary="Marcar todas las notificaciones como leídas"
)
def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    count = NotificationService.mark_all_as_read(db, current_user.id)
    return MessageResponse(message=f"{count} notificaciones marcadas como leídas")
