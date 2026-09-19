import sys
import os
import base64
from fastapi.testclient import TestClient

# UTF-8 stdout
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from app.main import app
from app.database import SessionLocal
from app.models.product_variant import ProductVariant

client = TestClient(app)

def test_ai_virtual_tryon():
    print("==================================================")
    print("   TEST DE VESTIDOR VIRTUAL CON IA (BACKEND)")
    print("==================================================")

    db = SessionLocal()
    try:
        # Buscar una variante cualquiera de Polera
        variant = db.query(ProductVariant).first()
        assert variant is not None, "No hay variantes en la base de datos para probar"
        print(f" -> Variante de prueba: {variant.sku} (Talla: {variant.talla}, Color: {variant.color})")

        # Crear una imagen base64 de prueba (1x1 pixel PNG)
        sample_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

        payload = {
            "variante_id": str(variant.id),
            "imagen_cliente_base64": f"data:image/png;base64,{sample_png}",
            "altura_cm": 178,
            "peso_kg": 74,
            "preferencia_calce": "OVERSIZE"
        }

        print("\n[PASO 1] Enviando petición a POST /api/v1/ai/virtual-tryon...")
        res = client.post("/api/v1/ai/virtual-tryon", json=payload)
        assert res.status_code == 200, f"Error en endpoint AI Tryon: {res.text}"

        data = res.json()
        print(" -> Respuesta exitosa (200 OK):")
        print(f"    * Talla sugerida: {data['talla_sugerida']}")
        print(f"    * Calce detectado: {data['calce_detectado']}")
        print(f"    * Nivel de coincidencia: {data['nivel_coincidencia_porcentaje']}%")
        print(f"    * Análisis de silueta: {data['analisis_silueta'][:80]}...")
        print(f"    * Consejo de estilo: {data['consejo_estilo'][:80]}...")
        print(f"    * Combinaciones sugeridas ({len(data['combinaciones_sugeridas'])}):")
        for c in data['combinaciones_sugeridas']:
            print(f"      - {c['nombre']} ({c['categoria']}): {c['motivo'][:50]}...")

        print("\n==================================================")
        print("   TEST DEL VESTIDOR VIRTUAL IA 100% EXITOSO!")
        print("==================================================")

    finally:
        db.close()

if __name__ == "__main__":
    test_ai_virtual_tryon()
