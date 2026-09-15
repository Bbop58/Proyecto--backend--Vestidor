import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.seed_permissions import seed

ADMIN_DATA = {
    "email": "admin_phase5@example.com",
    "password": "AdminPassword123!",
    "full_name": "Admin Phase 5"
}

CLIENT_DATA = {
    "email": "cliente_phase5@example.com",
    "password": "ClientPassword123!",
    "full_name": "Cliente Phase 5"
}


def test_phase5_complete_flow(client: TestClient, db_session: Session):
    # 1. Seed database
    seed(db_session)

    # 2. Register Admin user
    admin_reg = client.post("/api/v1/auth/register", json=ADMIN_DATA)
    assert admin_reg.status_code == 201
    admin_token = admin_reg.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 3. Register Client user
    client_reg = client.post("/api/v1/auth/register", json=CLIENT_DATA)
    assert client_reg.status_code == 201
    client_token = client_reg.json()["access_token"]
    client_headers = {"Authorization": f"Bearer {client_token}"}

    # 4. Setup Branch, Category, Product, Variant
    suc_resp = client.post("/api/v1/sucursales", json={
        "nombre": "Sucursal Sopocachi",
        "ciudad": "La Paz",
        "direccion": "Plaza Abaroa #456",
        "telefono": "70055443"
    }, headers=admin_headers)
    assert suc_resp.status_code == 201
    sucursal_id = suc_resp.json()["id"]

    cat_resp = client.post("/api/v1/categorias", json={
        "nombre": "Chaquetas",
        "descripcion": "Chaquetas y abrigos"
    }, headers=admin_headers)
    assert cat_resp.status_code == 201
    categoria_id = cat_resp.json()["id"]

    prod_resp = client.post("/api/v1/productos", json={
        "codigo": "CHQ-001",
        "nombre": "Chaqueta Cuero",
        "categoria_id": categoria_id,
        "precio_base": 250.0
    }, headers=admin_headers)
    assert prod_resp.status_code == 201
    producto_id = prod_resp.json()["id"]

    var_resp = client.post(f"/api/v1/productos/{producto_id}/variantes", json={
        "sku": "CHQ-001-M-NEG",
        "talla": "M",
        "color": "Negro",
        "precio_extra": 0.0
    }, headers=admin_headers)
    assert var_resp.status_code == 201
    variante_id = var_resp.json()["id"]

    # 5. Inventory Reception (20 units)
    rec_resp = client.post("/api/v1/inventario/recepcion", json={
        "sucursal_id": sucursal_id,
        "factura": "FAC-VTA-01",
        "productos": [{"variante_id": variante_id, "cantidad": 20}]
    }, headers=admin_headers)
    assert rec_resp.status_code == 201

    # ==================== 6. DIRECT PRESENCIAL CASH SALE ====================
    sale_payload = {
        "sucursal_id": sucursal_id,
        "items": [
            {
                "variante_id": variante_id,
                "cantidad": 3
            }
        ],
        "pago": {
            "metodo": "EFECTIVO",
            "monto_recibido": 800.0
        },
        "nota": "Venta rápida de mostrador"
    }

    sale_resp = client.post("/api/v1/ventas/presencial", json=sale_payload, headers=admin_headers)
    assert sale_resp.status_code == 201, sale_resp.text
    sale_data = sale_resp.json()
    sale_id = sale_data["id"]
    assert sale_data["monto_total"] == 750.0  # 3 * 250 = 750
    assert sale_data["cambio"] == 50.0        # 800 - 750 = 50
    assert sale_data["estado"] == "COMPLETADA"
    assert len(sale_data["detalles"]) == 1

    # Check inventory updated (stock_actual = 17)
    stocks_after = client.get(f"/api/v1/inventario/sucursal/{sucursal_id}", headers=admin_headers).json()
    assert stocks_after[0]["stock_actual"] == 17
    assert stocks_after[0]["stock_disponible"] == 17

    # ==================== 7. SALE FROM RESERVATION ====================
    # Client creates reservation for 2 units
    future_time = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    res_resp = client.post("/api/v1/reservas", json={
        "sucursal_id": sucursal_id,
        "fecha_hora_esperada": future_time,
        "items": [{"variante_id": variante_id, "cantidad": 2}]
    }, headers=client_headers)
    assert res_resp.status_code == 201
    reserva_id = res_resp.json()["id"]

    # Stock is now stock_actual = 17, stock_reservado = 2, stock_disponible = 15
    stocks_res = client.get(f"/api/v1/inventario/sucursal/{sucursal_id}", headers=admin_headers).json()
    assert stocks_res[0]["stock_reservado"] == 2
    assert stocks_res[0]["stock_disponible"] == 15

    # Cashier processes sale for the reservation using QR payment
    sale_res_payload = {
        "sucursal_id": sucursal_id,
        "items": [
            {
                "variante_id": variante_id,
                "cantidad": 2,
                "reserva_id": reserva_id
            }
        ],
        "pago": {
            "metodo": "QR",
            "referencia": "QR-BNB-887766"
        }
    }
    sale_res_resp = client.post("/api/v1/ventas/presencial", json=sale_res_payload, headers=admin_headers)
    assert sale_res_resp.status_code == 201
    assert sale_res_resp.json()["monto_total"] == 500.0

    # Verify reservation is now COMPLETADA
    res_status = client.get(f"/api/v1/reservas/{reserva_id}", headers=admin_headers).json()
    assert res_status["estado"] == "COMPLETADA"

    # Verify inventory (stock_actual = 15, stock_reservado = 0, stock_disponible = 15)
    stocks_res_after = client.get(f"/api/v1/inventario/sucursal/{sucursal_id}", headers=admin_headers).json()
    assert stocks_res_after[0]["stock_actual"] == 15
    assert stocks_res_after[0]["stock_reservado"] == 0
    assert stocks_res_after[0]["stock_disponible"] == 15

    # ==================== 8. CANCEL SALE FLOW ====================
    cancel_resp = client.patch(f"/api/v1/ventas/{sale_id}/cancelar", json={
        "motivo": "Cliente devolvió las prendas por error en talla"
    }, headers=admin_headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["estado"] == "CANCELADA"

    # Stock restored from 15 to 18
    stocks_reverted = client.get(f"/api/v1/inventario/sucursal/{sucursal_id}", headers=admin_headers).json()
    assert stocks_reverted[0]["stock_actual"] == 18

    # ==================== 9. SALES REPORTS ====================
    summary_resp = client.get("/api/v1/ventas/reportes/resumen", headers=admin_headers)
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["total_ventas"] >= 1  # 1 completed sale (QR sale)
    assert summary["ventas_qr"] == 500.0

    top_resp = client.get("/api/v1/ventas/reportes/top-productos", headers=admin_headers)
    assert top_resp.status_code == 200
    assert len(top_resp.json()) >= 1
