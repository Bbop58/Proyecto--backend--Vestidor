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
import asyncio
import json
from fastapi import WebSocket, WebSocketDisconnect
from app.config import settings
from app.services.notification_realtime import snapshot
from app.api.deps import get_current_token_payload
from app.schemas.notification_push import DeviceRegistration, DeviceRevocation
from app.services import notification_push_service as push

router = APIRouter(prefix="/notifications", tags=["Notificaciones"])


@router.put('/push/devices')
def register_push_device(payload: DeviceRegistration, db: Session = Depends(get_db),
    user: User = Depends(get_current_user), claims: dict = Depends(get_current_token_payload)):
    if not settings.NOTIFICATION_PUSH_ENABLED or not settings.FIREBASE_ADMIN_JSON:
        raise HTTPException(503,'Los avisos del sistema no están configurados')
    return push.register(db,user,payload,claims)


@router.post('/push/unregister')
def unregister_push_device(payload: DeviceRevocation, db: Session = Depends(get_db)):
    return push.unregister(db,payload)


@router.websocket('/ws')
async def notifications_socket(websocket: WebSocket, db: Session = Depends(get_db)):
    origin = websocket.headers.get('origin')
    if origin and origin not in [str(o) for o in settings.BACKEND_CORS_ORIGINS]:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        if len(raw) > 8192:
            await websocket.close(code=4401); return
        frame = json.loads(raw)
        token = frame.get('token') if isinstance(frame, dict) else None
        if not isinstance(token, str) or not token:
            await websocket.close(code=4401); return
        previous = None
        while True:
            # Never keep a read transaction/snapshot open while waiting for the client.
            try:
                state = snapshot(db, token)
            finally:
                db.rollback()
            if state['revision'] != previous:
                await websocket.send_json(state)
                previous = state['revision']
            try:
                incoming = await asyncio.wait_for(websocket.receive_text(), timeout=1)
                if len(incoming) > 8192:
                    await websocket.close(code=4401); return
                frame = json.loads(incoming)
                refreshed = frame.get('token') if isinstance(frame, dict) else None
                if isinstance(refreshed, str) and refreshed:
                    token = refreshed
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    except HTTPException as error:
        await websocket.close(code=4403 if error.status_code == 403 else 4401)
    except (ValueError, TypeError, asyncio.TimeoutError):
        await websocket.close(code=4401)


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
