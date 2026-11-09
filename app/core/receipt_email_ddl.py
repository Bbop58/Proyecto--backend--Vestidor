def ensure_receipt_email(connection):
    """Explicit additive migration, safe to run repeatedly on populated databases."""
    from app.models.receipt_email_delivery import ReceiptEmailDelivery
    ReceiptEmailDelivery.__table__.create(connection,checkfirst=True)
