import urllib.request
import urllib.error
import json
import sys

# Force UTF-8 stdout
sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:8000/api/v1"

USERS = [
    ("Admin", "admin@ficttstore.com", "Password123!"),
    ("Encargado", "encargado@ficttstore.com", "Password123!"),
    ("Cajero", "cajero@ficttstore.com", "Password123!"),
    ("Cliente", "cliente@ficttstore.com", "Password123!")
]

def make_req(url, method="GET", headers=None, body=None):
    if headers is None:
        headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            resp_body = resp.read().decode("utf-8")
            return resp.status, json.loads(resp_body) if resp_body else {}
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8")
        try:
            parsed = json.loads(resp_body)
        except Exception:
            parsed = resp_body
        return e.code, parsed
    except Exception as e:
        return 500, str(e)

print("=== VERIFICACION DE ROLES Y PERMISOS ===")

tokens = {}
users_info = {}

for name, email, password in USERS:
    status, data = make_req(f"{BASE_URL}/auth/login", method="POST", body={"email": email, "password": password})
    if status == 200:
        tokens[name] = data["access_token"]
        users_info[name] = data["user"]
        perms = data["user"]["permissions"]
        print(f"\n[OK] Login {name} ({email}) - Rol: {data['user']['role']['name']} - {len(perms)} permisos asignados")
        print(f"     Permisos: {sorted(perms)}")
    else:
        print(f"\n[ERROR] Login {name} fallo: {status} {data}")

print("\n=== PROBANDO ACCESOS POR ENDPOINT SEGUN ROL ===")

tests = [
    ("GET /roles", "GET", "/roles", ["Admin"], ["Encargado", "Cajero", "Cliente"]),
    ("GET /users", "GET", "/users", ["Admin"], ["Encargado", "Cajero", "Cliente"]),
    ("GET /sucursales", "GET", "/sucursales", ["Admin", "Encargado", "Cajero", "Cliente"], []),
    ("POST /sucursales", "POST", "/sucursales", ["Admin"], ["Encargado", "Cajero", "Cliente"]),
    ("GET /categorias", "GET", "/categorias", ["Admin", "Encargado", "Cajero", "Cliente"], []),
    ("GET /productos", "GET", "/productos", ["Admin", "Encargado", "Cajero", "Cliente"], []),
    ("POST /productos", "POST", "/productos", ["Admin"], ["Cajero", "Cliente"]),
    ("GET /proveedores", "GET", "/proveedores", ["Admin", "Encargado"], ["Cajero", "Cliente"]),
    ("GET /temporadas", "GET", "/temporadas", ["Admin", "Encargado", "Cajero", "Cliente"], []),
    ("GET /inventario/movimientos", "GET", "/inventario/movimientos", ["Admin", "Encargado", "Cajero"], ["Cliente"]),
    ("POST /inventario/recepcion", "POST", "/inventario/recepcion", ["Admin", "Encargado"], ["Cajero", "Cliente"]),
    ("GET /reservas", "GET", "/reservas", ["Admin", "Encargado", "Cajero"], ["Cliente"]),
    ("GET /reservas/mis-reservas", "GET", "/reservas/mis-reservas", ["Admin", "Encargado", "Cajero", "Cliente"], []),
    ("GET /ventas", "GET", "/ventas", ["Admin", "Encargado", "Cajero"], ["Cliente"]),
    ("GET /ventas/reportes/resumen", "GET", "/ventas/reportes/resumen", ["Admin", "Encargado", "Cajero"], ["Cliente"]),
]

for label, method, path, allowed, forbidden in tests:
    print(f"\n--- Probando {label} ---")
    for role in allowed:
        token = tokens.get(role)
        headers = {"Authorization": f"Bearer {token}"}
        status, data = make_req(f"{BASE_URL}{path}", method=method, headers=headers, body={} if method == "POST" else None)
        if status not in (401, 403):
            print(f"  [ALLOWED] {role}: Status {status}")
        else:
            print(f"  [ERROR - DEBERIA TENER ACCESO] {role}: Status {status}")

    for role in forbidden:
        token = tokens.get(role)
        headers = {"Authorization": f"Bearer {token}"}
        status, data = make_req(f"{BASE_URL}{path}", method=method, headers=headers, body={} if method == "POST" else None)
        if status == 403:
            print(f"  [BLOCKED] {role}: Status 403 Forbidden (Correcto)")
        else:
            print(f"  [WARNING - DEBERIA ESTAR BLOQUEADO] {role}: Status {status}")

print("\n=== VERIFICACION COMPLETADA EXITOSAMENTE ===")
