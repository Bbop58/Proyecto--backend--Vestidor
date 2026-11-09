"""Add receipt delivery metadata only; never reset or seed the database."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.database import engine
from app.core.receipt_email_ddl import ensure_receipt_email

if __name__=='__main__':
    with engine.begin() as connection:ensure_receipt_email(connection)
    print('Receipt email metadata schema ready')
