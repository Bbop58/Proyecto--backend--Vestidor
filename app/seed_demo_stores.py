"""Fictional menswear stores and their historical counter sales."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from random import Random
from uuid import NAMESPACE_URL, uuid5

from app.core.security import get_password_hash
from app.models import (
    Branch, Category, DeliveryRate, Inventory, InventoryMovement, MovementType,
    PaymentMethod, Product, ProductVariant, Role, Sale, SaleDetail, SaleStatus,
    SaleType, Subscription, Tenant, User,
)


STORES = (
    {
        "slug": "norteandino", "name": "Norte Andino", "code": "NAN",
        "colors": ("#123c35", "#408673"), "slogan": "Ropa urbana para el frío andino",
        "professional": True,
        "branches": (("La Paz", "Sopocachi", 14), ("El Alto", "Ciudad Satélite", 10),
                     ("Oruro", "Centro", 8)),
        "volume": (0, 0, 2, 3, 5, 8, 7, 4, 2, 1),
        "products": (
            ("BOM", "Chaqueta bomber Altiplano", "Abrigos", "Verde olivo", 390, "chaqueta_bomber_ma1.jpg"),
            ("TRK", "Cazadora denim Illimani", "Abrigos", "Azul índigo", 340, "cazadora_denim_trucker.jpg"),
            ("HUD", "Polerón térmico Sajama", "Polerones", "Negro", 260, "hoodie_heavyweight_canguro.jpg"),
            ("CRG", "Pantalón cargo Cordillera", "Pantalones", "Verde olivo", 290, "pantalon_cargo_tactico.jpg"),
            ("JNS", "Jeans rectos Puna", "Pantalones", "Azul índigo", 245, "jeans_baggy_skate.jpg"),
        ),
    },
    {
        "slug": "corteclasico", "name": "Corte Clásico", "code": "CCL",
        "colors": ("#24213d", "#866632"), "slogan": "Ropa formal masculina para cada ocasión",
        "professional": False,
        "branches": (("Sucre", "Centro Histórico", 13), ("Potosí", "San Roque", 8)),
        "volume": (3, 4, 1, 0, 2, 0, 1, 2, 4, 6),
        "products": (
            ("LIN", "Camisa de lino Capitolio", "Camisas", "Blanco", 240, "camisa_casual_lino_mao.jpg"),
            ("FLN", "Sobrecamisa Reserva", "Camisas", "Gris", 265, "sobrecamisa_franela_lenadora.jpg"),
            ("CSL", "Camisa cuello cubano Gran Salón", "Camisas", "Beige", 230, "camisa_resort_cubana.jpg"),
            ("CHI", "Pantalón chino Gran Vía", "Pantalones", "Beige", 295, "pantalon_chino_slim.jpg"),
            ("CRW", "Suéter cuello redondo León", "Suéteres", "Azul marino", 275, "poleron_crewneck_minimalist.jpg"),
        ),
    },
)


def demo_id(slug, key):
    return uuid5(NAMESPACE_URL, f"fashionstore:demo:store:{slug}:{key}")


def _add(db, row):
    db.add(row)
    db.flush()
    return row


def _seed_store(db, spec, plan, contract_day):
    slug, name, code = (spec[key] for key in ("slug", "name", "code"))
    tenant = db.query(Tenant).filter_by(slug=slug).one_or_none()
    tenant_id = demo_id(slug, "tenant")
    if tenant is None:
        tenant = _add(db, Tenant(id=tenant_id, slug=slug, nombre=name,
            razon_social=f"{name} Demo", nit_rut=f"DEMO-{code}",
            logo_url=f"/static/brands/{slug}.svg", color_primario=spec["colors"][0],
            color_secundario=spec["colors"][1], eslogan=spec["slogan"],
            email_contacto=f"contacto@{slug}.example", activo=True))
    if tenant.id != tenant_id or tenant.es_operadora:
        raise ValueError(f"El identificador de tienda demo {slug} ya pertenece a otra empresa")

    if not db.query(Subscription.id).filter_by(tenant_id=tenant_id).first():
        _add(db, Subscription(tenant_id=tenant_id, plan_id=plan.id, estado="ACTIVA",
            fecha_inicio=contract_day, fecha_vencimiento=contract_day + timedelta(days=365),
            notas="Suscripción anual de demostración"))

    password_hash = None
    roles = {name: db.query(Role).filter_by(name=name, tenant_id=None).one()
             for name in ("admin", "encargado", "cajero", "cliente")}

    def account(alias, role, label):
        nonlocal password_hash
        email = f"{alias}@{slug}.example"
        user = db.query(User).filter_by(email=email).one_or_none()
        if user:
            if user.tenant_id != tenant_id:
                raise ValueError(f"La cuenta demo {email} pertenece a otra empresa")
            return user
        if password_hash is None:
            password_hash = get_password_hash("Password123!")
        return _add(db, User(id=demo_id(slug, email), tenant_id=tenant_id,
            role_id=roles[role].id, email=email, full_name=label,
            hashed_password=password_hash, is_active=True, is_verified=True,
            created_at=datetime(2025, 12, 15, 16, tzinfo=timezone.utc)))

    account("admin", "admin", f"Administrador {name}")
    account("encargado", "encargado", f"Encargado {name}")
    buyers = [account("cliente", "cliente", f"Cliente {name}")]
    buyers += [account(f"cliente{i:02d}", "cliente", f"Cliente demo {name} {i:02d}")
               for i in range(2, 19)]

    variants = []
    for item, label, category_name, color, price, image in spec["products"]:
        category_id = demo_id(slug, f"category:{category_name}")
        category = db.get(Category, category_id)
        if category is None:
            category = _add(db, Category(id=category_id, tenant_id=tenant_id,
                nombre=category_name, descripcion="Ropa masculina", activa=True))
        product_id = demo_id(slug, f"product:{item}")
        product = db.get(Product, product_id)
        if product is None:
            product = _add(db, Product(id=product_id, tenant_id=tenant_id,
                categoria_id=category.id, nombre=label, descripcion=f"{label} para hombre",
                precio_base=price, imagen_url=f"/static/products/{image}", activo=True))
        if category.tenant_id != tenant_id or product.tenant_id != tenant_id:
            raise ValueError(f"Catálogo demo incompatible para {slug}")
        for size, extra in (("S", 0), ("M", 0), ("L", 10)):
            sku = f"{code}-{item}-{size}"
            variant = db.query(ProductVariant).filter_by(sku=sku).one_or_none()
            if variant is None:
                variant = _add(db, ProductVariant(producto_id=product.id, sku=sku,
                    talla=size, color=color, precio_extra=extra, activo=True))
            if variant.producto_id != product.id:
                raise ValueError(f"El SKU demo {sku} pertenece a otro producto")
            variants.append((variant.id, Decimal(price + extra)))

    for number, (city, district, base_volume) in enumerate(spec["branches"], start=1):
        branch_id = demo_id(slug, f"branch:{number}")
        branch = db.get(Branch, branch_id)
        if branch is None:
            branch = _add(db, Branch(id=branch_id, tenant_id=tenant_id,
                nombre=f"{name} · {district}", ciudad=city,
                direccion=f"Zona {district} (sucursal demo)", activa=True))
        if branch.tenant_id != tenant_id:
            raise ValueError(f"La sucursal demo de {slug} pertenece a otra empresa")
        cashier_alias = "cajero" if number == 1 else f"cajero{number}"
        cashier = account(cashier_alias, "cajero", f"Cajero {name} · {district}")
        if not db.query(DeliveryRate.id).filter_by(
            tenant_id=tenant_id, branch_id=branch.id, zona_nombre=district
        ).first():
            _add(db, DeliveryRate(tenant_id=tenant_id, branch_id=branch.id,
                zona_nombre=district, ciudad=city, costo_envio=15, tiempo_estimado_horas=24))
        _seed_branch_sales(db, spec, branch, cashier, buyers, variants, number, base_volume)


def _seed_branch_sales(db, spec, branch, cashier, buyers, variants, number, base_volume):
    code = spec["code"]
    rng = Random(f"{code}-{number}-2026")
    history = []
    demand = Counter()
    for month, increase in enumerate(spec["volume"], start=1):
        weights = [4, 4, 4] + [2] * 12
        if spec["professional"] and 5 <= month <= 8:
            weights[:9] = [5] * 9
        for seq in range(base_volume + increase):
            day = 1 + 7 * (seq % 4) + rng.randrange(7)
            when = datetime(2026, month, day, rng.randrange(13, 23), rng.randrange(60), tzinfo=timezone.utc)
            variant_id, price = rng.choices(variants, weights=weights)[0]
            buyer = rng.choices(buyers, weights=range(len(buyers), 0, -1))[0]
            quantity = rng.choices((1, 2, 3), weights=(10, 4, 1))[0]
            receipt = f"VTA-{code}-2026{month:02d}-{number}-{seq+1:03d}"
            history.append((when, receipt, variant_id, price, buyer.id, quantity))
            demand[variant_id] += quantity

    stock = {}
    for index, (variant_id, _) in enumerate(variants):
        inv = db.query(Inventory).filter_by(sucursal_id=branch.id, variante_id=variant_id).one_or_none()
        if inv is None:
            initial = demand[variant_id] + 3 + (index * 7 + number) % 27
            inv = _add(db, Inventory(tenant_id=branch.tenant_id, sucursal_id=branch.id,
                variante_id=variant_id, stock_actual=initial, stock_minimo=5, activo=True))
            db.add(InventoryMovement(tenant_id=branch.tenant_id, inventario_id=inv.id,
                tipo=MovementType.ENTRADA, cantidad=initial, stock_antes=0,
                stock_despues=initial, referencia=f"DEMO-APERTURA-{code}",
                created_at=datetime(2025, 12, 31, 16, tzinfo=timezone.utc)))
        if inv.tenant_id != branch.tenant_id:
            raise ValueError(f"Inventario demo de {code} pertenece a otra empresa")
        stock[variant_id] = inv

    for when, receipt, variant_id, price, buyer_id, quantity in sorted(history):
        existing = db.query(Sale.tenant_id, Sale.sucursal_id).filter_by(numero_recibo=receipt).one_or_none()
        if existing:
            if tuple(existing) != (branch.tenant_id, branch.id):
                raise ValueError(f"El recibo demo {receipt} pertenece a otra sucursal")
            continue
        inv = stock[variant_id]
        before = inv.stock_actual
        if before - inv.stock_reservado < quantity:
            raise ValueError(f"Stock disponible insuficiente para sembrar el recibo {receipt}")
        total = price * quantity
        tax = (total * Decimal("0.13")).quantize(Decimal("0.01"))
        received = ((int(total) + 49) // 50) * 50
        sale = _add(db, Sale(tenant_id=branch.tenant_id, sucursal_id=branch.id,
            numero_recibo=receipt, cliente_id=buyer_id, cajero_id=cashier.id,
            tipo=SaleType.PRESENCIAL, estado=SaleStatus.COMPLETADA, metodo_pago=PaymentMethod.EFECTIVO,
            monto_subtotal=total, monto_total=total, impuesto_iva=tax, monto_neto=total-tax,
            monto_recibido=received, cambio=received-total, nota="Venta histórica ficticia de demostración",
            created_at=when, updated_at=when))
        inv.stock_actual -= quantity
        db.add(SaleDetail(venta_id=sale.id, variante_id=variant_id, cantidad=quantity,
            precio_unitario=price, subtotal=total, created_at=when, updated_at=when))
        db.add(InventoryMovement(tenant_id=branch.tenant_id, inventario_id=inv.id,
            tipo=MovementType.SALIDA_VENTA, cantidad=quantity, stock_antes=before,
            stock_despues=inv.stock_actual, referencia=receipt, usuario_id=cashier.id, created_at=when))
    db.flush()


def seed_demo_stores(db, professional_plan, basic_plan, contract_day):
    for spec in STORES:
        plan = professional_plan if spec["professional"] else basic_plan
        _seed_store(db, spec, plan, contract_day)
