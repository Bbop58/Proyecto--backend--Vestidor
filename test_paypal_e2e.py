import urllib.request
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')

print("=== VERIFICACIÓN END-TO-END DE PAYPAL SANDBOX ===")

# 1. Configuración pública
req = urllib.request.Request("http://127.0.0.1:8000/api/v1/payments/paypal/config")
with urllib.request.urlopen(req) as resp:
    cfg = json.loads(resp.read().decode("utf-8"))
    print(f"1. Configuración de Pasarela:")
    print(f"   - Client ID: {cfg['client_id'][:20]}...")
    print(f"   - Mode: {cfg['mode']}")
    print(f"   - Exchange Rate: {cfg['exchange_rate']} Bs. / 1 USD")
    print(f"   - Currency: {cfg['currency']}")

# 2. Login Cajero / Admin
login_req = urllib.request.Request(
    "http://127.0.0.1:8000/api/v1/auth/login",
    data=json.dumps({"email": "admin@ficttstore.com", "password": "Password123!"}).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(login_req) as resp:
    token = json.loads(resp.read().decode("utf-8"))["access_token"]
    print("\n2. Autenticación exitosa (Token JWT obtenido)")

# 3. Crear orden en PayPal (ej: 2 prendas por 280 Bs.)
create_req = urllib.request.Request(
    "http://127.0.0.1:8000/api/v1/payments/paypal/create-order",
    data=json.dumps({
        "monto_bob": 280.0,
        "descripcion": "Venta en mostrador FICCT STORE",
        "items": [
            {"name": "Camisa Oxford Blanca (M/Blanco)", "quantity": 1, "unit_amount_bob": 150.0},
            {"name": "Pantalón Chino Beige (32/Beige)", "quantity": 1, "unit_amount_bob": 130.0}
        ]
    }).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
)
with urllib.request.urlopen(create_req) as resp:
    order_data = json.loads(resp.read().decode("utf-8"))
    print("\n3. Orden de Pago Creada en PayPal Sandbox:")
    print(f"   - Order ID: {order_data['order_id']}")
    print(f"   - Status: {order_data['status']}")
    print(f"   - Total BOB: {order_data['monto_bob']} Bs.")
    print(f"   - Total USD: ${order_data['monto_usd']} USD")
    print(f"   - Approve URL: {order_data['approve_url']}")

print("\n=== TODAS LAS PRUEBAS DE INTEGRACIÓN DE PAYPAL FUERON EXITOSAS ===")
