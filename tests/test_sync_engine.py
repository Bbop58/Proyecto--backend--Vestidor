import pytest
import uuid
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.seed_permissions import seed as seed_permissions
from app.models.tenant import Tenant
from app.models.subscription_plan import SubscriptionPlan
from app.models.subscription import Subscription
from app.models.user import User
from app.models.branch import Branch
from app.models.category import Category
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.inventory import Inventory
from app.models.sale import Sale
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.sync_operation import SyncOperation
from app.models.role import Role
from app.core.security import get_password_hash


@pytest.fixture
def sync_setup(db_session: Session):
    """
    Sets up two tenants with branches, products, variants, and stock for testing sync and idempotency.
    """
    seed_permissions(db_session)
    admin_role = db_session.query(Role).filter(Role.name == "admin").first()

    # Plan
    plan_pro = SubscriptionPlan(
        codigo="PROFESIONAL",
        nombre="Plan Profesional",
        max_sucursales=10,
        permite_reportes_ia=True,
        permite_offline=True,
        precio_mensual_bob=Decimal("499.00"),
        activo=True
    )
    db_session.add(plan_pro)
    db_session.flush()

    # Tenants
    tenant_1 = Tenant(
        nombre="UrbanStyle Bolivia S.R.L.",
        razon_social="UrbanStyle Bolivia S.R.L.",
        nit_rut="1029384756",
        slug="urbanstyle",
        activo=True
    )
    tenant_2 = Tenant(
        nombre="Moda Express Tarija",
        razon_social="Moda Express Tarija S.R.L.",
        nit_rut="9876543210",
        slug="modaexpress",
        activo=True
    )
    db_session.add(tenant_1)
    db_session.add(tenant_2)
    db_session.flush()

    # Users
    pwd_hash = get_password_hash("Password123!")
    user_1 = User(
        email="cajero@urbanstyle.com",
        hashed_password=pwd_hash,
        full_name="Cajero UrbanStyle",
        role_id=admin_role.id,
        tenant_id=tenant_1.id,
        is_active=True
    )
    user_2 = User(
        email="cajero@modaexpress.com",
        hashed_password=pwd_hash,
        full_name="Cajero Moda Express",
        role_id=admin_role.id,
        tenant_id=tenant_2.id,
        is_active=True
    )
    db_session.add(user_1)
    db_session.add(user_2)

    # Branches
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
    db_session.add(b_u1)
    db_session.add(b_m1)
    db_session.flush()

    # Category, Product & Variant for UrbanStyle
    cat_u = Category(tenant_id=tenant_1.id, nombre="Streetwear", activa=True)
    db_session.add(cat_u)
    db_session.flush()

    prod_u = Product(
        tenant_id=tenant_1.id,
        categoria_id=cat_u.id,
        nombre="Hoodie Tech Heavyweight",
        precio_base=Decimal("250.00"),
        activo=True
    )
    db_session.add(prod_u)
    db_session.flush()

    var_u1 = ProductVariant(
        producto_id=prod_u.id,
        sku="URB-HD-M-BLK",
        talla="M",
        color="Negro",
        precio_extra=Decimal("0.00"),
        activo=True
    )
    var_u2 = ProductVariant(
        producto_id=prod_u.id,
        sku="URB-HD-L-BLK",
        talla="L",
        color="Negro",
        precio_extra=Decimal("0.00"),
        activo=True
    )
    db_session.add(var_u1)
    db_session.add(var_u2)
    db_session.flush()

    # Initial Inventory: 10 units of M, 3 units of L
    inv_u1 = Inventory(
        tenant_id=tenant_1.id,
        sucursal_id=b_u1.id,
        variante_id=var_u1.id,
        stock_actual=10,
        stock_minimo=2
    )
    inv_u2 = Inventory(
        tenant_id=tenant_1.id,
        sucursal_id=b_u1.id,
        variante_id=var_u2.id,
        stock_actual=3,
        stock_minimo=1
    )
    db_session.add(inv_u1)
    db_session.add(inv_u2)

    db_session.commit()

    return {
        "tenant_1": tenant_1,
        "tenant_2": tenant_2,
        "branch_u1": b_u1,
        "branch_m1": b_m1,
        "var_u1": var_u1,
        "var_u2": var_u2,
        "inv_u1": inv_u1,
        "inv_u2": inv_u2,
        "user_1": user_1,
        "user_2": user_2
    }


def get_auth_token(client: TestClient, email: str, password: str = "Password123!"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed for {email}: {resp.text}"
    return resp.json()["access_token"]


def test_sync_single_sale_success(client: TestClient, db_session: Session, sync_setup):
    """
    Test successful offline sale sync:
    - Deducts inventory atomically
    - Generates receipt number
    - Records kardex movement
    """
    token = get_auth_token(client, "cajero@urbanstyle.com")
    headers = {"Authorization": f"Bearer {token}"}

    client_op_id = str(uuid.uuid4())
    b_id = str(sync_setup["branch_u1"].id)
    v_id = str(sync_setup["var_u1"].id)

    payload = {
        "operations": [
            {
                "client_operation_id": client_op_id,
                "operation_type": "VENTA_CONTADO_OFFLINE",
                "sucursal_id": b_id,
                "fecha_local": datetime.now(timezone.utc).isoformat(),
                "items": [
                    {
                        "variante_id": v_id,
                        "cantidad": 2,
                        "precio_unitario": 250.00
                    }
                ],
                "monto_total": 500.00,
                "monto_efectivo_recibido": 500.00,
                "cambio_entregado": 0.00,
                "metodo_pago": "EFECTIVO"
            }
        ]
    }

    resp = client.post("/api/v1/sync/batch", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["total_received"] == 1
    assert data["total_confirmed"] == 1
    assert data["total_rejected"] == 0
    assert data["total_duplicates"] == 0

    res = data["results"][0]
    assert res["status"] == "CONFIRMADA"
    assert res["is_duplicate"] is False
    assert res["receipt_number"].startswith("VTA-OFF-")
    assert res["server_entity_id"] is not None

    # Check Database Stock: started at 10, sold 2 -> must be 8
    inv = db_session.query(Inventory).filter(
        Inventory.variante_id == sync_setup["var_u1"].id,
        Inventory.sucursal_id == sync_setup["branch_u1"].id
    ).first()
    assert inv.stock_actual == 8

    # Check Kardex Movement
    mov = db_session.query(InventoryMovement).filter(
        InventoryMovement.referencia == res["receipt_number"]
    ).first()
    assert mov is not None
    assert mov.cantidad == -2
    assert mov.tipo == MovementType.SALIDA_VENTA


def test_sync_strict_idempotency_duplicate_replay(client: TestClient, db_session: Session, sync_setup):
    """
    RNF05: Send the exact same batch twice.
    First time: CONFIRMADA, is_duplicate=False, stock goes 10 -> 8.
    Second time: CONFIRMADA, is_duplicate=True, stock remains 8 (NO DOUBLE DEDUCTION).
    """
    token = get_auth_token(client, "cajero@urbanstyle.com")
    headers = {"Authorization": f"Bearer {token}"}

    client_op_id = str(uuid.uuid4())
    b_id = str(sync_setup["branch_u1"].id)
    v_id = str(sync_setup["var_u1"].id)

    payload = {
        "operations": [
            {
                "client_operation_id": client_op_id,
                "operation_type": "VENTA_CONTADO_OFFLINE",
                "sucursal_id": b_id,
                "fecha_local": datetime.now(timezone.utc).isoformat(),
                "items": [
                    {
                        "variante_id": v_id,
                        "cantidad": 2,
                        "precio_unitario": 250.00
                    }
                ],
                "monto_total": 500.00
            }
        ]
    }

    # First request
    resp1 = client.post("/api/v1/sync/batch", json=payload, headers=headers)
    assert resp1.status_code == 200
    res1 = resp1.json()["results"][0]
    assert res1["status"] == "CONFIRMADA"
    assert res1["is_duplicate"] is False
    receipt_1 = res1["receipt_number"]

    # Verify stock deducted once: 10 - 2 = 8
    inv1 = db_session.query(Inventory).filter(Inventory.variante_id == sync_setup["var_u1"].id).first()
    assert inv1.stock_actual == 8

    # Second request (Replay / Network retry)
    resp2 = client.post("/api/v1/sync/batch", json=payload, headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total_duplicates"] == 1
    res2 = data2["results"][0]
    assert res2["status"] == "CONFIRMADA"
    assert res2["is_duplicate"] is True
    assert res2["receipt_number"] == receipt_1  # Exact same receipt replay

    # Verify stock was NOT deducted a second time (STILL 8!)
    db_session.expire_all()
    inv2 = db_session.query(Inventory).filter(Inventory.variante_id == sync_setup["var_u1"].id).first()
    assert inv2.stock_actual == 8

    # Verify only ONE sale exists in DB
    sales_count = db_session.query(Sale).filter(Sale.numero_recibo == receipt_1).count()
    assert sales_count == 1


def test_sync_stock_conflict_rejection(client: TestClient, db_session: Session, sync_setup):
    """
    Conflict Handling:
    Variant has only 3 units in stock.
    Offline device sends a sale for 5 units.
    Server rejects with RECHAZADA_POR_STOCK without crashing.
    Stock remains 3.
    """
    token = get_auth_token(client, "cajero@urbanstyle.com")
    headers = {"Authorization": f"Bearer {token}"}

    client_op_id = str(uuid.uuid4())
    b_id = str(sync_setup["branch_u1"].id)
    v_id = str(sync_setup["var_u2"].id)  # has 3 units

    payload = {
        "operations": [
            {
                "client_operation_id": client_op_id,
                "operation_type": "VENTA_CONTADO_OFFLINE",
                "sucursal_id": b_id,
                "fecha_local": datetime.now(timezone.utc).isoformat(),
                "items": [
                    {
                        "variante_id": v_id,
                        "cantidad": 5,  # Exceeds 3!
                        "precio_unitario": 250.00
                    }
                ],
                "monto_total": 1250.00
            }
        ]
    }

    resp = client.post("/api/v1/sync/batch", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_confirmed"] == 0
    assert data["total_rejected"] == 1
    res = data["results"][0]
    assert res["status"] == "RECHAZADA_POR_STOCK"
    assert "Stock insuficiente" in res["error_detail"]

    # Stock untouched
    inv = db_session.query(Inventory).filter(Inventory.variante_id == sync_setup["var_u2"].id).first()
    assert inv.stock_actual == 3


def test_sync_batch_partial_success(client: TestClient, db_session: Session, sync_setup):
    """
    Batch with 2 operations:
    Op 1: Valid (requests 2 of item with stock 10) -> CONFIRMADA
    Op 2: Invalid (requests 100 of item with stock 3) -> RECHAZADA_POR_STOCK
    Verifies that failure of Op 2 DOES NOT rollback Op 1.
    """
    token = get_auth_token(client, "cajero@urbanstyle.com")
    headers = {"Authorization": f"Bearer {token}"}

    op1_id = str(uuid.uuid4())
    op2_id = str(uuid.uuid4())
    b_id = str(sync_setup["branch_u1"].id)

    payload = {
        "operations": [
            {
                "client_operation_id": op1_id,
                "operation_type": "VENTA_CONTADO_OFFLINE",
                "sucursal_id": b_id,
                "fecha_local": datetime.now(timezone.utc).isoformat(),
                "items": [{"variante_id": str(sync_setup["var_u1"].id), "cantidad": 2, "precio_unitario": 250.00}],
                "monto_total": 500.00
            },
            {
                "client_operation_id": op2_id,
                "operation_type": "VENTA_CONTADO_OFFLINE",
                "sucursal_id": b_id,
                "fecha_local": datetime.now(timezone.utc).isoformat(),
                "items": [{"variante_id": str(sync_setup["var_u2"].id), "cantidad": 100, "precio_unitario": 250.00}],
                "monto_total": 25000.00
            }
        ]
    }

    resp = client.post("/api/v1/sync/batch", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_received"] == 2
    assert data["total_confirmed"] == 1
    assert data["total_rejected"] == 1

    assert data["results"][0]["status"] == "CONFIRMADA"
    assert data["results"][1]["status"] == "RECHAZADA_POR_STOCK"

    # Op 1 stock deducted (10 - 2 = 8)
    inv1 = db_session.query(Inventory).filter(Inventory.variante_id == sync_setup["var_u1"].id).first()
    assert inv1.stock_actual == 8

    # Op 2 stock untouched (3)
    inv2 = db_session.query(Inventory).filter(Inventory.variante_id == sync_setup["var_u2"].id).first()
    assert inv2.stock_actual == 3


def test_sync_multitenant_branch_isolation(client: TestClient, db_session: Session, sync_setup):
    """
    SaaS Isolation: User from Moda Express tries to sync a sale targeting UrbanStyle's branch.
    Server rejects with status CONFLICTO.
    """
    token_moda = get_auth_token(client, "cajero@modaexpress.com")
    headers_moda = {"Authorization": f"Bearer {token_moda}"}

    client_op_id = str(uuid.uuid4())
    # Targeting UrbanStyle's branch!
    b_urban_id = str(sync_setup["branch_u1"].id)

    payload = {
        "operations": [
            {
                "client_operation_id": client_op_id,
                "operation_type": "VENTA_CONTADO_OFFLINE",
                "sucursal_id": b_urban_id,
                "fecha_local": datetime.now(timezone.utc).isoformat(),
                "items": [{"variante_id": str(sync_setup["var_u1"].id), "cantidad": 1, "precio_unitario": 250.00}],
                "monto_total": 250.00
            }
        ]
    }

    resp = client.post("/api/v1/sync/batch", json=payload, headers=headers_moda)
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_rejected"] == 1
    res = data["results"][0]
    assert res["status"] == "CONFLICTO"
    assert "no pertenece a su empresa" in res["error_detail"]
