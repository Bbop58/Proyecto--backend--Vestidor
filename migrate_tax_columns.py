import sys
import os

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from sqlalchemy import text
from app.database import engine

def migrate():
    print("Iniciando migración de columnas impuesto_iva y monto_neto en la tabla ventas...")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE ventas ADD COLUMN IF NOT EXISTS impuesto_iva NUMERIC(10, 2) DEFAULT 0.0;"))
        conn.execute(text("ALTER TABLE ventas ADD COLUMN IF NOT EXISTS monto_neto NUMERIC(10, 2) DEFAULT 0.0;"))
        result = conn.execute(text("""
            UPDATE ventas
            SET impuesto_iva = ROUND(monto_total * 0.13, 2),
                monto_neto = monto_total - ROUND(monto_total * 0.13, 2)
            WHERE (impuesto_iva = 0 OR impuesto_iva IS NULL) AND monto_total > 0;
        """))
        print(f"Migración completada con éxito. Filas actualizadas: {result.rowcount}")

if __name__ == "__main__":
    migrate()
