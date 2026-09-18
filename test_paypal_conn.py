import sys
sys.stdout.reconfigure(encoding='utf-8')
from app.services.paypal_service import PayPalService

print("=== PRUEBA DE CONECTIVIDAD PAYPAL SANDBOX ===")

try:
    print("1. Solicitando Access Token OAuth2 a PayPal...")
    token = PayPalService.get_access_token()
    print(f"   [OK] Token obtenido exitosamente: {token[:20]}... (longitud: {len(token)})")

    print("\n2. Probando creación de orden PayPal de prueba (100 BOB -> USD)...")
    order = PayPalService.create_order(
        monto_bob=100.0,
        descripcion="Prueba automatizada de conexion FICCT STORE"
    )
    print("   [OK] Orden creada exitosamente en PayPal:")
    print(f"        Order ID: {order['order_id']}")
    print(f"        Status: {order['status']}")
    print(f"        Monto BOB: {order['monto_bob']} Bs.")
    print(f"        Monto USD: ${order['monto_usd']} USD (Tasa: {order['exchange_rate']})")
    print(f"        Approve URL: {order['approve_url']}")

    print("\n3. Consultando detalle de la orden recién creada...")
    details = PayPalService.get_order_details(order['order_id'])
    print(f"   [OK] Detalle obtenido: Status = {details.get('status')}, Intent = {details.get('intent')}")

    print("\n=== PAYPAL SANDBOX ESTÁ 100% OPERATIVO Y CONECTADO ===")
except Exception as e:
    print(f"\n[ERROR] Fallo en la prueba de PayPal: {e}")
