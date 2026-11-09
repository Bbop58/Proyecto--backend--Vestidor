"""Small database-backed WebSocket monitor; reconnect recovers through REST."""
import hashlib
from sqlalchemy import func
from app.models.notification import Notification
from app.api.deps import get_current_token_payload, get_current_user


def snapshot(db, token):
    payload = get_current_token_payload(token=token, db=db)
    user = get_current_user(db=db, payload=payload, request=None)
    rows = (db.query(Notification.id, Notification.is_read)
        .filter(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(50).all())
    unread = db.query(func.count(Notification.id)).filter(
        Notification.user_id == user.id, Notification.is_read.is_(False)).scalar()
    revision = hashlib.sha256(repr((unread, [(str(r.id), r.is_read) for r in rows])).encode()).hexdigest()
    return {'type': 'notifications.changed', 'latest_id': str(rows[0].id) if rows else None,
        'unread_count': unread, 'revision': revision}
