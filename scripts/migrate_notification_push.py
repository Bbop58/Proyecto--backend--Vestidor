"""Explicit, additive migration; never resets commercial data."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.database import engine
from app.core.notification_push_ddl import ensure_notification_push
if __name__=='__main__':
    with engine.begin() as connection:ensure_notification_push(connection)
    print('Notification device/delivery schema ready')
