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
from app.models.product import Product

client = TestClient(app)

def test_ai_virtual_tryon():
    print("==================================================")
    print("   TEST DE VESTIDOR VIRTUAL CON GEMINI (BACKEND)")
    print("==================================================")

    db = SessionLocal()
    try:
        product = db.query(Product).first()
        assert product is not None, "No hay productos en la base de datos para probar"
        print(f" -> Producto de prueba: {product.nombre} (ID: {product.id})")

        # Imagen base64 de prueba (1x1 pixel PNG)
        sample_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

        payload = {
            "producto_id": str(product.id),
            "imagen_cliente_base64": f"data:image/png;base64,{sample_png}"
        }

        print("\n[PASO 1] Enviando petición simplificada a POST /api/v1/ai/virtual-tryon...")
        res = client.post("/api/v1/ai/virtual-tryon", json=payload)
        
        print(f" -> Status code recibido: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            assert "imagen_resultado_base64" in data, "La respuesta no contiene imagen_resultado_base64"
            print(" -> ¡Generación exitosa con Gemini (200 OK)!")
            print(f"    * Longitud de imagen generada: {len(data['imagen_resultado_base64'])} caracteres")
        elif res.status_code in (500, 503):
            # Error controlado cuando no hay cuota de imágenes en el plan de la API
            data = res.json()
            print(f" -> Error controlado en español: {data.get('detail')}")
            assert "No se pudo generar la imagen del vestidor virtual" in data.get("detail", ""), "El mensaje de error no es el esperado"
            print(" -> Manejo de error de cuota/API verificado con éxito.")
        else:
            raise AssertionError(f"Status inesperado: {res.status_code} - {res.text}")

        print("\n==================================================")
        print("   TEST DEL VESTIDOR VIRTUAL COMPLETADO CON ÉXITO")
        print("==================================================")

    finally:
        db.close()

if __name__ == "__main__":
    test_ai_virtual_tryon()
