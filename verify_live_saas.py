import requests
import json

BASE_URL = "http://127.0.0.1:8000/api/v1"

def test_login(email, password="Password123!"):
    resp = requests.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password})
    if resp.status_code != 200:
        print(f"[-] Login failed for {email}: {resp.status_code} - {resp.text}")
        return None
    data = resp.json()
    token = data["access_token"]
    user = data["user"]
    print(f"[+] Login OK: {email} | Tenant: {user.get('tenant_name')} ({user.get('tenant_id')})")
    return token

def check_subscription(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{BASE_URL}/subscriptions/current", headers=headers)
    if resp.status_code == 200:
        sub = resp.json()
        print(f"    Plan: {sub['plan']['nombre']} ({sub['plan']['codigo']})")
        print(f"    Sucursales: {sub['sucursales_usadas']} / {sub['sucursales_maximas']} ({sub['porcentaje_sucursales_usadas']}%)")
        print(f"    Permite IA: {sub['permite_reportes_ia']}")
        return sub
    else:
        print(f"[-] Subscription check failed: {resp.status_code} - {resp.text}")
        return None

def check_branches(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{BASE_URL}/sucursales", headers=headers)
    if resp.status_code == 200:
        branches = resp.json()
        print(f"    Sucursales visibles: {len(branches)} -> {[b['nombre'] for b in branches]}")
    else:
        print(f"[-] Branches fetch failed: {resp.status_code} - {resp.text}")

def check_ai_feature(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(
        f"{BASE_URL}/reportes-voz/query",
        headers=headers,
        json={"transcripcion": "Prendas más vendidas"}
    )
    print(f"    AI Endpoint status: {resp.status_code} ({'BLOQUEADO POR PLAN' if resp.status_code == 403 else 'HABILITADO'})")

def main():
    print("==================================================")
    print("VERIFICACION EN VIVO SAAS MULTITENANT")
    print("==================================================")
    
    print("\n--- 1. EMPRESA 1: UrbanStyle Bolivia (Plan Profesional) ---")
    token_u = test_login("admin@urbanstyle.com")
    if token_u:
        check_subscription(token_u)
        check_branches(token_u)
        check_ai_feature(token_u)

    print("\n--- 2. EMPRESA 2: Moda Express Tarija (Plan Básico) ---")
    token_m = test_login("admin@modaexpress.com")
    if token_m:
        check_subscription(token_m)
        check_branches(token_m)
        check_ai_feature(token_m)

    print("\n==================================================")
    print("VERIFICACION COMPLETADA CON EXITO")
    print("==================================================")

if __name__ == "__main__":
    main()
