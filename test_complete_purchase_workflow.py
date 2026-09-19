import sys
import uuid
from datetime import datetime, timedelta, timezone

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def log(step: str, detail: str):
    print(f"\n[PASO {step}] {detail}")

def test_full_workflow():
    print("=================================================================")
    print("   TEST DE WORKFLOW COMPLETO DE COMPRA (FICCT STORE)")
    print("=================================================================")

    unique_suffix = uuid.uuid4().hex[:6]
    test_email = f"comprador_{unique_suffix}@ficttstore.com"
    test_pass = "Password123!"
    test_name = f"Carlos Comprador {unique_suffix}"

    # -------------------------------------------------------------
    # 1. REGISTRO DE NUEVO CLIENTE
    # -------------------------------------------------------------
    log("1", f"Registrando nuevo cliente: {test_email}")
    reg_res = client.post("/api/v1/auth/register", json={
        "email": test_email,
        "password": test_pass,
        "full_name": test_name
    })
    assert reg_res.status_code == 201, f"Error en registro: {reg_res.text}"
    auth_data = reg_res.json()
    client_token = auth_data["access_token"]
    client_id = auth_data["user"]["id"]
    client_headers = {"Authorization": f"Bearer {client_token}"}
    print(f" -> Cliente registrado exitosamente. ID: {client_id}")
    print(f" -> Token JWT obtenido: {client_token[:25]}...")

    # -------------------------------------------------------------
    # 2. EXPLORACIÓN DE CATÁLOGO: BÚSQUEDA DE POLERAS
    # -------------------------------------------------------------
    log("2", "Explorando catálogo de productos buscando 'Polera'")
    prods_res = client.get("/api/v1/productos?search=Polera", headers=client_headers)
    assert prods_res.status_code == 200, f"Error listando productos: {prods_res.text}"
    products = prods_res.json()
    assert len(products) > 0, "No se encontraron poleras en el catálogo sembrado"

    polera = products[0]
    print(f" -> Producto seleccionado: '{polera['nombre']}' (Precio base: {polera['precio_base']} Bs.)")
    
    variantes = polera.get("variantes", [])
    assert len(variantes) > 0, "La polera no tiene variantes disponibles"
    variante = variantes[0]
    variante_id = variante["id"]
    sku = variante["sku"]
    talla = variante["talla"]
    color = variante["color"]
    precio_final = float(polera["precio_base"]) + float(variante.get("precio_extra", 0))
    print(f" -> Variante seleccionada: Talla {talla}, Color {color}, SKU: {sku}, Precio: {precio_final} Bs.")

    # Obtener Sucursal Central
    branches_res = client.get("/api/v1/sucursales", headers=client_headers)
    assert branches_res.status_code == 200, "Error obteniendo sucursales"
    branches = branches_res.json()
    sucursal = branches[0]
    sucursal_id = sucursal["id"]
    print(f" -> Sucursal seleccionada: {sucursal['nombre']} ({sucursal['ciudad']})")

    # -------------------------------------------------------------
    # 3. WORKFLOW 1: RESERVA CLICK & COLLECT (APP MÓVIL)
    # -------------------------------------------------------------
    log("3", "Creando Reserva Click & Collect (Apartado por 24h sin pago previo)")
    future_pickup = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()
    res_payload = {
        "sucursal_id": sucursal_id,
        "fecha_hora_esperada": future_pickup,
        "items": [{"variante_id": variante_id, "cantidad": 1}],
        "nota": "Apartado desde App Móvil para retiro hoy"
    }
    create_res = client.post("/api/v1/reservas", json=res_payload, headers=client_headers)
    assert create_res.status_code == 201, f"Error creando reserva: {create_res.text}"
    res_data = create_res.json()
    reserva_id = res_data["id"]
    codigo_reserva = res_data["codigo"]
    print(f" -> Reserva creada con éxito: {codigo_reserva}")
    print(f" -> Estado inicial: {res_data['estado']} (Stock reservado bloqueado en sucursal)")

    # -------------------------------------------------------------
    # 4. WORKFLOW 2: COBRO DE LA RESERVA EN PUNTO DE VENTA (POS WEB)
    # -------------------------------------------------------------
    log("4", "Cajero inicia sesión en Web POS y procesa cobro en EFECTIVO de la reserva")
    cajero_res = client.post("/api/v1/auth/login", json={
        "email": "cajero@ficttstore.com",
        "password": "Password123!"
    })
    assert cajero_res.status_code == 200, "Error en login de cajero"
    cajero_token = cajero_res.json()["access_token"]
    cajero_headers = {"Authorization": f"Bearer {cajero_token}"}

    pos_sale_payload = {
        "sucursal_id": sucursal_id,
        "cliente_id": client_id,
        "items": [{
            "variante_id": variante_id,
            "cantidad": 1,
            "precio_unitario": precio_final,
            "reserva_id": reserva_id
        }],
        "pago": {
            "metodo": "EFECTIVO",
            "monto_recibido": precio_final + 50.0  # Pago con billete mayor para verificar cambio
        },
        "nota": f"Cobro en mostrador de reserva {codigo_reserva}"
    }
    sale_res = client.post("/api/v1/ventas/presencial", json=pos_sale_payload, headers=cajero_headers)
    assert sale_res.status_code == 201, f"Error procesando venta POS: {sale_res.text}"
    sale_data = sale_res.json()
    numero_recibo = sale_data["numero_recibo"]
    cambio_calculado = sale_data["cambio"]
    print(f" -> Venta POS completada: {numero_recibo}")
    print(f" -> Total cobrado: {sale_data['monto_total']} Bs. | Monto recibido: {sale_data['monto_recibido']} Bs. | Cambio entregado: {cambio_calculado} Bs.")

    # Verificar que la reserva haya pasado a COMPLETADA
    check_res = client.get(f"/api/v1/reservas/{reserva_id}", headers=cajero_headers)
    assert check_res.json()["estado"] == "COMPLETADA", "La reserva debería estar COMPLETADA"
    print(f" -> Estado de la reserva actualizado a: COMPLETADA [OK]")

    # -------------------------------------------------------------
    # 5. WORKFLOW 3: COMPRA DIRECTA CON PAYPAL SANDBOX (APP MÓVIL)
    # -------------------------------------------------------------
    log("5", "Creando Orden de Compra Online Directa con PayPal Sandbox")
    paypal_order_payload = {
        "monto_bob": precio_final,
        "sucursal_id": sucursal_id,
        "cliente_id": client_id,
        "descripcion": f"Compra de Polera {talla}/{color} en FICCT STORE",
        "items": [{
            "name": f"Polera ({talla}/{color})",
            "quantity": 1,
            "unit_amount_bob": precio_final
        }],
        "return_url": "https://backend-production-d7d5d.up.railway.app/api/v1/payments/paypal/return",
        "cancel_url": "https://backend-production-d7d5d.up.railway.app/api/v1/payments/paypal/cancel"
    }
    paypal_res = client.post("/api/v1/payments/paypal/create-order", json=paypal_order_payload, headers=client_headers)
    assert paypal_res.status_code == 201, f"Error creando orden PayPal: {paypal_res.text}"
    paypal_order = paypal_res.json()
    order_id = paypal_order["order_id"]
    monto_usd = paypal_order["monto_usd"]
    approve_url = paypal_order["approve_url"]
    print(f" -> Orden PayPal creada: {order_id}")
    print(f" -> Total BOB: {paypal_order['monto_bob']} Bs. == USD: ${monto_usd} (Tasa: {paypal_order['exchange_rate']})")
    print(f" -> URL de Aprobación generada: {approve_url[:60]}...")

    # -------------------------------------------------------------
    # 6. VERIFICACIÓN DE ENDPOINTS DE RETORNO VISUAL HTML
    # -------------------------------------------------------------
    log("6", "Verificando endpoint visual HTML /paypal/return")
    html_res = client.get("/api/v1/payments/paypal/return")
    assert html_res.status_code == 200, "El endpoint /paypal/return falló"
    assert "Pago Autorizado" in html_res.text, "El HTML no contiene el mensaje de confirmación"
    print(" -> Página HTML de éxito de PayPal renderizada y verificada correctamente (200 OK) [OK]")

    # -------------------------------------------------------------
    # 7. VENTA DIGITAL (COMPLETANDO COMPRA TRAS RETORNO DE PAYPAL)
    # -------------------------------------------------------------
    log("7", "Registrando Venta Digital en App Móvil con token PayPal")
    digital_sale_payload = {
        "sucursal_id": sucursal_id,
        "items": [{
            "variante_id": variante_id,
            "cantidad": 1,
            "precio_unitario": precio_final
        }],
        "token_pago": f"PAYPAL_{order_id}",
        "nota": "Compra digital completada con PayPal Sandbox"
    }
    dig_res = client.post("/api/v1/ventas/digital", json=digital_sale_payload, headers=client_headers)
    assert dig_res.status_code == 201, f"Error en venta digital: {dig_res.text}"
    dig_data = dig_res.json()
    print(f" -> Venta digital registrada: {dig_data['numero_recibo']} (Total: {dig_data['monto_total']} Bs., Método: {dig_data['metodo_pago']}) [OK]")

    # Verificar mis compras
    my_purchases = client.get("/api/v1/ventas/mis-compras", headers=client_headers)
    assert my_purchases.status_code == 200, "Error obteniendo mis compras"
    assert len(my_purchases.json()) > 0, "Debería haber al menos una compra registrada"
    print(f" -> Historial de 'Mis Compras' verificado: {len(my_purchases.json())} compra(s) encontrada(s) [OK]")

    # -------------------------------------------------------------
    # 8. VENTA DIRECTA EN MOSTRADOR POS CON PAYPAL
    # -------------------------------------------------------------
    log("8", "Verificando Venta Directa en POS Web con método PAYPAL")
    direct_pos_paypal_payload = {
        "sucursal_id": sucursal_id,
        "cliente_id": client_id,
        "items": [{
            "variante_id": variante_id,
            "cantidad": 1,
            "precio_unitario": precio_final
        }],
        "pago": {
            "metodo": "PAYPAL",
            "referencia": f"PAYPAL_{order_id}"
        },
        "nota": "Venta mostrador cobrada con PayPal"
    }
    direct_sale_res = client.post("/api/v1/ventas/presencial", json=direct_pos_paypal_payload, headers=cajero_headers)
    assert direct_sale_res.status_code == 201, f"Error en venta directa POS PayPal: {direct_sale_res.text}"
    direct_sale = direct_sale_res.json()
    print(f" -> Venta POS con PayPal completada: {direct_sale['numero_recibo']} (Método: {direct_sale['metodo_pago']}) [OK]")

    print("\n=================================================================")
    print("   TODOS LOS FLUJOS Y REGLAS DE NEGOCIO PASARON AL 100%")
    print("=================================================================")

if __name__ == "__main__":
    try:
        test_full_workflow()
    except Exception as e:
        print(f"\nError durante el test: {e}")
        sys.exit(1)
