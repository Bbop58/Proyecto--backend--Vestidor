from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TYPE payment_method ADD VALUE IF NOT EXISTS 'PAYPAL'"))
        conn.commit()
        print("Enum payment_method successfully updated with 'PAYPAL'")
    except Exception as e:
        print("Note on enum update:", e)
