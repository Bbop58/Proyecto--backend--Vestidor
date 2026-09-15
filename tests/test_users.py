import pytest
from fastapi.testclient import TestClient

REGISTER_DATA = {
    "email": "userprofile@example.com",
    "password": "StrongPassword123!",
    "full_name": "Profile User"
}


def test_get_current_user_profile(client: TestClient):
    reg_resp = client.post("/api/v1/auth/register", json=REGISTER_DATA)
    token = reg_resp.json()["access_token"]
    
    response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == REGISTER_DATA["email"]
    assert data["full_name"] == REGISTER_DATA["full_name"]
    assert data["is_active"] is True


def test_get_current_user_unauthorized(client: TestClient):
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_update_user_profile(client: TestClient):
    reg_resp = client.post("/api/v1/auth/register", json=REGISTER_DATA)
    token = reg_resp.json()["access_token"]
    
    response = client.put(
        "/api/v1/users/me",
        json={"full_name": "Updated Full Name"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Updated Full Name"
