import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.seed_permissions import seed

ADMIN_DATA = {
    "email": "admin_phase3@example.com",
    "password": "AdminPassword123!",
    "full_name": "Admin Phase 3"
}

CLIENT_DATA = {
    "email": "cliente_phase3@example.com",
    "password": "ClientPassword123!",
    "full_name": "Cliente Phase 3"
}


def test_phase3_complete_flow(client: TestClient, db_session: Session):
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
    # Branch
    suc_resp = client.post("/api/v1/sucursales", json={
        "nombre": "Sucursal Central",
        "ciudad": "La Paz",
        "direccion": "Av. Principal 123",
        "telefono": "70011223"
    }, headers=admin_headers)
    assert suc_resp.status_code == 201
    sucursal_id = suc_resp.json()["id"]

    # Category
    cat_resp = client.post("/api/v1/categorias", json={
        "nombre": "Poleras",
        "descripcion": "Poleras de algodón"
    }, headers=admin_headers)
    assert cat_resp.status_code == 201
    categoria_id = cat_resp.json()["id"]

    # Product
    prod_resp = client.post("/api/v1/productos", json={
        "codigo": "POL-001",
        "nombre": "Polera Oversize",
        "categoria_id": categoria_id,
        "precio_base": 120.0
    }, headers=admin_headers)
    assert prod_resp.status_code == 201
    producto_id = prod_resp.json()["id"]

    # Variant
    var_resp = client.post(f"/api/v1/productos/{producto_id}/variantes", json={
        "sku": "POL-001-M-NEG",
        "talla": "M",
        "color": "Negro",
        "precio_extra": 0.0
    }, headers=admin_headers)
    assert var_resp.status_code == 201
    variante_id = var_resp.json()["id"]

    # ==================== INVENTARIO - RECEPCION ====================
    rec_payload = {
        "sucursal_id": sucursal_id,
        "factura": "FAC-00123",
        "nota": "Lote inicial de temporada",
        "productos": [
            {
                "variante_id": variante_id,
                "cantidad": 20
            }
        ]
    }

    # Client tries reception -> 403
    rec_forbidden = client.post("/api/v1/inventario/recepcion", json=rec_payload, headers=client_headers)
    assert rec_forbidden.status_code == 403

    # Admin performs reception
    rec_resp = client.post("/api/v1/inventario/recepcion", json=rec_payload, headers=admin_headers)
    assert rec_resp.status_code == 201, rec_resp.text
    rec_data = rec_resp.json()
    assert rec_data["items_procesados"] == 1

    # ==================== INVENTARIO - CONSULTA STOCK ====================
    stock_resp = client.get(f"/api/v1/inventario/sucursal/{sucursal_id}", headers=admin_headers)
    assert stock_resp.status_code == 200
    stocks = stock_resp.json()
    assert len(stocks) == 1
    assert stocks[0]["stock_actual"] == 20
    assert stocks[0]["stock_disponible"] == 20
    inventario_id = stocks[0]["id"]

    # Update settings (ubicacion y stock_minimo)
    set_resp = client.put(
        f"/api/v1/inventario/{inventario_id}",
        json={"stock_minimo": 5, "ubicacion": "Estante A1"},
        headers=admin_headers
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["ubicacion"] == "Estante A1"
    assert set_resp.json()["stock_minimo"] == 5

    # Availability endpoint (public)
    avail_resp = client.get(f"/api/v1/inventario/disponibilidad?producto_id={producto_id}")
    assert avail_resp.status_code == 200
    avail_data = avail_resp.json()
    assert len(avail_data) == 1
    assert avail_data[0]["stock_disponible"] == 20
    assert avail_data[0]["ciudad"] == "La Paz"

    # ==================== INVENTARIO - AJUSTE ====================
    adj_payload = {
        "inventario_id": inventario_id,
        "cantidad": -17,
        "nota": "Prendas dañadas por humedad"
    }
    adj_resp = client.post("/api/v1/inventario/ajuste", json=adj_payload, headers=admin_headers)
    assert adj_resp.status_code == 200
    adj_data = adj_resp.json()
    assert adj_data["stock_actual"] == 3
    assert adj_data["stock_disponible"] == 3
    assert adj_data["alerta"] == "BAJO"  # stock_minimo is 5, stock_actual is 3 -> alerta BAJO

    # ==================== INVENTARIO - ALERTAS ====================
    alerts_resp = client.get("/api/v1/inventario/alertas", headers=admin_headers)
    assert alerts_resp.status_code == 200
    alerts = alerts_resp.json()
    assert len(alerts) >= 1
    assert any(a["inventario_id"] == inventario_id for a in alerts)

    # ==================== MOVIMIENTOS ====================
    movs_resp = client.get(f"/api/v1/inventario/movimientos?sucursal_id={sucursal_id}", headers=admin_headers)
    assert movs_resp.status_code == 200
    movs = movs_resp.json()
    assert len(movs) == 2  # 1 ENTRADA, 1 AJUSTE
    # Check ENTRADA
    entrada = next(m for m in movs if m["tipo"] == "ENTRADA")
    assert entrada["cantidad"] == 20
    assert entrada["stock_antes"] == 0
    assert entrada["stock_despues"] == 20
    assert entrada["referencia"] == "FAC-00123"
    # Check AJUSTE
    ajuste = next(m for m in movs if m["tipo"] == "AJUSTE")
    assert ajuste["cantidad"] == -17
    assert ajuste["stock_antes"] == 20
    assert ajuste["stock_despues"] == 3
