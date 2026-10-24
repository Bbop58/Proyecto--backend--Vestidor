import requests
import json
import sys

BASE_URL = "http://127.0.0.1:8000/api/v1"

def log_check(name, passed, detail=""):
    symbol = "[PASS]" if passed else "[FAIL]"
    print(f" {symbol} {name}: {detail}")
    return passed

def run_audit():
    all_ok = True
    print("======================================================================")
    print("      AUDITORIA INTEGRAL DEL SISTEMA FICCT STORE - PARCIAL 2")
    print("======================================================================")

    # 1. Health Check
    try:
        r = requests.get("http://127.0.0.1:8000/health", timeout=5)
        passed = (r.status_code == 200 and r.json().get("status") == "healthy")
        all_ok = log_check("1. Backend Health Check", passed, f"Status: {r.status_code} - healthy") and all_ok
    except Exception as e:
        all_ok = log_check("1. Backend Health Check", False, str(e)) and all_ok

    # 2. Login UrbanStyle (Empresa 1 - Plan Profesional)
    token_urban = None
    try:
        r = requests.post(f"{BASE_URL}/auth/login", json={"email": "admin@urbanstyle.com", "password": "Password123!"})
        passed = (r.status_code == 200 and "access_token" in r.json())
        if passed:
            token_urban = r.json()["access_token"]
            tenant_name = r.json()["user"].get("tenant_name")
            all_ok = log_check("2. Autenticación Empresa 1 (UrbanStyle)", True, f"Tenant: {tenant_name}") and all_ok
        else:
            all_ok = log_check("2. Autenticación Empresa 1 (UrbanStyle)", False, f"{r.status_code} - {r.text}") and all_ok
    except Exception as e:
        all_ok = log_check("2. Autenticación Empresa 1 (UrbanStyle)", False, str(e)) and all_ok

    # 3. Login Moda Express (Empresa 2 - Plan Básico)
    token_moda = None
    try:
        r = requests.post(f"{BASE_URL}/auth/login", json={"email": "admin@modaexpress.com", "password": "Password123!"})
        passed = (r.status_code == 200 and "access_token" in r.json())
        if passed:
            token_moda = r.json()["access_token"]
            tenant_name = r.json()["user"].get("tenant_name")
            all_ok = log_check("3. Autenticación Empresa 2 (Moda Express)", True, f"Tenant: {tenant_name}") and all_ok
        else:
            all_ok = log_check("3. Autenticación Empresa 2 (Moda Express)", False, f"{r.status_code} - {r.text}") and all_ok
    except Exception as e:
        all_ok = log_check("3. Autenticación Empresa 2 (Moda Express)", False, str(e)) and all_ok

    # 4. Plan 04 - SaaS Multitenant: Cuotas, Aislamiento y Restricciones
    if token_urban and token_moda:
        headers_u = {"Authorization": f"Bearer {token_urban}"}
        headers_m = {"Authorization": f"Bearer {token_moda}"}

        # Subscriptions Current UrbanStyle
        ru_sub = requests.get(f"{BASE_URL}/subscriptions/current", headers=headers_u)
        passed_u = (ru_sub.status_code == 200 and ru_sub.json()["plan"]["codigo"] == "PROFESIONAL")
        all_ok = log_check("4.1. Suscripción UrbanStyle (Plan Pro)", passed_u, f"Sucursales: {ru_sub.json().get('sucursales_usadas')}/{ru_sub.json().get('sucursales_maximas')} | IA: {ru_sub.json().get('permite_reportes_ia')}") and all_ok

        # Subscriptions Current Moda Express
        rm_sub = requests.get(f"{BASE_URL}/subscriptions/current", headers=headers_m)
        passed_m = (rm_sub.status_code == 200 and rm_sub.json()["plan"]["codigo"] == "BASICO" and rm_sub.json()["sucursales_usadas"] == 2)
        all_ok = log_check("4.2. Suscripción Moda Express (Plan Básico)", passed_m, f"Sucursales: {rm_sub.json().get('sucursales_usadas')}/{rm_sub.json().get('sucursales_maximas')} (100% Límite) | IA: {rm_sub.json().get('permite_reportes_ia')}") and all_ok

        # Límite de sucursales en Plan Básico: intento de crear 3ra sucursal -> 400
        r_b3 = requests.post(f"{BASE_URL}/sucursales", json={
            "nombre": "Moda Express Sucursal Extra",
            "ciudad": "Tarija",
            "direccion": "Calle Falsa 123",
            "activa": True
        }, headers=headers_m)
        passed_limit = (r_b3.status_code == 400 and "Límite de sucursales alcanzado" in r_b3.text)
        all_ok = log_check("4.3. Bloqueo de 3ra Sucursal en Plan Básico", passed_limit, f"HTTP {r_b3.status_code} (Límite protegido)") and all_ok

        # Restricción de IA en Plan Básico -> 403 Forbidden
        r_ai_m = requests.post(f"{BASE_URL}/reportes-voz/query", json={"pregunta": "Prendas más vendidas"}, headers=headers_m)
        passed_ai_block = (r_ai_m.status_code == 403)
        all_ok = log_check("4.4. Bloqueo de IA en Plan Básico", passed_ai_block, f"HTTP {r_ai_m.status_code} Forbidden (Restricción activa)") and all_ok

        # Aislamiento de Sucursales
        r_su = requests.get(f"{BASE_URL}/sucursales", headers=headers_u)
        r_sm = requests.get(f"{BASE_URL}/sucursales", headers=headers_m)
        passed_iso = (r_su.status_code == 200 and r_sm.status_code == 200 and len(r_su.json()) == 4 and len(r_sm.json()) == 2)
        all_ok = log_check("4.5. Aislamiento Lógico Multitenant", passed_iso, f"UrbanStyle: {len(r_su.json())} sucursales | Moda Express: {len(r_sm.json())} sucursales") and all_ok

    # 5. Plan 01 - Historial y Sistema de Cambios de Prendas
    if token_urban:
        headers_u = {"Authorization": f"Bearer {token_urban}"}
        r_cambios = requests.get(f"{BASE_URL}/cambios", headers=headers_u)
        passed_cambios = (r_cambios.status_code == 200 and isinstance(r_cambios.json(), list))
        all_ok = log_check("5. Plan 01 - Módulo de Cambios de Prendas", passed_cambios, f"HTTP 200 OK | Registros: {len(r_cambios.json())}") and all_ok

    # 6. Plan 02 - Reportes IA por Voz (Gemini Text-to-SQL y Excel)
    if token_urban:
        headers_u = {"Authorization": f"Bearer {token_urban}"}
        r_rep = requests.post(f"{BASE_URL}/reportes-voz/query", json={"pregunta": "Ventas totales por sucursal"}, headers=headers_u)
        passed_rep = (r_rep.status_code == 200 and "filas" in r_rep.json() and "columnas" in r_rep.json())
        all_ok = log_check("6. Plan 02 - Reportes Inteligentes IA (Voz/Texto)", passed_rep, f"HTTP 200 OK | Columnas: {r_rep.json().get('columnas')} | Filas: {len(r_rep.json().get('filas', []))}") and all_ok

    # 7. Plan 03 - Machine Learning & Analítica Predictiva
    if token_urban:
        headers_u = {"Authorization": f"Bearer {token_urban}"}
        # Previsión de Demanda (Supervisado)
        r_fc = requests.get(f"{BASE_URL}/analytics/forecast?weeks_ahead=4", headers=headers_u)
        fc_data = r_fc.json()
        passed_fc = (r_fc.status_code == 200 and "serie_temporal" in fc_data)
        r2 = fc_data.get("metricas", {}).get("r2_score")
        proyecciones = [p for p in fc_data.get("serie_temporal", []) if p.get("es_proyeccion")]
        all_ok = log_check("7.1. Plan 03 - ML Supervisado (Demanda)", passed_fc, f"Algoritmo: Random Forest | R²: {r2} | Semanas Proyectadas: {len(proyecciones)}") and all_ok

        # Segmentación de Clientes (No Supervisado)
        r_seg = requests.get(f"{BASE_URL}/analytics/segments", headers=headers_u)
        seg_data = r_seg.json()
        passed_seg = (r_seg.status_code == 200 and "clusters" in seg_data)
        sil = seg_data.get("metricas", {}).get("silhouette_score")
        all_ok = log_check("7.2. Plan 03 - ML No Supervisado (K-Means Clientes)", passed_seg, f"Clusters: {len(seg_data.get('clusters', []))} | Silhouette Score: {sil}") and all_ok

    # 8. Inventario y Alertas de Stock
    if token_urban:
        headers_u = {"Authorization": f"Bearer {token_urban}"}
        r_inv = requests.get(f"{BASE_URL}/inventario/alertas", headers=headers_u)
        passed_inv = (r_inv.status_code == 200 and isinstance(r_inv.json(), list))
        all_ok = log_check("8. Control de Inventario y Alertas", passed_inv, f"HTTP 200 OK | Alertas evaluadas: {len(r_inv.json())}") and all_ok

    # 9. Plan 05 - Motor de Sincronización Offline e Idempotencia (RNF04/RNF05)
    if token_urban:
        headers_u = {"Authorization": f"Bearer {token_urban}"}
        # 9.1 Consultar historial de sincronizaciones
        r_sync_hist = requests.get(f"{BASE_URL}/sync/history?limit=10", headers=headers_u)
        passed_hist = (r_sync_hist.status_code == 200 and isinstance(r_sync_hist.json(), list))
        all_ok = log_check("9.1. Plan 05 - Historial de Sincronización Offline", passed_hist, f"HTTP 200 OK | Registros previos: {len(r_sync_hist.json()) if passed_hist else 0}") and all_ok

        # 9.2 Probar Lote Idempotente con UUID
        import uuid
        from datetime import datetime, timezone
        test_client_op_id = str(uuid.uuid4())
        
        # Obtener una sucursal activa y una variante
        r_branches = requests.get(f"{BASE_URL}/sucursales", headers=headers_u)
        r_prods = requests.get(f"{BASE_URL}/productos", headers=headers_u)
        
        branch_id = r_branches.json()[0]["id"] if (r_branches.status_code == 200 and len(r_branches.json()) > 0) else None
        variant_id = None
        if r_prods.status_code == 200:
            for p in r_prods.json():
                if p.get("variantes"):
                    variant_id = p["variantes"][0]["id"]
                    break

        if branch_id and variant_id:
            fake_batch = {
                "operations": [
                    {
                        "client_operation_id": test_client_op_id,
                        "operation_type": "VENTA_CONTADO_OFFLINE",
                        "sucursal_id": branch_id,
                        "fecha_local": datetime.now(timezone.utc).isoformat(),
                        "items": [
                            {
                                "variante_id": variant_id,
                                "cantidad": 1,
                                "precio_unitario": 100.00
                            }
                        ],
                        "monto_total": 100.00,
                        "metodo_pago": "EFECTIVO"
                    }
                ]
            }
            # Primer envío
            r_sync1 = requests.post(f"{BASE_URL}/sync/batch", json=fake_batch, headers=headers_u)
            # Reintento (Replay para validar idempotencia RNF05)
            r_sync2 = requests.post(f"{BASE_URL}/sync/batch", json=fake_batch, headers=headers_u)
            passed_idempotency = (
                r_sync1.status_code == 200 and 
                r_sync2.status_code == 200 and 
                r_sync2.json().get("total_duplicates") == 1 and
                r_sync2.json().get("results", [{}])[0].get("is_duplicate") is True
            )
            all_ok = log_check("9.2. Plan 05 - Idempotencia RNF05 (Reintento de Lote)", passed_idempotency, f"1er Envío: {r_sync1.status_code} | 2do Envío (Duplicado Detectado): {r_sync2.json().get('total_duplicates')} duplicado(s)") and all_ok

    print("======================================================================")
    if all_ok:
        print(" [RESULTADO GLOBAL] TODOS LOS MODULOS OPERANDO AL 100% SIN ERRORES")
    else:
        print(" [RESULTADO GLOBAL] DETECTADOS MODULOS CON INCIDENCIAS")
    print("======================================================================")
    return all_ok

if __name__ == "__main__":
    ok = run_audit()
    sys.exit(0 if ok else 1)
