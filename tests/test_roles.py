import pytest
from fastapi.testclient import TestClient
from app.seed_permissions import seed

ADMIN_USER_DATA = {
    "email": "adminrbac@example.com",
    "password": "AdminPassword123!",
    "full_name": "Admin RBAC User"
}


from sqlalchemy.orm import Session


def test_roles_and_permissions_flow(client: TestClient, db_session: Session):
    # 1. Seed permissions
    seed(db_session)


    # 2. Register user (will get admin role by default)
    reg_resp = client.post("/api/v1/auth/register", json=ADMIN_USER_DATA)
    assert reg_resp.status_code == 201
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 3. List permissions
    perm_resp = client.get("/api/v1/permissions", headers=headers)
    assert perm_resp.status_code == 200
    groups = perm_resp.json()
    assert len(groups) > 0

    # 4. List roles
    roles_resp = client.get("/api/v1/roles", headers=headers)
    assert roles_resp.status_code == 200
    roles = roles_resp.json()
    assert any(r["name"] == "admin" for r in roles)

    # 5. Create new role
    new_role_resp = client.post(
        "/api/v1/roles",
        json={"name": "editor", "description": "Editor con acceso limitado"},
        headers=headers
    )
    assert new_role_resp.status_code == 201
    role_id = new_role_resp.json()["id"]

    # 6. Assign permissions to new role
    perm_id = groups[0]["permissions"][0]["id"]
    assign_resp = client.put(
        f"/api/v1/roles/{role_id}/permissions",
        json={"permission_ids": [perm_id]},
        headers=headers
    )
    assert assign_resp.status_code == 200
    assert len(assign_resp.json()["permissions"]) == 1

    # 7. Try deleting admin role -> should fail 400
    admin_role = next(r for r in roles if r["name"] == "admin")
    del_admin_resp = client.delete(f"/api/v1/roles/{admin_role['id']}", headers=headers)
    assert del_admin_resp.status_code == 400
