import pytest
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.seed_permissions import seed

ADMIN_DATA = {
    "email": "admin_phase2@example.com",
    "password": "AdminPassword123!",
    "full_name": "Admin Phase 2"
}

CLIENT_DATA = {
    "email": "cliente_phase2@example.com",
    "password": "ClientPassword123!",
    "full_name": "Cliente Phase 2"
}


def test_phase2_complete_flow(client: TestClient, db_session: Session):
    # 1. Seed database
    seed(db_session)

    # 2. Register Admin user
    admin_reg = client.post("/api/v1/auth/register", json=ADMIN_DATA)
    assert admin_reg.status_code == 201
    admin_token = admin_reg.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 3. Register Cliente user
    client_reg = client.post("/api/v1/auth/register", json=CLIENT_DATA)
    assert client_reg.status_code == 201
    client_token = client_reg.json()["access_token"]
    client_headers = {"Authorization": f"Bearer {client_token}"}

    # ==================== PROVEEDORES ====================
    # Admin creates supplier
    sup_payload = {
        "nombre": "Textiles del Altiplano",
        "contacto": "Juan Perez",
        "telefono": "76543210",
        "email": "contacto@altiplano.com",
        "direccion": "Zona Industrial El Alto",
        "activo": True
    }
    sup_resp = client.post("/api/v1/proveedores", json=sup_payload, headers=admin_headers)
    assert sup_resp.status_code == 201, sup_resp.text
    supplier_data = sup_resp.json()
    supplier_id = supplier_data["id"]
    assert supplier_data["nombre"] == "Textiles del Altiplano"

    # Client tries to create supplier -> 403 Forbidden
    sup_forbidden = client.post("/api/v1/proveedores", json=sup_payload, headers=client_headers)
    assert sup_forbidden.status_code == 403

    # Admin updates supplier
    sup_up_resp = client.put(
        f"/api/v1/proveedores/{supplier_id}",
        json={"contacto": "Carlos Perez"},
        headers=admin_headers
    )
    assert sup_up_resp.status_code == 200
    assert sup_up_resp.json()["contacto"] == "Carlos Perez"

    # Admin soft deletes supplier
    sup_del_resp = client.delete(f"/api/v1/proveedores/{supplier_id}", headers=admin_headers)
    assert sup_del_resp.status_code == 200
    assert sup_del_resp.json()["activo"] is False

    # ==================== TEMPORADAS ====================
    # List seeded seasons
    seasons_resp = client.get("/api/v1/temporadas", headers=admin_headers)
    assert seasons_resp.status_code == 200
    seasons = seasons_resp.json()
    assert len(seasons) >= 4

    # Check that Primavera-Verano 2025 is active
    pv_season = next(s for s in seasons if s["nombre"] == "Primavera-Verano 2025")
    assert pv_season["activa"] is True

    # Try creating season with invalid dates (fecha_fin < fecha_inicio) -> 422 Unprocessable Entity
    invalid_dates_payload = {
        "nombre": "Temporada Inválida 2026",
        "año": 2026,
        "fecha_inicio": "2026-10-01",
        "fecha_fin": "2026-05-01",
        "activa": False
    }
    inv_resp = client.post("/api/v1/temporadas", json=invalid_dates_payload, headers=admin_headers)
    assert inv_resp.status_code == 422

    # Activate another season in 2025 (e.g. Otoño-Invierno 2025) -> PV 2025 should deactivate automatically
    oi_season = next(s for s in seasons if s["nombre"] == "Otoño-Invierno 2025")
    oi_activate_resp = client.put(
        f"/api/v1/temporadas/{oi_season['id']}",
        json={"activa": True},
        headers=admin_headers
    )
    assert oi_activate_resp.status_code == 200
    assert oi_activate_resp.json()["activa"] is True

    # Verify that PV 2025 was automatically set to activa = False
    pv_updated = client.get(f"/api/v1/temporadas/{pv_season['id']}", headers=admin_headers).json()
    assert pv_updated["activa"] is False

    # Create a new season for 2026
    s2026_payload = {
        "nombre": "Verano 2026",
        "año": 2026,
        "fecha_inicio": "2026-01-01",
        "fecha_fin": "2026-04-30",
        "activa": True
    }
    s2026_resp = client.post("/api/v1/temporadas", json=s2026_payload, headers=admin_headers)
    assert s2026_resp.status_code == 201
    s2026_id = s2026_resp.json()["id"]

    # Delete 2026 season (no products attached)
    del_s_resp = client.delete(f"/api/v1/temporadas/{s2026_id}", headers=admin_headers)
    assert del_s_resp.status_code == 204
