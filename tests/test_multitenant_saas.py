import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.seed_permissions import seed as seed_permissions
from app.models.tenant import Tenant
from app.models.subscription_plan import SubscriptionPlan
from app.models.subscription import Subscription
from app.models.user import User
from app.models.branch import Branch
from app.models.product import Product
from app.models.category import Category
from app.models.role import Role
from app.core.security import get_password_hash
from datetime import date, timedelta
from decimal import Decimal


@pytest.fixture
def saas_setup(db_session: Session):
    """
    Sets up two tenants with plans, subscriptions, users, branches, and products.
    """
    seed_permissions(db_session)
    admin_role = db_session.query(Role).filter(Role.name == "admin").first()

    # 1. Create Subscription Plans
    plan_basico = SubscriptionPlan(
        codigo="BASICO",
        nombre="Plan Básico",
        descripcion="Para pequeñas boutiques",
        max_sucursales=2,
        permite_reportes_ia=False,
        permite_offline=True,
        precio_mensual_bob=Decimal("199.00"),
        activo=True
    )
    plan_pro = SubscriptionPlan(
        codigo="PROFESIONAL",
        nombre="Plan Profesional",
        descripcion="Para empresas en crecimiento con IA",
        max_sucursales=10,
        permite_reportes_ia=True,
        permite_offline=True,
        precio_mensual_bob=Decimal("499.00"),
        activo=True
    )
    db_session.add(plan_basico)
    db_session.add(plan_pro)
    db_session.flush()

    # 2. Create Tenants
    tenant_1 = Tenant(
        nombre="UrbanStyle Bolivia S.R.L.",
        razon_social="UrbanStyle Bolivia S.R.L.",
        nit_rut="1029384756",
        slug="urbanstyle",
        email_contacto="contacto@urbanstyle.com",
        telefono="70011223",
        activo=True
    )
    tenant_2 = Tenant(
        nombre="Moda Express Tarija",
        razon_social="Moda Express Tarija S.R.L.",
        nit_rut="9876543210",
        slug="modaexpress",
        email_contacto="contacto@modaexpress.com",
        telefono="76543210",
        activo=True
    )
    db_session.add(tenant_1)
    db_session.add(tenant_2)
    db_session.flush()

    # 3. Create Subscriptions
    today = date.today()
    sub_1 = Subscription(
        tenant_id=tenant_1.id,
        plan_id=plan_pro.id,
        estado="ACTIVA",
        fecha_inicio=today,
        fecha_vencimiento=today + timedelta(days=365)
    )
    sub_2 = Subscription(
        tenant_id=tenant_2.id,
        plan_id=plan_basico.id,
        estado="ACTIVA",
        fecha_inicio=today,
        fecha_vencimiento=today + timedelta(days=365)
    )
    db_session.add(sub_1)
    db_session.add(sub_2)

    # 4. Create Admin Users
    pwd_hash = get_password_hash("Password123!")
    user_1 = User(
        email="admin@urbanstyle.com",
        hashed_password=pwd_hash,
        full_name="Admin UrbanStyle",
        role_id=admin_role.id,
        tenant_id=tenant_1.id,
        is_active=True
    )
    user_2 = User(
        email="admin@modaexpress.com",
        hashed_password=pwd_hash,
        full_name="Admin Moda Express",
        role_id=admin_role.id,
        tenant_id=tenant_2.id,
        is_active=True
    )
    db_session.add(user_1)
    db_session.add(user_2)

    # 5. Create Branches: UrbanStyle has 1, Moda Express has 2 (its limit)
    b_u1 = Branch(
        tenant_id=tenant_1.id,
        nombre="UrbanStyle Equipetrol",
        ciudad="Santa Cruz",
        direccion="Av. San Martin #450",
        activa=True
    )
    b_m1 = Branch(
        tenant_id=tenant_2.id,
        nombre="Moda Express Tarija Centro",
        ciudad="Tarija",
        direccion="Calle Sucre #100",
        activa=True
    )
    b_m2 = Branch(
        tenant_id=tenant_2.id,
        nombre="Moda Express El Molino",
        ciudad="Tarija",
        direccion="Calle 15 de Abril #200",
        activa=True
    )
    db_session.add(b_u1)
    db_session.add(b_m1)
    db_session.add(b_m2)

    # 6. Create Products & Categories
    cat_u = Category(tenant_id=tenant_1.id, nombre="Urban Streetwear", activa=True)
    cat_m = Category(tenant_id=tenant_2.id, nombre="Moda Formal", activa=True)
    db_session.add(cat_u)
    db_session.add(cat_m)
    db_session.flush()

    prod_u = Product(
        tenant_id=tenant_1.id,
        categoria_id=cat_u.id,
        nombre="Hoodie Streetwear Oversize",
        descripcion="Hoodie urbano exclusivo",
        precio_base=Decimal("250.00"),
        activo=True
    )
    prod_m = Product(
        tenant_id=tenant_2.id,
        categoria_id=cat_m.id,
        nombre="Camisa Formal Ejecutiva",
        descripcion="Camisa formal de lino",
        precio_base=Decimal("180.00"),
        activo=True
    )
    db_session.add(prod_u)
    db_session.add(prod_m)

    db_session.commit()

    return {
        "tenant_1": tenant_1,
        "tenant_2": tenant_2,
        "plan_basico": plan_basico,
        "plan_pro": plan_pro,
        "user_1": user_1,
        "user_2": user_2
    }


def get_auth_token(client: TestClient, email: str, password: str = "Password123!"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed for {email}: {resp.text}"
    return resp.json()["access_token"]


def test_multitenant_data_isolation(client: TestClient, saas_setup):
    """
    Verify cross-tenant data isolation:
    UrbanStyle cannot see Moda Express's branches, products, or categories.
    Moda Express cannot see UrbanStyle's.
    """
    token_urban = get_auth_token(client, "admin@urbanstyle.com")
    token_moda = get_auth_token(client, "admin@modaexpress.com")

    headers_urban = {"Authorization": f"Bearer {token_urban}"}
    headers_moda = {"Authorization": f"Bearer {token_moda}"}

    # 1. Test Branches Isolation
    resp_u_branches = client.get("/api/v1/sucursales", headers=headers_urban)
    assert resp_u_branches.status_code == 200
    u_branches = resp_u_branches.json()
    assert len(u_branches) == 1
    assert u_branches[0]["nombre"] == "UrbanStyle Equipetrol"

    resp_m_branches = client.get("/api/v1/sucursales", headers=headers_moda)
    assert resp_m_branches.status_code == 200
    m_branches = resp_m_branches.json()
    assert len(m_branches) == 2
    assert all("Moda Express" in b["nombre"] for b in m_branches)

    # 2. Test Products Isolation
    resp_u_prods = client.get("/api/v1/productos", headers=headers_urban)
    assert resp_u_prods.status_code == 200
    u_items = resp_u_prods.json()
    assert len(u_items) == 1
    assert u_items[0]["nombre"] == "Hoodie Streetwear Oversize"

    resp_m_prods = client.get("/api/v1/productos", headers=headers_moda)
    assert resp_m_prods.status_code == 200
    m_items = resp_m_prods.json()
    assert len(m_items) == 1
    assert m_items[0]["nombre"] == "Camisa Formal Ejecutiva"


def test_plan_basico_branch_limit(client: TestClient, saas_setup):
    """
    Verify that a tenant on Plan Básico (limit: 2 branches) cannot create a 3rd branch.
    """
    token_moda = get_auth_token(client, "admin@modaexpress.com")
    headers_moda = {"Authorization": f"Bearer {token_moda}"}

    # Attempt to create 3rd branch for Moda Express
    payload = {
        "nombre": "Moda Express 3ra Sucursal",
        "ciudad": "Tarija",
        "direccion": "Av. Las Panosas #500",
        "telefono": "65432100",
        "activa": True
    }
    resp = client.post("/api/v1/sucursales", json=payload, headers=headers_moda)
    assert resp.status_code == 400
    error_msg = resp.json()["detail"]
    assert "Límite de sucursales alcanzado" in error_msg
    assert "Plan Básico" in error_msg


def test_plan_basico_ai_feature_restricted(client: TestClient, saas_setup):
    """
    Verify that an enterprise on Plan Básico is denied access to AI endpoints (HTTP 403).
    """
    token_moda = get_auth_token(client, "admin@modaexpress.com")
    headers_moda = {"Authorization": f"Bearer {token_moda}"}

    # Attempt to call Voice Reports Query
    query_payload = {
        "transcripcion": "¿Cuáles son las prendas más vendidas del mes?"
    }
    resp = client.post("/api/v1/reportes-voz/query", json=query_payload, headers=headers_moda)
    assert resp.status_code == 403
    assert "Plan Básico" in resp.json()["detail"] or "IA" in resp.json()["detail"]


def test_plan_profesional_ai_feature_allowed(client: TestClient, saas_setup):
    """
    Verify that an enterprise on Plan Profesional is allowed to use AI endpoints.
    """
    token_urban = get_auth_token(client, "admin@urbanstyle.com")
    headers_urban = {"Authorization": f"Bearer {token_urban}"}

    # Call Voice Reports Query - should not fail with 403
    query_payload = {
        "transcripcion": "¿Cuáles son las prendas más vendidas?"
    }
    resp = client.post("/api/v1/reportes-voz/query", json=query_payload, headers=headers_urban)
    assert resp.status_code != 403, f"Unexpected 403: {resp.text}"


def test_subscription_management_and_upgrade(client: TestClient, saas_setup):
    """
    Verify subscription endpoints:
    - GET /api/v1/subscriptions/current
    - GET /api/v1/subscriptions/plans
    - POST /api/v1/subscriptions/change-plan (upgrade)
    - Verifying that after upgrade, the branch limit is expanded and AI feature is unlocked.
    """
    token_moda = get_auth_token(client, "admin@modaexpress.com")
    headers_moda = {"Authorization": f"Bearer {token_moda}"}

    # 1. Check current subscription
    resp_curr = client.get("/api/v1/subscriptions/current", headers=headers_moda)
    assert resp_curr.status_code == 200
    curr_data = resp_curr.json()
    assert curr_data["plan"]["codigo"] == "BASICO"
    assert curr_data["sucursales_maximas"] == 2
    assert curr_data["sucursales_usadas"] == 2
    assert curr_data["permite_reportes_ia"] is False

    # 2. List available plans
    resp_plans = client.get("/api/v1/subscriptions/plans", headers=headers_moda)
    assert resp_plans.status_code == 200
    plans = resp_plans.json()
    assert len(plans) >= 2
    plan_codes = [p["codigo"] for p in plans]
    assert "BASICO" in plan_codes
    assert "PROFESIONAL" in plan_codes

    # 3. Upgrade to PROFESIONAL
    resp_change = client.post(
        "/api/v1/subscriptions/change-plan",
        json={"plan_codigo": "PROFESIONAL"},
        headers=headers_moda
    )
    assert resp_change.status_code == 200
    upgraded = resp_change.json()
    assert upgraded["plan"]["codigo"] == "PROFESIONAL"
    assert upgraded["sucursales_maximas"] == 10
    assert upgraded["permite_reportes_ia"] is True

    # 4. Now create 3rd branch - should succeed!
    payload = {
        "nombre": "Moda Express 3ra Sucursal",
        "ciudad": "Tarija",
        "direccion": "Av. Las Panosas #500",
        "telefono": "65432100",
        "activa": True
    }
    resp_b3 = client.post("/api/v1/sucursales", json=payload, headers=headers_moda)
    assert resp_b3.status_code == 201
    assert resp_b3.json()["nombre"] == "Moda Express 3ra Sucursal"

    # 5. Now query AI feature - should not return 403!
    query_payload = {
        "transcripcion": "¿Cuáles son las prendas más vendidas del mes?"
    }
    resp_ai = client.post("/api/v1/reportes-voz/query", json=query_payload, headers=headers_moda)
    assert resp_ai.status_code != 403
