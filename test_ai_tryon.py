import sys
import os
import base64
import httpx
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
    print("   TEST DE VESTIDOR VIRTUAL CON IDM-VTON (GRATIS)")
    print("==================================================")

    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.activo == True).first()
        assert product is not None, "No hay productos en la base de datos para probar"
        print(f" -> Producto de prueba: {product.nombre} (ID: {product.id})")

        # Descargar una foto real de una persona para que el modelo identifique pose y cuerpo
        print(" -> Obteniendo imagen de prueba de persona...")
        person_url = "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=600&auto=format&fit=crop&q=80"
        resp = httpx.get(person_url, timeout=10.0)
        person_b64 = base64.b64encode(resp.content).decode("utf-8")

        payload = {
            "producto_id": str(product.id),
            "imagen_cliente_base64": f"data:image/jpeg;base64,{person_b64}"
        }

        print("\n[PASO 1] Enviando petición a POST /api/v1/ai/virtual-tryon...")
        res = client.post("/api/v1/ai/virtual-tryon", json=payload)
        
        print(f" -> Status code recibido: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            assert "imagen_resultado_base64" in data, "La respuesta no contiene imagen_resultado_base64"
            print(" -> ¡Generación de vestidor virtual exitosa (200 OK)!")
            print(f"    * Longitud de imagen base64: {len(data['imagen_resultado_base64'])} caracteres")
            assert data["imagen_resultado_base64"].startswith("data:image/png;base64,"), "El formato no es data URI base64"
        else:
            data = res.json()
            print(f" -> Respuesta: {data}")
            assert False, f"Fallo con status {res.status_code}: {res.text}"

        print("\n==================================================")
        print("   TEST DEL VESTIDOR VIRTUAL COMPLETADO CON ÉXITO")
        print("==================================================")

    finally:
        db.close()

if __name__ == "__main__":
    test_ai_virtual_tryon()
