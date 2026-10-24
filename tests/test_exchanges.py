import pytest
import uuid
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.seed_permissions import seed
from app.models.sale import Sale
from app.models.inventory import Inventory

ADMIN_DATA = {
    "email": "admin_exchanges@example.com",
    "password": "AdminPassword123!",
    "full_name": "Admin Exchanges"
}


def test_complete_product_exchange_flow(client: TestClient, db_session: Session):
    # 1. Seed base roles & permissions
    seed(db_session)

    # 2. Registrar usuario Admin
    admin_reg = client.post("/api/v1/auth/register", json=ADMIN_DATA)
    assert admin_reg.status_code == 201
    admin_token = admin_reg.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 3. Crear Sucursal, Categoría, Producto y 2 Variantes (Talla S y Talla M)
    suc_resp = client.post("/api/v1/sucursales", json={
        "nombre": "Sucursal Central Moda",
        "ciudad": "Santa Cruz",
        "direccion": "Av. Monseñor Rivero #123",
        "telefono": "78912345"
    }, headers=admin_headers)
    assert suc_resp.status_code == 201
    sucursal_id = suc_resp.json()["id"]

    cat_resp = client.post("/api/v1/categorias", json={
        "nombre": "Poleras y Camisas",
        "descripcion": "Prendas superiores"
    }, headers=admin_headers)
    assert cat_resp.status_code == 201
    categoria_id = cat_resp.json()["id"]

    prod_resp = client.post("/api/v1/productos", json={
        "codigo": "POL-EXC-01",
        "nombre": "Polera Oversize Algodón",
        "categoria_id": categoria_id,
        "precio_base": 100.0
    }, headers=admin_headers)
    assert prod_resp.status_code == 201
    producto_id = prod_resp.json()["id"]

    # Variante S (Negra, sin precio extra)
    var_s_resp = client.post(f"/api/v1/productos/{producto_id}/variantes", json={
        "talla": "S",
        "color": "Negro",
        "sku": f"POL-S-BLK-{uuid.uuid4().hex[:4]}",
        "precio_extra": 0.0
    }, headers=admin_headers)
    assert var_s_resp.status_code == 201
    var_s_id = var_s_resp.json()["id"]

    # Variante M (Blanca, precio extra 10.0 -> total 110.0)
    var_m_resp = client.post(f"/api/v1/productos/{producto_id}/variantes", json={
        "talla": "M",
        "color": "Blanco",
        "sku": f"POL-M-WHT-{uuid.uuid4().hex[:4]}",
        "precio_extra": 10.0
    }, headers=admin_headers)
    assert var_m_resp.status_code == 201
    var_m_id = var_m_resp.json()["id"]

    # 4. Recepcionar Stock inicial (5 unidades de Talla S y 5 de Talla M)
    rec_resp = client.post("/api/v1/inventario/recepcion", json={
        "sucursal_id": sucursal_id,
        "factura": "FAC-TEST-EXCHANGE",
        "productos": [
            {"variante_id": var_s_id, "cantidad": 5},
            {"variante_id": var_m_id, "cantidad": 5}
        ]
    }, headers=admin_headers)
    assert rec_resp.status_code == 201

    # 5. Realizar venta presencial: el cliente compra 2 unidades de Talla S (Total: 200.0)
    sale_resp = client.post("/api/v1/ventas/presencial", json={
        "sucursal_id": sucursal_id,
        "items": [
            {
                "variante_id": var_s_id,
                "cantidad": 2,
                "precio_unitario": 100.0
            }
        ],
        "pago": {
            "metodo": "EFECTIVO",
            "monto_recibido": 200.0
        }
    }, headers=admin_headers)
    assert sale_resp.status_code == 201
    venta = sale_resp.json()
    numero_recibo = venta["numero_recibo"]
    venta_id = venta["id"]

    # Verificar stock post-venta:
    # Talla S debe ser 5 - 2 = 3
    # Talla M debe ser 5
    inv_s = db_session.query(Inventory).filter(Inventory.sucursal_id == sucursal_id, Inventory.variante_id == var_s_id).first()
    assert inv_s.stock_actual == 3
    inv_m = db_session.query(Inventory).filter(Inventory.sucursal_id == sucursal_id, Inventory.variante_id == var_m_id).first()
    assert inv_m.stock_actual == 5

    # 6. Test: Verificar recibo
    verif_resp = client.get(f"/api/v1/cambios/verificar-recibo/{numero_recibo}", headers=admin_headers)
    assert verif_resp.status_code == 200
    verif_data = verif_resp.json()
    assert verif_data["valido"] is True
    assert verif_data["dentro_del_plazo"] is True
    assert verif_data["dias_transcurridos"] == 0
    assert len(verif_data["items"]) == 1
    assert verif_data["items"][0]["cantidad_disponible_cambio"] == 2

    # 7. Test: Validación de tiempo límite (> 30 días)
    # Simulamos retroceder la fecha de la venta a 35 días atrás
    venta_db = db_session.query(Sale).filter(Sale.id == venta_id).first()
    fecha_antigua = datetime.now(timezone.utc) - timedelta(days=35)
    venta_db.created_at = fecha_antigua
    db_session.commit()

    # Ahora verificar debe indicar que expiró el plazo
    verif_expired = client.get(f"/api/v1/cambios/verificar-recibo/{numero_recibo}", headers=admin_headers)
    assert verif_expired.status_code == 200
    assert verif_expired.json()["valido"] is False
    assert "30 días" in verif_expired.json()["motivo_invalido"] or "plazo" in verif_expired.json()["motivo_invalido"].lower()

    # Intentar ejecutar cambio sobre venta expirada debe retornar 400
    fail_exchange = client.post("/api/v1/cambios", json={
        "numero_recibo": numero_recibo,
        "motivo": "Cambio de talla por M",
        "items": [
            {
                "variante_devuelta_id": var_s_id,
                "cantidad_devuelta": 1,
                "variante_entregada_id": var_m_id,
                "cantidad_entregada": 1
            }
        ]
    }, headers=admin_headers)
    assert fail_exchange.status_code == 400
    assert "expirado" in fail_exchange.json()["detail"] or "plazo" in fail_exchange.json()["detail"].lower()

    # 8. Restaurar fecha de venta a hoy (dentro del plazo de 30 días)
    venta_db.created_at = datetime.now(timezone.utc)
    db_session.commit()

    # 9. Test: Ejecutar cambio exitoso: Devuelve 1 unidad de S (valor 100) y se lleva 1 unidad de M (valor 110)
    # Diferencia = 110 - 100 = 10.0 a pagar por el cliente
    exchange_resp = client.post("/api/v1/cambios", json={
        "numero_recibo": numero_recibo,
        "motivo": "Le quedó pequeña la talla S, cambia por talla M",
        "estado_prenda": "CON_ETIQUETA",
        "metodo_pago_diferencia": "EFECTIVO",
        "nota": "Cliente satisfecho con la talla M",
        "items": [
            {
                "variante_devuelta_id": var_s_id,
                "cantidad_devuelta": 1,
                "variante_entregada_id": var_m_id,
                "cantidad_entregada": 1
            }
        ]
    }, headers=admin_headers)
    assert exchange_resp.status_code == 201
    cambio_data = exchange_resp.json()
    assert cambio_data["numero_cambio"].startswith("CAM-")
    assert cambio_data["monto_total_devuelto"] == 100.0
    assert cambio_data["monto_total_entregado"] == 110.0
    assert cambio_data["diferencia"] == 10.0
    assert cambio_data["metodo_pago_diferencia"] == "EFECTIVO"
    assert len(cambio_data["detalles"]) == 1

    # 10. Verificar consistencia de stock en inventario tras el cambio:
    # Talla S: tenía 3, reingresó 1 -> Ahora tiene 4
    # Talla M: tenía 5, salió 1 entregada -> Ahora tiene 4
    db_session.refresh(inv_s)
    db_session.refresh(inv_m)
    assert inv_s.stock_actual == 4
    assert inv_m.stock_actual == 4

    # 11. Test: Comprobar remanente disponible para cambio:
    # Se compraron 2, ya se cambió 1 -> Solo queda 1 disponible para cambiar
    verif_remanente = client.get(f"/api/v1/cambios/verificar-recibo/{numero_recibo}", headers=admin_headers)
    assert verif_remanente.status_code == 200
    assert verif_remanente.json()["items"][0]["cantidad_comprada"] == 2
    assert verif_remanente.json()["items"][0]["cantidad_ya_cambiada"] == 1
    assert verif_remanente.json()["items"][0]["cantidad_disponible_cambio"] == 1

    # 12. Test: Intentar cambiar 2 unidades cuando solo queda 1 debe fallar con 400
    exceso_resp = client.post("/api/v1/cambios", json={
        "numero_recibo": numero_recibo,
        "motivo": "Cambio no permitido",
        "items": [
            {
                "variante_devuelta_id": var_s_id,
                "cantidad_devuelta": 2,
                "variante_entregada_id": var_m_id,
                "cantidad_entregada": 2
            }
        ]
    }, headers=admin_headers)
    assert exceso_resp.status_code == 400
    assert "excede el remanente disponible" in exceso_resp.json()["detail"]

    # 13. Test: Listar historial de cambios
    list_resp = client.get("/api/v1/cambios", headers=admin_headers)
    assert list_resp.status_code == 200
    historial = list_resp.json()
    assert len(historial) >= 1
    assert historial[0]["numero_cambio"] == cambio_data["numero_cambio"]
