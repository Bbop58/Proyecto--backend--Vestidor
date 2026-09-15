import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.seed_permissions import seed
from app.models.role import Role
from app.models.user import User

ADMIN_DATA = {
    "email": "admin_phase1@example.com",
    "password": "AdminPassword123!",
    "full_name": "Admin Phase 1"
}

CLIENT_DATA = {
    "email": "cliente_phase1@example.com",
    "password": "ClientPassword123!",
    "full_name": "Cliente Phase 1"
}


def test_phase1_complete_flow(client: TestClient, db_session: Session):
    # 1. Seed database with roles and permissions
    seed(db_session)

    # 2. Register Admin user (first user = admin)
    admin_reg = client.post("/api/v1/auth/register", json=ADMIN_DATA)
    assert admin_reg.status_code == 201
    admin_token = admin_reg.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 3. Register Cliente user (subsequent user = cliente)
    client_reg = client.post("/api/v1/auth/register", json=CLIENT_DATA)
    assert client_reg.status_code == 201
    client_token = client_reg.json()["access_token"]
    client_headers = {"Authorization": f"Bearer {client_token}"}

    # ==================== SUCURSALES ====================
    # Admin creates branch
    branch_payload = {
        "nombre": "Sucursal Central",
        "ciudad": "La Paz",
        "direccion": "Av. 16 de Julio #1234",
        "telefono": "22233344",
        "activa": True
    }
    b_resp = client.post("/api/v1/sucursales", json=branch_payload, headers=admin_headers)
    assert b_resp.status_code == 201, b_resp.text
    branch_data = b_resp.json()
    branch_id = branch_data["id"]
    assert branch_data["nombre"] == "Sucursal Central"

    # Client tries to create branch -> 403 Forbidden
    b_forbidden = client.post("/api/v1/sucursales", json=branch_payload, headers=client_headers)
    assert b_forbidden.status_code == 403

    # Admin updates branch
    b_up_resp = client.put(
        f"/api/v1/sucursales/{branch_id}",
        json={"telefono": "77788899"},
        headers=admin_headers
    )
    assert b_up_resp.status_code == 200
    assert b_up_resp.json()["telefono"] == "77788899"

    # Admin soft deletes branch
    b_del_resp = client.delete(f"/api/v1/sucursales/{branch_id}", headers=admin_headers)
    assert b_del_resp.status_code == 200
    assert b_del_resp.json()["activa"] is False

    # ==================== CATEGORÍAS ====================
    # Admin creates category
    cat_payload = {
        "nombre": "Camisas y Polos",
        "descripcion": "Ropa formal y casual",
        "activa": True
    }
    cat_resp = client.post("/api/v1/categorias", json=cat_payload, headers=admin_headers)
    assert cat_resp.status_code == 201, cat_resp.text
    cat_data = cat_resp.json()
    category_id = cat_data["id"]

    # Client can list categories
    cat_list = client.get("/api/v1/categorias", headers=client_headers)
    assert cat_list.status_code == 200
    assert len(cat_list.json()) >= 1

    # ==================== PRODUCTOS Y VARIANTES ====================
    # Admin creates product with initial variant
    prod_payload = {
        "nombre": "Camisa Oxford Clásica",
        "descripcion": "Camisa manga larga 100% algodón",
        "precio_base": 180.50,
        "categoria_id": category_id,
        "temporada": "Primavera-Verano 2025",
        "proveedor": "Textiles del Valle",
        "activo": True,
        "variantes": [
            {
                "talla": "M",
                "color": "Azul",
                "precio_extra": 0.00,
                "activo": True
            }
        ]
    }
    prod_resp = client.post("/api/v1/productos", json=prod_payload, headers=admin_headers)
    assert prod_resp.status_code == 201, prod_resp.text
    prod_data = prod_resp.json()
    product_id = prod_data["id"]
    assert len(prod_data["variantes"]) == 1
    first_var = prod_data["variantes"][0]
    assert "OXFORD" in first_var["sku"] or "CAMISA" in first_var["sku"]

    # Add second variant to product
    var2_payload = {
        "talla": "L",
        "color": "Blanco",
        "precio_extra": 10.00,
        "activo": True
    }
    var2_resp = client.post(f"/api/v1/productos/{product_id}/variantes", json=var2_payload, headers=admin_headers)
    assert var2_resp.status_code == 201, var2_resp.text
    variant2_id = var2_resp.json()["id"]

    # Duplicate variant combination (L, Blanco) on same product -> 400 Bad Request
    var_dup_resp = client.post(f"/api/v1/productos/{product_id}/variantes", json=var2_payload, headers=admin_headers)
    assert var_dup_resp.status_code == 400

    # Test Product filters
    filter_cat = client.get(f"/api/v1/productos?categoria_id={category_id}", headers=admin_headers)
    assert filter_cat.status_code == 200
    assert len(filter_cat.json()) == 1

    filter_search = client.get("/api/v1/productos?search=Oxford", headers=admin_headers)
    assert filter_search.status_code == 200
    assert len(filter_search.json()) == 1

    # Update variant
    var_up_resp = client.put(f"/api/v1/variantes/{variant2_id}", json={"precio_extra": 15.00}, headers=admin_headers)
    assert var_up_resp.status_code == 200
    assert float(var_up_resp.json()["precio_extra"]) == 15.00

    # Soft delete variant
    var_del_resp = client.delete(f"/api/v1/variantes/{variant2_id}", headers=admin_headers)
    assert var_del_resp.status_code == 200
    assert var_del_resp.json()["activo"] is False

    # Soft delete product
    prod_del_resp = client.delete(f"/api/v1/productos/{product_id}", headers=admin_headers)
    assert prod_del_resp.status_code == 200
    assert prod_del_resp.json()["activo"] is False
