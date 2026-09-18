import urllib.request
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')

# 1. Test GET /payments/paypal/config
req = urllib.request.Request("http://127.0.0.1:8000/api/v1/payments/paypal/config")
with urllib.request.urlopen(req) as resp:
    print("Config response:", resp.read().decode("utf-8"))

# 2. Test Login Admin to get token
login_req = urllib.request.Request(
    "http://127.0.0.1:8000/api/v1/auth/login",
    data=json.dumps({"email": "admin@ficttstore.com", "password": "Password123!"}).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(login_req) as resp:
    token = json.loads(resp.read().decode("utf-8"))["access_token"]
    print("Logged in, token obtained")

# 3. Test POST /payments/paypal/create-order
create_req = urllib.request.Request(
    "http://127.0.0.1:8000/api/v1/payments/paypal/create-order",
    data=json.dumps({
        "monto_bob": 150.0,
        "descripcion": "Camisa Blanca Talla M en POS"
    }).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
)
with urllib.request.urlopen(create_req) as resp:
    print("Create Order response:", resp.read().decode("utf-8"))
