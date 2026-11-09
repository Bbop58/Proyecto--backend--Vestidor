def ensure_notification_push(connection):
    from app.models.notification_push import NotificationDevice,NotificationPushDelivery
    NotificationDevice.__table__.create(connection,checkfirst=True)
    NotificationPushDelivery.__table__.create(connection,checkfirst=True)
    from sqlalchemy import text
    connection.execute(text("ALTER TABLE notification_devices ADD COLUMN IF NOT EXISTS credential_hash VARCHAR(64) NOT NULL DEFAULT 'revoked-before-migration'"))
    connection.execute(text("ALTER TABLE notification_devices ADD COLUMN IF NOT EXISTS session_expires_at TIMESTAMPTZ"))
