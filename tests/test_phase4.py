import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.seed_permissions import seed

ADMIN_DATA = {
    "email": "admin_phase4@example.com",
    "password": "AdminPassword123!",
    "full_name": "Admin Phase 4"
}

CLIENT_DATA = {
    "email": "cliente_phase4@example.com",
    "password": "ClientPassword123!",
    "full_name": "Cliente Phase 4"
}


def test_phase4_complete_flow(client: TestClient, db_session: Session):
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
        "nombre": "Sucursal San Miguel",
        "ciudad": "La Paz",
        "direccion": "Calle 21 de Calacoto",
        "telefono": "70099887"
    }, headers=admin_headers)
    assert suc_resp.status_code == 201
    sucursal_id = suc_resp.json()["id"]

    cat_resp = client.post("/api/v1/categorias", json={
        "nombre": "Pantalones",
        "descripcion": "Jeans y pantalones"
    }, headers=admin_headers)
    assert cat_resp.status_code == 201
    categoria_id = cat_resp.json()["id"]

    prod_resp = client.post("/api/v1/productos", json={
        "codigo": "PAN-001",
        "nombre": "Jean Slim Fit",
        "categoria_id": categoria_id,
        "precio_base": 180.0
    }, headers=admin_headers)
    assert prod_resp.status_code == 201
    producto_id = prod_resp.json()["id"]

    var_resp = client.post(f"/api/v1/productos/{producto_id}/variantes", json={
        "sku": "PAN-001-32-AZU",
        "talla": "L",
        "color": "Azul",
        "precio_extra": 20.0
    }, headers=admin_headers)
    assert var_resp.status_code == 201
    variante_id = var_resp.json()["id"]

    # 5. Inventory Reception (10 units)
    rec_resp = client.post("/api/v1/inventario/recepcion", json={
        "sucursal_id": sucursal_id,
        "factura": "FAC-RES-01",
        "productos": [{"variante_id": variante_id, "cantidad": 10}]
    }, headers=admin_headers)
    assert rec_resp.status_code == 201

    # Check inventory before reservation
    stocks = client.get(f"/api/v1/inventario/sucursal/{sucursal_id}", headers=admin_headers).json()
    assert stocks[0]["stock_actual"] == 10
    assert stocks[0]["stock_reservado"] == 0
    assert stocks[0]["stock_disponible"] == 10

    # ==================== 6. CREATE RESERVATION (CLIENT) ====================
    future_time = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    res_payload = {
        "sucursal_id": sucursal_id,
        "fecha_hora_esperada": future_time,
        "nota": "Pasaré a recoger en la tarde",
        "items": [{"variante_id": variante_id, "cantidad": 2}]
    }

    res_resp = client.post("/api/v1/reservas", json=res_payload, headers=client_headers)
    assert res_resp.status_code == 201, res_resp.text
    res_data = res_resp.json()
    reserva_id = res_data["id"]
    assert res_data["estado"] == "PENDIENTE"
    assert res_data["total_estimado"] == 400.0  # (180 base + 20 extra) * 2 = 400
    assert len(res_data["detalles"]) == 1
    assert res_data["detalles"][0]["cantidad"] == 2

    # Verify inventory updated (stock_reservado = 2, stock_disponible = 8)
    stocks_after = client.get(f"/api/v1/inventario/sucursal/{sucursal_id}", headers=admin_headers).json()
    assert stocks_after[0]["stock_actual"] == 10
    assert stocks_after[0]["stock_reservado"] == 2
    assert stocks_after[0]["stock_disponible"] == 8

    # Verify client can view in /mis-reservas
    my_res = client.get("/api/v1/reservas/mis-reservas", headers=client_headers).json()
    assert len(my_res) >= 1
    assert my_res[0]["id"] == reserva_id

    # ==================== 7. PREPARE RESERVATION (STAFF) ====================
    prep_resp = client.patch(f"/api/v1/reservas/{reserva_id}/preparar", headers=admin_headers)
    assert prep_resp.status_code == 200
    assert prep_resp.json()["estado"] == "PREPARADA"

    # ==================== 8. PICKUP RESERVATION (STAFF) ====================
    pickup_resp = client.patch(f"/api/v1/reservas/{reserva_id}/recoger", headers=admin_headers)
    assert pickup_resp.status_code == 200
    assert pickup_resp.json()["estado"] == "RECOGIDA"
    assert pickup_resp.json()["fecha_recogida"] is not None

    # ==================== 9. CANCELLATION FLOW ====================
    # Create second reservation for 3 units
    res2_resp = client.post("/api/v1/reservas", json={
        "sucursal_id": sucursal_id,
        "fecha_hora_esperada": future_time,
        "items": [{"variante_id": variante_id, "cantidad": 3}]
    }, headers=client_headers)
    assert res2_resp.status_code == 201
    res2_id = res2_resp.json()["id"]

    # Stock reservado is now 2 (from previous) + 3 = 5
    stocks_mid = client.get(f"/api/v1/inventario/sucursal/{sucursal_id}", headers=admin_headers).json()
    assert stocks_mid[0]["stock_reservado"] == 5
    assert stocks_mid[0]["stock_disponible"] == 5

    # Client cancels second reservation
    cancel_resp = client.patch(f"/api/v1/reservas/{res2_id}/cancelar", headers=client_headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["estado"] == "CANCELADA"

    # Stock reservado released by 3 -> back to 2
    stocks_cancelled = client.get(f"/api/v1/inventario/sucursal/{sucursal_id}", headers=admin_headers).json()
    assert stocks_cancelled[0]["stock_reservado"] == 2
    assert stocks_cancelled[0]["stock_disponible"] == 8

    # ==================== 10. EXPIRATION FLOW ====================
    expire_resp = client.post("/api/v1/reservas/expirar-vencidas", headers=admin_headers)
    assert expire_resp.status_code == 200
    assert "total_expiradas" in expire_resp.json()
