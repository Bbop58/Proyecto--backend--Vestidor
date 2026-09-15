import pytest
from fastapi.testclient import TestClient

REGISTER_DATA = {
    "email": "testuser@example.com",
    "password": "SecurePassword123!",
    "full_name": "Test User"
}


def test_health_check(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_register_user_success(client: TestClient):
    response = client.post("/api/v1/auth/register", json=REGISTER_DATA)
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == REGISTER_DATA["email"]
    assert data["user"]["full_name"] == REGISTER_DATA["full_name"]
    assert "id" in data["user"]


def test_register_duplicate_email_fails(client: TestClient):
    # First registration
    client.post("/api/v1/auth/register", json=REGISTER_DATA)
    # Second registration with same email
    response = client.post("/api/v1/auth/register", json=REGISTER_DATA)
    assert response.status_code == 400
    assert "Ya existe un usuario" in response.json()["detail"]


def test_login_success(client: TestClient):
    # Register first
    client.post("/api/v1/auth/register", json=REGISTER_DATA)
    
    # Login
    response = client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_DATA["email"], "password": REGISTER_DATA["password"]}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == REGISTER_DATA["email"]


def test_login_invalid_password_fails(client: TestClient):
    client.post("/api/v1/auth/register", json=REGISTER_DATA)
    
    response = client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_DATA["email"], "password": "WrongPassword123!"}
    )
    assert response.status_code == 401


def test_login_nonexistent_user_fails(client: TestClient):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "Password123!"}
    )
    assert response.status_code == 401


def test_refresh_token(client: TestClient):
    reg_resp = client.post("/api/v1/auth/register", json=REGISTER_DATA)
    refresh_token = reg_resp.json()["refresh_token"]
    
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_logout_and_revocation(client: TestClient):
    reg_resp = client.post("/api/v1/auth/register", json=REGISTER_DATA)
    access_token = reg_resp.json()["access_token"]
    refresh_token = reg_resp.json()["refresh_token"]
    
    # Check access before logout
    me_resp = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert me_resp.status_code == 200
    
    # Logout
    logout_resp = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert logout_resp.status_code == 200
    
    # Check access after logout -> should be 401 (token revoked)
    me_resp_after = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert me_resp_after.status_code == 401
