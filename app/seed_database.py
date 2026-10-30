import sys
import os
import uuid
import random
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, engine, Base
from app.core.security import get_password_hash
from app.models.role import Role
from app.models.permission import Permission
from app.models.user import User
from app.models.branch import Branch
from app.models.category import Category
from app.models.season import Season
from app.models.supplier import Supplier
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.reservation import Reservation, ReservationStatus
from app.models.reservation_detail import ReservationDetail
from app.models.sale import Sale, SaleType, SaleStatus, PaymentMethod
from app.models.sale_detail import SaleDetail
from app.models.tenant import Tenant
from app.models.subscription_plan import SubscriptionPlan
from app.models.subscription import Subscription
from app.models.favorite import Favorite
from app.models.delivery_rate import DeliveryRate
from app.models.product_exchange import ProductExchange
from app.models.product_exchange_detail import ProductExchangeDetail
from app.seed_permissions import seed as seed_permissions


def utc_now():
    return datetime.now(timezone.utc)


def seed_historical_sales_and_reservations(
    db,
    tenant_urban,
    tenant_moda,
    branches_map,
    users_map,
    all_variants,
    moda_variants
):
    print("\n[7/7] Sembrando Historial Equitativo por Sucursal y Mes (Enero - Octubre 2026)...")

    existing_sales_count = db.query(Sale).count()
    if existing_sales_count >= 100:
        print(f"  [+] Ya existen {existing_sales_count} ventas en base de datos. Se conserva el historial.")
        return

    random.seed(42)

    cliente_role = db.query(Role).filter(Role.name == "cliente").first()
    if not cliente_role:
        return

    admin_user = users_map.get("admin@ficttstore.com") or users_map.get("admin@urbanstyle.com")
    cajero_urban_central = users_map.get("cajero@ficttstore.com")
    cajero_urban_general = users_map.get("cajero@urbanstyle.com") or cajero_urban_central
    cajero_moda = users_map.get("cajero@modaexpress.com")

    cached_hash = get_password_hash("Password123!")

    # 1. Clientes para UrbanStyle (35 clientes)
    urban_customer_names = [
        ("Mateo Fernández", "mateo.fernandez@gmail.com"),
        ("Lucas Rojas", "lucas.rojas@gmail.com"),
        ("Alejandro Vargas", "alejandro.vargas@gmail.com"),
        ("Diego Castro", "diego.castro@gmail.com"),
        ("Carlos Mendoza", "carlos.mendoza@gmail.com"),
        ("Valeria Paz", "valeria.paz@gmail.com"),
        ("Gabriel Suárez", "gabriel.suarez@gmail.com"),
        ("Andrea Soliz", "andrea.soliz@gmail.com"),
        ("Rodrigo Torrico", "rodrigo.torrico@gmail.com"),
        ("Camila Montero", "camila.montero@gmail.com"),
        ("Sebastián Flores", "sebastian.flores@gmail.com"),
        ("Mariana Ayala", "mariana.ayala@gmail.com"),
        ("Nicolás Balcázar", "nicolas.balcazar@gmail.com"),
        ("Fernanda Salazar", "fernanda.salazar@gmail.com"),
        ("Luis Guzmán", "luis.guzman@gmail.com"),
        ("Daniela Terrazas", "daniela.terrazas@gmail.com"),
        ("Joaquín Aguilera", "joaquin.aguilera@gmail.com"),
        ("Paola Arce", "paola.arce@gmail.com"),
        ("Hugo Claros", "hugo.claros@gmail.com"),
        ("Natalia Justiniano", "natalia.justiniano@gmail.com"),
        ("Pablo Calvimontes", "pablo.calvimontes@gmail.com"),
        ("Carolina Serrano", "carolina.serrano@gmail.com"),
        ("Bruno Delgado", "bruno.delgado@gmail.com"),
        ("Patricia Cabrera", "patricia.cabrera@gmail.com"),
        ("Mauricio Zambrana", "mauricio.zambrana@gmail.com"),
        ("Claudia Escalante", "claudia.escalante@gmail.com"),
        ("Javier Banzer", "javier.banzer@gmail.com"),
        ("Silvia Chávez", "silvia.chavez@gmail.com"),
        ("Esteban Rivero", "esteban.rivero@gmail.com"),
        ("Adriana Ribera", "adriana.ribera@gmail.com"),
        ("Felipe Mercado", "felipe.mercado@gmail.com"),
        ("Luciana Villarroel", "luciana.villarroel@gmail.com"),
        ("Ignacio Peinado", "ignacio.peinado@gmail.com"),
        ("Julio César Prado", "julio.prado@gmail.com"),
        ("Lorena Justiniano", "lorena.justiniano@gmail.com"),
    ]

    urban_customers = []
    for full_name, email in urban_customer_names:
        u = db.query(User).filter(User.email == email).first()
        if not u:
            u = User(
                email=email,
                full_name=full_name,
                role_id=cliente_role.id,
                hashed_password=cached_hash,
                is_active=True,
                is_verified=True,
                tenant_id=tenant_urban.id
            )
            db.add(u)
            db.flush()
        urban_customers.append(u)

    # Clientes para Moda Express (8 clientes)
    moda_customer_names = [
        ("Óscar Valdivia Tarija", "oscar.valdivia.tja@gmail.com"),
        ("María Álvarez Tarija", "maria.alvarez.tja@gmail.com"),
        ("Pedro Gutiérrez Tarija", "pedro.gutierrez.tja@gmail.com"),
        ("Lucía Farfán Tarija", "lucia.farfan.tja@gmail.com"),
        ("Gonzalo Valdez Tarija", "gonzalo.valdez.tja@gmail.com"),
        ("Elena Cardoso Tarija", "elena.cardoso.tja@gmail.com"),
        ("Marcelo Villegas Tarija", "marcelo.villegas.tja@gmail.com"),
        ("Renata Barrientos Tarija", "renata.barrientos.tja@gmail.com"),
    ]

    moda_customers = []
    for full_name, email in moda_customer_names:
        u = db.query(User).filter(User.email == email).first()
        if not u:
            u = User(
                email=email,
                full_name=full_name,
                role_id=cliente_role.id,
                hashed_password=cached_hash,
                is_active=True,
                is_verified=True,
                tenant_id=tenant_moda.id
            )
            db.add(u)
            db.flush()
        moda_customers.append(u)

    db.commit()
    print(f"  [OK] Clientes registrados: {len(urban_customers)} UrbanStyle y {len(moda_customers)} Moda Express.")

    # 2. Refuerzo de Stock Inicial en Inventario
    print("  -> Reforzando stock inicial y registrando reabastecimiento en kardex...")
    inv_map = {}
    for inv in db.query(Inventory).all():
        inv_map[(inv.sucursal_id, inv.variante_id)] = inv
        if inv.stock_actual < 300:
            diff = 350 - inv.stock_actual
            inv.stock_actual = 350
            mov_ref = InventoryMovement(
                tenant_id=inv.tenant_id,
                inventario_id=inv.id,
                tipo=MovementType.ENTRADA,
                cantidad=diff,
                stock_antes=inv.stock_actual - diff,
                stock_despues=350,
                referencia="REABASTECIMIENTO-ANUAL-2026",
                nota="Carga de inventario de seguridad y reposición anual",
                usuario_id=admin_user.id if admin_user else None,
                created_at=datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)
            )
            db.add(mov_ref)
    db.commit()

    # 3. Agrupar variantes por categoría para estacionalidad
    variants_by_cat = {}
    for v in all_variants:
        cat_name = v.producto.categoria.nombre if (v.producto and v.producto.categoria) else "General"
        variants_by_cat.setdefault(cat_name, []).append(v)

    # Configuración equitativa por sucursales
    urban_branch_configs = [
        {"branch": branches_map.get("Sucursal Central"), "target": 28, "cajero": cajero_urban_central, "code": "CEN"},
        {"branch": branches_map.get("Sucursal Equipetrol"), "target": 24, "cajero": cajero_urban_general, "code": "EQU"},
        {"branch": branches_map.get("Sucursal Cochabamba"), "target": 20, "cajero": cajero_urban_central, "code": "CBB"},
        {"branch": branches_map.get("Sucursal Montero"), "target": 16, "cajero": cajero_urban_general, "code": "MON"},
    ]
    moda_branch_configs = [
        {"branch": branches_map.get("Moda Express - Tarija Centro"), "target": 14, "cajero": cajero_moda, "code": "TJC"},
        {"branch": branches_map.get("Moda Express - El Molino"), "target": 11, "cajero": cajero_moda, "code": "MLN"},
    ]

    bill_denominations = [20, 50, 100, 150, 200, 250, 300, 400, 500, 600, 700, 800, 1000]

    total_sales_created = 0
    total_reservations_created = 0

    print("  -> Generando ventas y reservas mes a mes (Enero a Octubre 2026)...")

    for month in range(1, 11):
        month_seq = 1
        
        # Ponderación estacional para UrbanStyle
        if month in [1, 2]:
            cat_weights = [("Shorts & Bermudas", 0.40), ("Poleras & Remeras", 0.40), ("Camisas & Sobrecamisas", 0.15), ("Pantalones & Jeans", 0.05)]
        elif month in [3, 4]:
            cat_weights = [("Pantalones & Jeans", 0.35), ("Camisas & Sobrecamisas", 0.30), ("Poleras & Remeras", 0.25), ("Hoodies & Polerones", 0.10)]
        elif month in [5, 6, 7]:
            cat_weights = [("Hoodies & Polerones", 0.45), ("Chaquetas & Abrigos", 0.35), ("Pantalones & Jeans", 0.20)]
        else: # 8, 9, 10
            cat_weights = [("Poleras & Remeras", 0.35), ("Shorts & Bermudas", 0.25), ("Chaquetas & Abrigos", 0.20), ("Pantalones & Jeans", 0.20)]

        cat_names = [cw[0] for cw in cat_weights]
        cat_probs = [cw[1] for cw in cat_weights]

        # --- A. VENTAS URBANSTYLE ---
        for b_cfg in urban_branch_configs:
            branch = b_cfg["branch"]
            if not branch:
                continue

            for _ in range(b_cfg["target"]):
                day = random.randint(1, 28)
                hour = random.choice([10, 11, 12, 14, 15, 16, 17, 18, 19, 20])
                minute = random.randint(0, 59)
                second = random.randint(0, 59)
                sale_date = datetime(2026, month, day, hour, minute, second, tzinfo=timezone.utc)

                is_presencial = random.random() < 0.70
                sale_type = SaleType.PRESENCIAL if is_presencial else SaleType.DIGITAL
                cajero = b_cfg["cajero"] if is_presencial else None

                if is_presencial:
                    metodo_pago = PaymentMethod.EFECTIVO
                    ref_pago = None
                else:
                    metodo_pago = PaymentMethod.PAYPAL
                    ref_pago = f"PAYPAL_CAPTURE_{month:02d}{month_seq:04d}E"

                cliente = random.choice(urban_customers) if random.random() < 0.65 else None
                receipt_no = f"VTA-2026{month:02d}-{month_seq:04d}-{b_cfg['code']}"

                num_items = random.choices([1, 2, 3], weights=[0.55, 0.35, 0.10])[0]
                selected_variants = []
                for _ in range(num_items):
                    chosen_cat = random.choices(cat_names, weights=cat_probs)[0]
                    pool = variants_by_cat.get(chosen_cat) or all_variants
                    if pool:
                        selected_variants.append(random.choice(pool))

                if not selected_variants:
                    selected_variants = [random.choice(all_variants)]

                monto_total = Decimal("0.00")
                sale_items_data = []
                for v in selected_variants:
                    qty = random.choices([1, 2], weights=[0.85, 0.15])[0]
                    price = v.producto.precio_base + v.precio_extra
                    subtot = price * qty
                    monto_total += subtot
                    sale_items_data.append((v, qty, price, subtot))

                impuesto_iva = round(monto_total * Decimal("0.13"), 2)
                monto_neto = monto_total - impuesto_iva

                if metodo_pago == PaymentMethod.EFECTIVO:
                    tot_int = int(monto_total)
                    opts = [b for b in bill_denominations if b >= tot_int]
                    monto_recibido = Decimal(opts[0]) if opts else monto_total
                    cambio = monto_recibido - monto_total
                else:
                    monto_recibido = monto_total
                    cambio = Decimal("0.00")

                new_sale = Sale(
                    tenant_id=tenant_urban.id,
                    numero_recibo=receipt_no,
                    tipo=sale_type,
                    estado=SaleStatus.COMPLETADA,
                    sucursal_id=branch.id,
                    cliente_id=cliente.id if cliente else None,
                    cajero_id=cajero.id if cajero else None,
                    metodo_pago=metodo_pago,
                    referencia_pago=ref_pago,
                    monto_total=monto_total,
                    impuesto_iva=impuesto_iva,
                    monto_neto=monto_neto,
                    monto_recibido=monto_recibido,
                    cambio=cambio,
                    nota=f"Venta {'mostrador' if is_presencial else 'digital'} - {branch.nombre}",
                    created_at=sale_date,
                    updated_at=sale_date
                )
                db.add(new_sale)
                db.flush()

                for var, qty, pr, st in sale_items_data:
                    det = SaleDetail(
                        venta_id=new_sale.id,
                        variante_id=var.id,
                        cantidad=qty,
                        precio_unitario=pr,
                        subtotal=st,
                        created_at=sale_date
                    )
                    db.add(det)

                    inv = inv_map.get((branch.id, var.id))
                    if inv:
                        antes = inv.stock_actual
                        inv.stock_actual = max(0, inv.stock_actual - qty)
                        mov = InventoryMovement(
                            tenant_id=tenant_urban.id,
                            inventario_id=inv.id,
                            tipo=MovementType.SALIDA_VENTA,
                            cantidad=qty,
                            stock_antes=antes,
                            stock_despues=inv.stock_actual,
                            referencia=receipt_no,
                            nota=f"Venta en {branch.nombre}",
                            usuario_id=cajero.id if cajero else (admin_user.id if admin_user else None),
                            created_at=sale_date
                        )
                        db.add(mov)

                month_seq += 1
                total_sales_created += 1

        # --- B. VENTAS MODA EXPRESS ---
        for b_cfg in moda_branch_configs:
            branch = b_cfg["branch"]
            if not branch:
                continue

            for _ in range(b_cfg["target"]):
                day = random.randint(1, 28)
                hour = random.choice([10, 11, 14, 15, 16, 17, 18, 19])
                minute = random.randint(0, 59)
                second = random.randint(0, 59)
                sale_date = datetime(2026, month, day, hour, minute, second, tzinfo=timezone.utc)

                is_presencial = random.random() < 0.80
                sale_type = SaleType.PRESENCIAL if is_presencial else SaleType.DIGITAL
                cajero = b_cfg["cajero"] if is_presencial else None

                if is_presencial:
                    metodo_pago = PaymentMethod.EFECTIVO
                    ref_pago = None
                else:
                    metodo_pago = PaymentMethod.PAYPAL
                    ref_pago = f"PAYPAL_MODA_{month:02d}_{month_seq:04d}"
                cliente = random.choice(moda_customers) if random.random() < 0.60 else None
                receipt_no = f"VTA-MODA-{month:02d}-{month_seq:04d}-{b_cfg['code']}"

                var = random.choice(moda_variants)
                qty = 1
                price = var.producto.precio_base + var.precio_extra
                subtot = price * qty
                monto_total = subtot

                impuesto_iva = round(monto_total * Decimal("0.13"), 2)
                monto_neto = monto_total - impuesto_iva

                if metodo_pago == PaymentMethod.EFECTIVO:
                    tot_int = int(monto_total)
                    opts = [b for b in bill_denominations if b >= tot_int]
                    monto_recibido = Decimal(opts[0]) if opts else monto_total
                    cambio = monto_recibido - monto_total
                else:
                    monto_recibido = monto_total
                    cambio = Decimal("0.00")

                new_sale = Sale(
                    tenant_id=tenant_moda.id,
                    numero_recibo=receipt_no,
                    tipo=sale_type,
                    estado=SaleStatus.COMPLETADA,
                    sucursal_id=branch.id,
                    cliente_id=cliente.id if cliente else None,
                    cajero_id=cajero.id if cajero else None,
                    metodo_pago=metodo_pago,
                    referencia_pago=ref_pago,
                    monto_total=monto_total,
                    impuesto_iva=impuesto_iva,
                    monto_neto=monto_neto,
                    monto_recibido=monto_recibido,
                    cambio=cambio,
                    nota=f"Venta Moda Express - {branch.nombre}",
                    created_at=sale_date,
                    updated_at=sale_date
                )
                db.add(new_sale)
                db.flush()

                det = SaleDetail(
                    venta_id=new_sale.id,
                    variante_id=var.id,
                    cantidad=qty,
                    precio_unitario=price,
                    subtotal=subtot,
                    created_at=sale_date
                )
                db.add(det)

                inv = inv_map.get((branch.id, var.id))
                if inv:
                    antes = inv.stock_actual
                    inv.stock_actual = max(0, inv.stock_actual - qty)
                    mov = InventoryMovement(
                        tenant_id=tenant_moda.id,
                        inventario_id=inv.id,
                        tipo=MovementType.SALIDA_VENTA,
                        cantidad=qty,
                        stock_antes=antes,
                        stock_despues=inv.stock_actual,
                        referencia=receipt_no,
                        nota=f"Venta en {branch.nombre}",
                        usuario_id=cajero.id if cajero else None,
                        created_at=sale_date
                    )
                    db.add(mov)

                month_seq += 1
                total_sales_created += 1

        # --- C. RESERVAS POR SUCURSAL Y MES ---
        for b_cfg in (urban_branch_configs + moda_branch_configs):
            branch = b_cfg["branch"]
            if not branch:
                continue

            tenant = tenant_urban if b_cfg in urban_branch_configs else tenant_moda
            cust_pool = urban_customers if b_cfg in urban_branch_configs else moda_customers
            var_pool = all_variants if b_cfg in urban_branch_configs else moda_variants

            num_res = 2 if b_cfg in urban_branch_configs else 1
            for r_idx in range(num_res):
                r_day = random.randint(1, 25)
                r_hour = random.randint(10, 18)
                res_created = datetime(2026, month, r_day, r_hour, 0, tzinfo=timezone.utc)
                res_cli = random.choice(cust_pool)
                res_code = f"RES-2026{month:02d}-{b_cfg['code']}-{r_idx+1}"

                if month == 10:
                    estado_res = ReservationStatus.PREPARADA if r_idx == 0 else ReservationStatus.PENDIENTE
                    exp_date = res_created + timedelta(days=3)
                else:
                    estado_res = random.choices(
                        [ReservationStatus.COMPLETADA, ReservationStatus.EXPIRADA, ReservationStatus.CANCELADA],
                        weights=[0.75, 0.15, 0.10]
                    )[0]
                    exp_date = res_created + timedelta(days=3)

                r_var = random.choice(var_pool)
                r_qty = 1
                r_price = r_var.producto.precio_base + r_var.precio_extra

                res = Reservation(
                    tenant_id=tenant.id,
                    codigo=res_code,
                    cliente_id=res_cli.id,
                    sucursal_id=branch.id,
                    estado=estado_res,
                    fecha_hora_esperada=res_created + timedelta(days=1),
                    fecha_expiracion=exp_date,
                    total_estimado=r_price,
                    nota=f"Reserva {estado_res.value.lower()} en {branch.nombre}",
                    activo=True,
                    created_at=res_created
                )
                db.add(res)
                db.flush()

                r_det = ReservationDetail(
                    reserva_id=res.id,
                    variante_id=r_var.id,
                    cantidad=r_qty,
                    precio_unitario=r_price,
                    subtotal=r_price
                )
                db.add(r_det)

                if estado_res in [ReservationStatus.PREPARADA, ReservationStatus.PENDIENTE]:
                    inv_r = inv_map.get((branch.id, r_var.id))
                    if inv_r:
                        inv_r.stock_reservado += r_qty

                total_reservations_created += 1

        db.commit()
        print(f"    -> Mes {month:02d}/2026 completado ({month_seq - 1} ventas sembradas).")

    print(f"  [SUCCESS] Sembrado histórico completado: {total_sales_created} ventas y {total_reservations_created} reservas generadas en 10 meses.")


def seed_delivery_rates_and_favorites(db, tenant_urban, tenant_moda, branches_map):
    print("\n[8/8] Sembrando Tarifas de Delivery y Favoritos de Clientes...")
    
    # 1. Delivery Rates
    existing_rates = db.query(DeliveryRate).count()
    if existing_rates == 0:
        rates_to_create = []
        for branch_name, branch in branches_map.items():
            if not branch or branch.tenant_id != tenant_urban.id:
                continue
            city = branch.ciudad or "Santa Cruz"
            if "Santa Cruz" in city:
                rates_to_create.extend([
                    DeliveryRate(
                        branch_id=branch.id,
                        tenant_id=tenant_urban.id,
                        zona_nombre="Zona Central y 1er Anillo",
                        ciudad=city,
                        costo_envio=Decimal("15.00"),
                        tiempo_estimado_horas=24,
                        activo=True
                    ),
                    DeliveryRate(
                        branch_id=branch.id,
                        tenant_id=tenant_urban.id,
                        zona_nombre="Equipetrol y Urbarí",
                        ciudad=city,
                        costo_envio=Decimal("20.00"),
                        tiempo_estimado_horas=24,
                        activo=True
                    ),
                    DeliveryRate(
                        branch_id=branch.id,
                        tenant_id=tenant_urban.id,
                        zona_nombre="Plan 3000 y Villa 1ro de Mayo",
                        ciudad=city,
                        costo_envio=Decimal("25.00"),
                        tiempo_estimado_horas=48,
                        activo=True
                    ),
                ])
            elif "La Paz" in city:
                rates_to_create.extend([
                    DeliveryRate(
                        branch_id=branch.id,
                        tenant_id=tenant_urban.id,
                        zona_nombre="Centro / Miraflores / Sopocachi",
                        ciudad=city,
                        costo_envio=Decimal("15.00"),
                        tiempo_estimado_horas=24,
                        activo=True
                    ),
                    DeliveryRate(
                        branch_id=branch.id,
                        tenant_id=tenant_urban.id,
                        zona_nombre="Zona Sur / Calacoto / San Miguel",
                        ciudad=city,
                        costo_envio=Decimal("20.00"),
                        tiempo_estimado_horas=24,
                        activo=True
                    ),
                ])
            else:
                rates_to_create.append(
                    DeliveryRate(
                        branch_id=branch.id,
                        tenant_id=tenant_urban.id,
                        zona_nombre=f"Radio Urbano {city}",
                        ciudad=city,
                        costo_envio=Decimal("18.00"),
                        tiempo_estimado_horas=24,
                        activo=True
                    )
                )
        

        # Tarifas de Delivery para Moda Express (Tarija)
        for branch_name, branch in branches_map.items():
            if not branch or branch.tenant_id != tenant_moda.id:
                continue
            rates_to_create.extend([
                DeliveryRate(
                    branch_id=branch.id,
                    tenant_id=tenant_moda.id,
                    zona_nombre=f"Radio Urbano - {branch.nombre}",
                    ciudad="Tarija",
                    costo_envio=Decimal("10.00"),
                    tiempo_estimado_horas=24,
                    activo=True
                ),
                DeliveryRate(
                    branch_id=branch.id,
                    tenant_id=tenant_moda.id,
                    zona_nombre="Zonas Aledañas y Periferia Tarija",
                    ciudad="Tarija",
                    costo_envio=Decimal("18.00"),
                    tiempo_estimado_horas=48,
                    activo=True
                ),
            ])

        for r in rates_to_create:
            db.add(r)
        db.commit()
        print(f"  [OK] Sembradas {len(rates_to_create)} tarifas de delivery.")
    else:
        print(f"  [+] Ya existen {existing_rates} tarifas de delivery registradas.")

    # 2. Favorites
    existing_favs = db.query(Favorite).count()
    if existing_favs == 0:
        clients = db.query(User).filter(User.tenant_id == tenant_urban.id).limit(5).all()
        products = db.query(Product).filter(Product.tenant_id == tenant_urban.id, Product.activo == True).limit(5).all()
        fav_count = 0
        for client in clients:
            for prod in products[:2]:
                fav = Favorite(
                    user_id=client.id,
                    product_id=prod.id,
                    tenant_id=tenant_urban.id
                )
                db.add(fav)
                fav_count += 1
        db.commit()
        print(f"  [OK] Sembrados {fav_count} favoritos iniciales para clientes.")
    else:
        print(f"  [+] Ya existen {existing_favs} favoritos registrados.")




def seed_product_exchanges(db, tenant_urban, branches_map, users_map):
    print("\n[9/9] Sembrando Registro de Cambios de Prendas (Garantías Retail)...")
    existing_exchanges = db.query(ProductExchange).count()
    if existing_exchanges > 0:
        print(f"  [+] Ya existen {existing_exchanges} cambios de prenda registrados.")
        return

    central_branch = branches_map.get("Sucursal Central")
    cajero = users_map.get("cajero@urbanstyle.com") or users_map.get("cajero@ficttstore.com")
    cliente = users_map.get("cliente@ficttstore.com")

    sale = db.query(Sale).filter(
        Sale.tenant_id == tenant_urban.id,
        Sale.sucursal_id == central_branch.id,
        Sale.estado == SaleStatus.COMPLETADA
    ).first()

    if not sale or not sale.detalles or not cajero:
        print("  [WARN] No se encontró venta base para sembrar cambio de prendas.")
        return

    det_devuelto = sale.detalles[0]
    var_devuelta = det_devuelto.variante

    var_entregada = db.query(ProductVariant).filter(
        ProductVariant.producto_id == var_devuelta.producto_id,
        ProductVariant.id != var_devuelta.id,
        ProductVariant.activo == True
    ).first()

    if not var_entregada:
        var_entregada = var_devuelta

    precio_dev = Decimal(str(det_devuelto.precio_unitario))
    prod_ent = var_entregada.producto
    precio_ent = prod_ent.precio_base + var_entregada.precio_extra
    diff = precio_ent - precio_dev

    numero_cambio = "CAM-20261015-0001"

    cambio = ProductExchange(
        tenant_id=tenant_urban.id,
        numero_cambio=numero_cambio,
        venta_id=sale.id,
        sucursal_id=central_branch.id,
        cajero_id=cajero.id,
        cliente_id=sale.cliente_id or (cliente.id if cliente else None),
        fecha_cambio=datetime(2026, 10, 15, 15, 30, tzinfo=timezone.utc),
        dias_transcurridos=3,
        monto_total_devuelto=precio_dev,
        monto_total_entregado=precio_ent,
        diferencia=diff,
        metodo_pago_diferencia="EFECTIVO" if diff > 0 else "NINGUNO",
        motivo="Cambio por talla más grande (ajuste de confección)",
        estado_prenda="BUEN_ESTADO",
        nota="Prenda devuelta intacta con etiquetas originales. Cambio presencial en caja."
    )
    db.add(cambio)
    db.flush()

    det_cambio = ProductExchangeDetail(
        cambio_id=cambio.id,
        variante_devuelta_id=var_devuelta.id,
        cantidad_devuelta=1,
        precio_unitario_devuelto=precio_dev,
        variante_entregada_id=var_entregada.id,
        cantidad_entregada=1,
        precio_unitario_entregado=precio_ent,
        subtotal_diferencia=diff
    )
    db.add(det_cambio)

    inv_dev = db.query(Inventory).filter(
        Inventory.sucursal_id == central_branch.id,
        Inventory.variante_id == var_devuelta.id
    ).first()
    if inv_dev:
        antes = inv_dev.stock_actual
        inv_dev.stock_actual += 1
        mov_in = InventoryMovement(
            tenant_id=tenant_urban.id,
            inventario_id=inv_dev.id,
            tipo=MovementType.DEVOLUCION,
            cantidad=1,
            stock_antes=antes,
            stock_despues=inv_dev.stock_actual,
            referencia=numero_cambio,
            nota="Reingreso por cambio de prenda: Talla devuelta",
            usuario_id=cajero.id,
            created_at=datetime(2026, 10, 15, 15, 30, tzinfo=timezone.utc)
        )
        db.add(mov_in)

    inv_ent = db.query(Inventory).filter(
        Inventory.sucursal_id == central_branch.id,
        Inventory.variante_id == var_entregada.id
    ).first()
    if inv_ent:
        antes_ent = inv_ent.stock_actual
        inv_ent.stock_actual = max(0, inv_ent.stock_actual - 1)
        mov_out = InventoryMovement(
            tenant_id=tenant_urban.id,
            inventario_id=inv_ent.id,
            tipo=MovementType.SALIDA_VENTA,
            cantidad=1,
            stock_antes=antes_ent,
            stock_despues=inv_ent.stock_actual,
            referencia=numero_cambio,
            nota="Salida por entrega de prenda en cambio de talla",
            usuario_id=cajero.id,
            created_at=datetime(2026, 10, 15, 15, 30, tzinfo=timezone.utc)
        )
        db.add(mov_out)

    db.commit()
    print(f"  [OK] Sembrado cambio de prenda de prueba: {numero_cambio} (Diferencia: {diff} Bs.)")


def seed_all():
    print("==================================================")
    print("Iniciando Sembrado Completo de la Base de Datos...")
    print("==================================================")
    
    # 0. Crear tablas si no existen y sincronizar columnas
    print("\n[0/6] Creando esquema de tablas en la base de datos...")
    Base.metadata.create_all(bind=engine)

    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS impuesto_iva NUMERIC(10, 2) DEFAULT 0.0 NOT NULL;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS monto_neto NUMERIC(10, 2) DEFAULT 0.0 NOT NULL;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS costo_envio NUMERIC(10, 2) DEFAULT 0.0;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS direccion_envio VARCHAR(300);
                UPDATE ventas 
                SET impuesto_iva = ROUND(monto_total * 0.13, 2),
                    monto_neto = ROUND(monto_total - ROUND(monto_total * 0.13, 2), 2)
                WHERE (monto_neto = 0 OR monto_neto IS NULL) AND monto_total > 0;
                
                -- Multitenancy columns
                ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;
                ALTER TABLE sucursales ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE productos ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE categorias ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE temporadas ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE inventario ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE reservas ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE movimientos_inventario ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE cambios_producto ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE favoritos ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE tarifas_delivery ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

                -- Limpieza y corrección de encoding en registros preexistentes
                UPDATE subscription_plans SET nombre = 'Plan Básico', descripcion = 'Máximo 2 sucursales, Operación Offline, Sin Reportes IA', precio_mensual_bob = 199.00 WHERE codigo = 'BASICO';
                UPDATE subscription_plans SET nombre = 'Plan Profesional', descripcion = 'Hasta 10 sucursales, Reportes con IA e Inteligencia Artificial', precio_mensual_bob = 499.00 WHERE codigo = 'PROFESIONAL';
                UPDATE categorias SET descripcion = 'Poleras oversized, boxy fit, remeras básicas y urbanas para hombre' WHERE nombre = 'Poleras & Remeras';
                UPDATE categorias SET descripcion = 'Camisas de lino, franela leñadora, sobrecamisas y estilo resort' WHERE nombre = 'Camisas & Sobrecamisas';
                UPDATE categorias SET descripcion = 'Pantalones cargo tácticos, jeans baggy 90s y pantalones chino stretch' WHERE nombre = 'Pantalones & Jeans';
                UPDATE productos SET activo = true WHERE tenant_id IN (SELECT id FROM tenants WHERE slug = 'modaexpress');
            """))
        print("  [OK] Columnas de impuestos, multitenant y envíos verificadas y migradas.")
    except Exception as e:
        print(f"  [WARN] No se requirió migración manual o aviso: {e}")

    # 1. Sembrar roles, permisos y temporadas
    print("\n[1/6] Sembrando Roles, Permisos y Temporadas base...")
    seed_permissions()

    db = SessionLocal()
    try:
        # 1.5. Sembrar Planes de Suscripción y Empresas Multitenant
        print("\n[1.5/6] Sembrando Planes de Suscripción y Empresas Multitenant...")
        plan_pro = db.query(SubscriptionPlan).filter(SubscriptionPlan.codigo == "PROFESIONAL").first()
        if not plan_pro:
            plan_pro = SubscriptionPlan(
                codigo="PROFESIONAL",
                nombre="Plan Profesional",
                descripcion="Hasta 10 sucursales, Reportes con IA e Inteligencia Artificial",
                max_sucursales=10,
                permite_reportes_ia=True,
                permite_offline=True,
                precio_mensual_bob=499.00,
                activo=True
            )
            db.add(plan_pro)
            db.flush()
        else:
            plan_pro.nombre = "Plan Profesional"
            plan_pro.descripcion = "Hasta 10 sucursales, Reportes con IA e Inteligencia Artificial"
            plan_pro.max_sucursales = 10
            plan_pro.permite_reportes_ia = True
            plan_pro.permite_offline = True
            plan_pro.precio_mensual_bob = 499.00
            plan_pro.activo = True

        plan_basico = db.query(SubscriptionPlan).filter(SubscriptionPlan.codigo == "BASICO").first()
        if not plan_basico:
            plan_basico = SubscriptionPlan(
                codigo="BASICO",
                nombre="Plan Básico",
                descripcion="Máximo 2 sucursales, Operación Offline, Sin Reportes IA",
                max_sucursales=2,
                permite_reportes_ia=False,
                permite_offline=True,
                precio_mensual_bob=199.00,
                activo=True
            )
            db.add(plan_basico)
            db.flush()
        else:
            plan_basico.nombre = "Plan Básico"
            plan_basico.descripcion = "Máximo 2 sucursales, Operación Offline, Sin Reportes IA"
            plan_basico.max_sucursales = 2
            plan_basico.permite_reportes_ia = False
            plan_basico.permite_offline = True
            plan_basico.precio_mensual_bob = 199.00
            plan_basico.activo = True
        db.commit()

        # Empresa 1: UrbanStyle Bolivia S.R.L. (Plan Profesional)
        tenant_urban = db.query(Tenant).filter(Tenant.slug == "urbanstyle").first()
        if not tenant_urban:
            tenant_urban = Tenant(
                nombre="UrbanStyle Bolivia S.R.L.",
                razon_social="UrbanStyle Bolivia Sociedad de Responsabilidad Limitada",
                nit_rut="1029384756",
                slug="urbanstyle",
                logo_url="https://images.unsplash.com/photo-1441986300917-64674bd600d8?w=200",
                color_primario="#4f46e5",
                color_secundario="#818cf8",
                eslogan="Streetwear & Urban Culture",
                telefono="+591 3 3456789",
                email_contacto="contacto@urbanstyle.com",
                activo=True
            )
            db.add(tenant_urban)
            db.flush()

            sub_urban = Subscription(
                tenant_id=tenant_urban.id,
                plan_id=plan_pro.id,
                estado="ACTIVA",
                fecha_inicio=date.today(),
                fecha_vencimiento=date.fromordinal(date.today().toordinal() + 365),
                notas="Suscripción Anual Profesional"
            )
            db.add(sub_urban)
            db.commit()
            print("  [OK] Creada Empresa 1: UrbanStyle Bolivia S.R.L. (Plan Profesional)")
        else:
            tenant_urban.color_primario = "#4f46e5"
            tenant_urban.color_secundario = "#818cf8"
            tenant_urban.eslogan = "Streetwear & Urban Culture"
            db.commit()

        # Empresa 2: Moda Express Tarija (Plan Básico)
        tenant_moda = db.query(Tenant).filter(Tenant.slug == "modaexpress").first()
        if not tenant_moda:
            tenant_moda = Tenant(
                nombre="Moda Express Tarija",
                razon_social="Moda Express Tarija Unipersonal",
                nit_rut="7463529018",
                slug="modaexpress",
                logo_url="https://images.unsplash.com/photo-1472851294608-062f824d29cc?w=200",
                color_primario="#d97706",
                color_secundario="#f59e0b",
                eslogan="Elegancia Formal y Distinción",
                telefono="+591 4 6645566",
                email_contacto="contacto@modaexpress.com",
                activo=True
            )
            db.add(tenant_moda)
            db.flush()

            sub_moda = Subscription(
                tenant_id=tenant_moda.id,
                plan_id=plan_basico.id,
                estado="ACTIVA",
                fecha_inicio=date.today(),
                fecha_vencimiento=date.fromordinal(date.today().toordinal() + 365),
                notas="Suscripción Anual Básica"
            )
            db.add(sub_moda)
            db.commit()
            print("  [OK] Creada Empresa 2: Moda Express Tarija (Plan Básico)")

        # 2. Sembrar Usuarios con diferentes Roles
        print("\n[2/6] Sembrando Usuarios de Prueba...")
        admin_role = db.query(Role).filter(Role.name == "admin").first()
        encargado_role = db.query(Role).filter(Role.name == "encargado").first()
        cajero_role = db.query(Role).filter(Role.name == "cajero").first()
        cliente_role = db.query(Role).filter(Role.name == "cliente").first()

        users_data = [
            {
                "email": "admin@ficttstore.com",
                "full_name": "Administrador General",
                "role_id": admin_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_urban.id
            },
            {
                "email": "superadmin@ficcstore.com",
                "full_name": "Super Administrador SaaS",
                "role_id": admin_role.id,
                "password": get_password_hash("SuperAdmin2026!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": None
            },
            {
                "email": "cliente@ficcstore.com",
                "full_name": "Cliente FICCT Store",
                "role_id": cliente_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_urban.id
            },
            {
                "email": "admin@sistema.com",
                "full_name": "Super Administrador",
                "role_id": admin_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_urban.id
            },
            {
                "email": "admin@urbanstyle.com",
                "full_name": "Admin UrbanStyle (Pro)",
                "role_id": admin_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_urban.id
            },
            {
                "email": "cajero@urbanstyle.com",
                "full_name": "Cajero UrbanStyle",
                "role_id": cajero_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_urban.id
            },
            {
                "email": "admin@modaexpress.com",
                "full_name": "Admin Moda Express (Básico)",
                "role_id": admin_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_moda.id
            },
            {
                "email": "cajero@modaexpress.com",
                "full_name": "Cajero Moda Express",
                "role_id": cajero_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_moda.id
            },
            {
                "email": "cliente@modaexpress.com",
                "full_name": "Valeria Morales Cliente",
                "role_id": cliente_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_moda.id
            },
            {
                "email": "encargado@ficttstore.com",
                "full_name": "Carlos Encargado Sucursal",
                "role_id": encargado_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_urban.id
            },
            {
                "email": "cajero@ficttstore.com",
                "full_name": "María Cajera Central",
                "role_id": cajero_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_urban.id
            },
            {
                "email": "cliente@ficttstore.com",
                "full_name": "Juan Pérez Cliente",
                "role_id": cliente_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_urban.id
            },
            {
                "email": "sofia.cliente@ficttstore.com",
                "full_name": "Sofía Morales",
                "role_id": cliente_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True,
                "tenant_id": tenant_urban.id
            }
        ]

        users_map = {}
        for u_data in users_data:
            user = db.query(User).filter(User.email == u_data["email"]).first()
            if not user:
                user = User(
                    email=u_data["email"],
                    full_name=u_data["full_name"],
                    role_id=u_data["role_id"],
                    hashed_password=u_data["password"],
                    is_active=u_data["is_active"],
                    is_verified=u_data["is_verified"],
                    tenant_id=u_data.get("tenant_id")
                )
                db.add(user)
                db.commit()
                db.refresh(user)
                print(f"  [OK] Creado usuario: {user.email} (Rol: {user.role.name})")
            else:
                user.role_id = u_data["role_id"]
                user.hashed_password = u_data["password"]
                user.full_name = u_data["full_name"]
                user.tenant_id = u_data.get("tenant_id")
                db.commit()
                print(f"  [+] Usuario actualizado: {user.email}")
            users_map[u_data["email"]] = user

        # 3. Sembrar Sucursales
        print("\n[3/6] Sembrando Sucursales...")
        branches_data = [
            {
                "nombre": "Sucursal Central",
                "ciudad": "Santa Cruz de la Sierra",
                "direccion": "Av. Banzer y 3er Anillo Interno #150",
                "telefono": "+591 3 3456789",
                "activa": True,
                "tenant_id": tenant_urban.id
            },
            {
                "nombre": "Sucursal Equipetrol",
                "ciudad": "Santa Cruz de la Sierra",
                "direccion": "Av. San Martín #450, Esq. Calle 7",
                "telefono": "+591 3 3498123",
                "activa": True,
                "tenant_id": tenant_urban.id
            },
            {
                "nombre": "Sucursal Montero",
                "ciudad": "Montero",
                "direccion": "Calle Comercio #120, Zona Comercial",
                "telefono": "+591 3 9223344",
                "activa": True,
                "tenant_id": tenant_urban.id
            },
            {
                "nombre": "Sucursal Cochabamba",
                "ciudad": "Cochabamba",
                "direccion": "Av. América Este #780",
                "telefono": "+591 4 4112233",
                "activa": True,
                "tenant_id": tenant_urban.id
            },
            # Sucursales Empresa 2 (Moda Express Tarija - Plan Básico con límite de 2)
            {
                "nombre": "Moda Express - Tarija Centro",
                "ciudad": "Tarija",
                "direccion": "Calle Sucre #240, Plaza Principal",
                "telefono": "+591 4 6641122",
                "activa": True,
                "tenant_id": tenant_moda.id
            },
            {
                "nombre": "Moda Express - El Molino",
                "ciudad": "Tarija",
                "direccion": "Av. Las Américas #890",
                "telefono": "+591 4 6643344",
                "activa": True,
                "tenant_id": tenant_moda.id
            }
        ]

        branches_map = {}
        for b_data in branches_data:
            branch = db.query(Branch).filter(Branch.nombre == b_data["nombre"]).first()
            if not branch:
                branch = Branch(**b_data)
                db.add(branch)
                db.commit()
                db.refresh(branch)
                print(f"  [OK] Creada sucursal: {branch.nombre} en {branch.ciudad}")
            else:
                branch.tenant_id = b_data.get("tenant_id", tenant_urban.id)
                db.commit()
            branches_map[b_data["nombre"]] = branch

        # 4. Sembrar Proveedores y Categorías de Moda Masculina
        print("\n[4/6] Sembrando Proveedores y Categorías...")
        suppliers_data = [
            {
                "nombre": "Textilera Andina S.A.",
                "contacto": "Roberto Gómez",
                "telefono": "+591 2 2841100",
                "email": "ventas@textilandina.com",
                "direccion": "Parque Industrial Calle 3, La Paz",
                "activo": True
            },
            {
                "nombre": "Moda Urbana Bolivia",
                "contacto": "Laura Fernandez",
                "telefono": "+591 3 3556677",
                "email": "contacto@modaurbanabolivia.com",
                "direccion": "Av. Doble Vía a La Guardia Km 4, Santa Cruz",
                "activo": True
            },
            {
                "nombre": "Confecciones del Oriente",
                "contacto": "Mauricio Vaca",
                "telefono": "+591 3 3119988",
                "email": "pedidos@confeccionesoriente.bo",
                "direccion": "Radial 17 y medio, Santa Cruz",
                "activo": True
            },
            {
                "nombre": "Streetwear Denim Lab",
                "contacto": "Valeria Rios",
                "telefono": "+591 4 4220011",
                "email": "info@streetweardenim.com",
                "direccion": "Av. Heroínas #560, Cochabamba",
                "activo": True
            }
        ]

        for s_data in suppliers_data:
            supplier = db.query(Supplier).filter(Supplier.nombre == s_data["nombre"]).first()
            if not supplier:
                s_dict = {**s_data, "tenant_id": tenant_urban.id}
                supplier = Supplier(**s_dict)
                db.add(supplier)
                db.commit()
                print(f"  [OK] Creado proveedor: {supplier.nombre}")
            else:
                supplier.tenant_id = tenant_urban.id
                db.commit()

        categories_data = [
            {"nombre": "Poleras & Remeras", "descripcion": "Poleras oversized, boxy fit, remeras básicas y urbanas para hombre"},
            {"nombre": "Hoodies & Polerones", "descripcion": "Hoodies frizados pesados, polerones crewneck y hoodies con cierre"},
            {"nombre": "Camisas & Sobrecamisas", "descripcion": "Camisas de lino, franela leñadora, sobrecamisas y estilo resort"},
            {"nombre": "Pantalones & Jeans", "descripcion": "Pantalones cargo tácticos, jeans baggy 90s y pantalones chino stretch"},
            {"nombre": "Chaquetas & Abrigos", "descripcion": "Bombers urbanas, cazadoras trucker en denim y cortavientos"},
            {"nombre": "Shorts & Bermudas", "descripcion": "Shorts cargo de verano, bermudas denim y shorts deportivos"}
        ]

        categories_map = {}
        for c_data in categories_data:
            cat = db.query(Category).filter(Category.nombre == c_data["nombre"]).first()
            if not cat:
                c_dict = {**c_data, "tenant_id": tenant_urban.id}
                cat = Category(**c_dict)
                db.add(cat)
                db.commit()
                db.refresh(cat)
                print(f"  [OK] Creada categoría: {cat.nombre}")
            else:
                cat.tenant_id = tenant_urban.id
                cat.descripcion = c_data["descripcion"]
                db.commit()
            categories_map[c_data["nombre"]] = cat

        # Categoría para Moda Express (Empresa 2)
        cat_moda = db.query(Category).filter(Category.nombre == "Moda Formal y Ejecutiva").first()
        if not cat_moda:
            cat_moda = Category(
                nombre="Moda Formal y Ejecutiva",
                descripcion="Prendas de vestir formales, trajes ejecutivos y vestidos elegantes",
                activa=True,
                tenant_id=tenant_moda.id
            )
            db.add(cat_moda)
            db.commit()
            db.refresh(cat_moda)
            print("  [OK] Creada categoría exclusiva para Moda Express: Moda Formal y Ejecutiva")
        else:
            cat_moda.tenant_id = tenant_moda.id
            db.commit()
        categories_map["Moda Formal y Ejecutiva"] = cat_moda

        # 5. Sembrar Productos y Variantes de Moda Masculina (100% Exclusivo Hombre)
        print("\n[5/6] Sembrando Productos y Variantes (Catálogo Masculino con Fondo Blanco)...")

        # Migrar nombres de productos anteriores para conservar historial de ventas/reservas
        renames_map = {
            "Polera Oversize FICTT Core": "Polera Gráfica Venom Streetwear",
            "Polera Heavyweight Boxy Fit": "Polera Minimalista Yahweh Yireh",
            "Polera Gráfica Cyber Street": "Polera Gráfica Venom Streetwear",
        }
        for old_name, new_name in renames_map.items():
            old_p = db.query(Product).filter(Product.nombre == old_name).first()
            if old_p:
                target_p = db.query(Product).filter(Product.nombre == new_name).first()
                if not target_p or target_p.id == old_p.id:
                    old_p.nombre = new_name
                    db.commit()
                else:
                    old_p.activo = False
                    db.commit()

        products_data = [
            # 1. Poleras
            {
                "nombre": "Polera Gráfica Venom Streetwear",
                "descripcion": "Polera 100% algodón peinado 240g de corte streetwear con estampado frontal serigráfico Venom Eddie Brock en alta definición.",
                "precio_base": Decimal("125.00"),
                "categoria_nombre": "Poleras & Remeras",
                "temporada": "Colección Streetwear FICTT 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "/static/products/polera_venom_graphic.png",
                "variantes": [
                    {"talla": "S", "color": "Negro", "sku": "POL-VNM-BLK-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Negro", "sku": "POL-VNM-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro", "sku": "POL-VNM-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Negro", "sku": "POL-VNM-BLK-XL", "precio_extra": Decimal("10.00")},
                    {"talla": "M", "color": "Negro Lavado", "sku": "POL-VNM-WSH-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro Lavado", "sku": "POL-VNM-WSH-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Polera Minimalista Yahweh Yireh",
                "descripcion": "Polera boxy fit de estructura pesada 280g, cuello cerrado reforzado y tipografía minimalista blanca sobre negro.",
                "precio_base": Decimal("130.00"),
                "categoria_nombre": "Poleras & Remeras",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "/static/products/polera_yahweh_yireh.png",
                "variantes": [
                    {"talla": "S", "color": "Negro Mate", "sku": "POL-YHW-BLK-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Negro Mate", "sku": "POL-YHW-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro Mate", "sku": "POL-YHW-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Negro Mate", "sku": "POL-YHW-BLK-XL", "precio_extra": Decimal("10.00")},
                    {"talla": "M", "color": "Blanco Crudo", "sku": "POL-YHW-WHT-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Blanco Crudo", "sku": "POL-YHW-WHT-L", "precio_extra": Decimal("0.00")},
                ]
            },
            # 2. Hoodies & Polerones
            {
                "nombre": "Hoodie Heavyweight Canguro FICTT",
                "descripcion": "Buzo con capucha frizado 400g con bolsillo canguro reforzado y cordones gruesos en negro mate.",
                "precio_base": Decimal("280.00"),
                "categoria_nombre": "Hoodies & Polerones",
                "temporada": "Colección Streetwear FICTT 2025",
                "proveedor": "Confecciones del Oriente",
                "imagen_url": "/static/products/hoodie_heavyweight_canguro.jpg",
                "variantes": [
                    {"talla": "S", "color": "Negro", "sku": "HOD-HVY-BLK-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Negro", "sku": "HOD-HVY-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro", "sku": "HOD-HVY-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Negro", "sku": "HOD-HVY-BLK-XL", "precio_extra": Decimal("15.00")},
                    {"talla": "M", "color": "Gris Jaspe", "sku": "HOD-HVY-GRY-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Gris Jaspe", "sku": "HOD-HVY-GRY-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Polerón Crewneck Minimalist",
                "descripcion": "Sweatshirt cuello redondo en felpa francesa gris melange de corte clásico sin capucha para estilo smart casual masculino.",
                "precio_base": Decimal("240.00"),
                "categoria_nombre": "Hoodies & Polerones",
                "temporada": "Otoño-Invierno 2025",
                "proveedor": "Confecciones del Oriente",
                "imagen_url": "/static/products/poleron_crewneck_minimalist.jpg",
                "variantes": [
                    {"talla": "M", "color": "Gris Melange", "sku": "CRW-MIN-GRY-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Gris Melange", "sku": "CRW-MIN-GRY-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Gris Melange", "sku": "CRW-MIN-GRY-XL", "precio_extra": Decimal("10.00")},
                    {"talla": "M", "color": "Negro", "sku": "CRW-MIN-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro", "sku": "CRW-MIN-BLK-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Hoodie Zip-Up Tech Fleece",
                "descripcion": "Chaqueta buzo con cierre frontal completo YKK, tejido térmico Tech Fleece carbón y bolsillos sellados con cremallera.",
                "precio_base": Decimal("310.00"),
                "categoria_nombre": "Hoodies & Polerones",
                "temporada": "Otoño-Invierno 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "/static/products/hoodie_tech_zipup.jpg",
                "variantes": [
                    {"talla": "M", "color": "Carbón", "sku": "HOD-ZIP-DRK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Carbón", "sku": "HOD-ZIP-DRK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Carbón", "sku": "HOD-ZIP-DRK-XL", "precio_extra": Decimal("15.00")},
                    {"talla": "M", "color": "Negro Mate", "sku": "HOD-ZIP-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro Mate", "sku": "HOD-ZIP-BLK-L", "precio_extra": Decimal("0.00")},
                ]
            },
            # 3. Camisas & Sobrecamisas
            {
                "nombre": "Camisa Casual Lino Cuello Mao",
                "descripcion": "Camisa de lino puro blanco con cuello mao mandarin, manga larga arremangable y corte relajado para climas cálidos.",
                "precio_base": Decimal("180.00"),
                "categoria_nombre": "Camisas & Sobrecamisas",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "/static/products/camisa_casual_lino_mao.jpg",
                "variantes": [
                    {"talla": "S", "color": "Blanco Puro", "sku": "CAM-LIN-WHT-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Blanco Puro", "sku": "CAM-LIN-WHT-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Blanco Puro", "sku": "CAM-LIN-WHT-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Blanco Puro", "sku": "CAM-LIN-WHT-XL", "precio_extra": Decimal("10.00")},
                    {"talla": "M", "color": "Arena Natural", "sku": "CAM-LIN-SND-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Arena Natural", "sku": "CAM-LIN-SND-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Sobrecamisa Franela Leñadora",
                "descripcion": "Sobrecamisa gruesa de franela escocesa a cuadros rojo y negro con doble bolsillo con solapa en el pecho.",
                "precio_base": Decimal("210.00"),
                "categoria_nombre": "Camisas & Sobrecamisas",
                "temporada": "Otoño-Invierno 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "/static/products/sobrecamisa_franela_lenadora.jpg",
                "variantes": [
                    {"talla": "M", "color": "Rojo/Negro", "sku": "CAM-FLN-RED-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Rojo/Negro", "sku": "CAM-FLN-RED-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Rojo/Negro", "sku": "CAM-FLN-RED-XL", "precio_extra": Decimal("10.00")},
                    {"talla": "M", "color": "Azul/Negro", "sku": "CAM-FLN-BLU-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Azul/Negro", "sku": "CAM-FLN-BLU-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Camisa Resort Cuello Cubano",
                "descripcion": "Camisa veraniega con cuello abierto estilo camp collar en tejido suave y fresco con patrón geométrico minimalista.",
                "precio_base": Decimal("165.00"),
                "categoria_nombre": "Camisas & Sobrecamisas",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "/static/products/camisa_resort_cubana.jpg",
                "variantes": [
                    {"talla": "S", "color": "Beige Geométrico", "sku": "CAM-RST-BGE-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Beige Geométrico", "sku": "CAM-RST-BGE-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Beige Geométrico", "sku": "CAM-RST-BGE-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Beige Geométrico", "sku": "CAM-RST-BGE-XL", "precio_extra": Decimal("10.00")},
                    {"talla": "M", "color": "Blanco Crudo", "sku": "CAM-RST-WHT-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Blanco Crudo", "sku": "CAM-RST-WHT-L", "precio_extra": Decimal("0.00")},
                ]
            },
            # 4. Pantalones & Jeans
            {
                "nombre": "Pantalón Cargo Táctico Streetwear",
                "descripcion": "Pantalón cargo ripstop táctico con 6 bolsillos utilitarios de gran capacidad en negro sólido y calce regular.",
                "precio_base": Decimal("220.00"),
                "categoria_nombre": "Pantalones & Jeans",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "/static/products/pantalon_cargo_tactico.jpg",
                "variantes": [
                    {"talla": "30", "color": "Negro", "sku": "CARGO-TAC-BLK-30", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Negro", "sku": "CARGO-TAC-BLK-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Negro", "sku": "CARGO-TAC-BLK-34", "precio_extra": Decimal("0.00")},
                    {"talla": "36", "color": "Negro", "sku": "CARGO-TAC-BLK-36", "precio_extra": Decimal("10.00")},
                    {"talla": "32", "color": "Verde Militar", "sku": "CARGO-TAC-MIL-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Verde Militar", "sku": "CARGO-TAC-MIL-34", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Jeans Baggy Skate 90s",
                "descripcion": "Pantalón vaquero denim rígido 14oz con corte ancho skate de tiro medio y lavado celeste vintage para hombre.",
                "precio_base": Decimal("235.00"),
                "categoria_nombre": "Pantalones & Jeans",
                "temporada": "Colección Streetwear FICTT 2025",
                "proveedor": "Streetwear Denim Lab",
                "imagen_url": "/static/products/jeans_baggy_skate.jpg",
                "variantes": [
                    {"talla": "30", "color": "Azul Claro Lavado", "sku": "JNS-BGY-LGT-30", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Azul Claro Lavado", "sku": "JNS-BGY-LGT-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Azul Claro Lavado", "sku": "JNS-BGY-LGT-34", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Denim Medio", "sku": "JNS-BGY-MED-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Denim Medio", "sku": "JNS-BGY-MED-34", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Pantalón Chino Slim Fit Stretch",
                "descripcion": "Pantalón chino formal-casual en gabardina de algodón beige khaki con elastano para máxima comodidad.",
                "precio_base": Decimal("195.00"),
                "categoria_nombre": "Pantalones & Jeans",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "/static/products/pantalon_chino_slim.jpg",
                "variantes": [
                    {"talla": "30", "color": "Beige Khaki", "sku": "CHN-SLM-KHK-30", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Beige Khaki", "sku": "CHN-SLM-KHK-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Beige Khaki", "sku": "CHN-SLM-KHK-34", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Azul Marino", "sku": "CHN-SLM-NVY-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Negro", "sku": "CHN-SLM-BLK-34", "precio_extra": Decimal("0.00")},
                ]
            },
            # 5. Chaquetas & Abrigos
            {
                "nombre": "Chaqueta Bomber MA-1 Streetwear",
                "descripcion": "Chaqueta aviadora bomber de satén de nylon negro con forro interior naranja clásico y bolsillo militar en manga.",
                "precio_base": Decimal("350.00"),
                "categoria_nombre": "Chaquetas & Abrigos",
                "temporada": "Otoño-Invierno 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "/static/products/chaqueta_bomber_ma1.jpg",
                "variantes": [
                    {"talla": "M", "color": "Negro", "sku": "BOM-MA1-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro", "sku": "BOM-MA1-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Negro", "sku": "BOM-MA1-BLK-XL", "precio_extra": Decimal("20.00")},
                    {"talla": "M", "color": "Verde Militar", "sku": "BOM-MA1-GRN-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Verde Militar", "sku": "BOM-MA1-GRN-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Cazadora Denim Trucker Vintage",
                "descripcion": "Chaqueta vaquera trucker clásica en denim azul grueso 100% algodón con botones metálicos grabados.",
                "precio_base": Decimal("310.00"),
                "categoria_nombre": "Chaquetas & Abrigos",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Streetwear Denim Lab",
                "imagen_url": "/static/products/cazadora_denim_trucker.jpg",
                "variantes": [
                    {"talla": "S", "color": "Azul Clásico", "sku": "JKT-DNM-BLU-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Azul Clásico", "sku": "JKT-DNM-BLU-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Azul Clásico", "sku": "JKT-DNM-BLU-L", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Denim Negro", "sku": "JKT-DNM-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Denim Negro", "sku": "JKT-DNM-BLK-L", "precio_extra": Decimal("0.00")},
                ]
            },
            # 6. Shorts & Bermudas
            {
                "nombre": "Short Cargo Urbano Ripstop",
                "descripcion": "Bermuda cargo casual a la rodilla en verde oliva ripstop resistente con bolsillos laterales con solapa para hombre.",
                "precio_base": Decimal("140.00"),
                "categoria_nombre": "Shorts & Bermudas",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "/static/products/short_cargo_urbano.jpg",
                "variantes": [
                    {"talla": "30", "color": "Verde Oliva", "sku": "SHT-CRG-OLV-30", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Verde Oliva", "sku": "SHT-CRG-OLV-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Verde Oliva", "sku": "SHT-CRG-OLV-34", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Negro", "sku": "SHT-CRG-BLK-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Negro", "sku": "SHT-CRG-BLK-34", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Short Deportivo FICTT Athletics",
                "descripcion": "Short deportivo de secado rápido en negro con cintura elástica con cordón y detalles reflectivos.",
                "precio_base": Decimal("115.00"),
                "categoria_nombre": "Shorts & Bermudas",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "/static/products/short_deportivo_athletics.jpg",
                "variantes": [
                    {"talla": "S", "color": "Negro", "sku": "SHT-ATH-BLK-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Negro", "sku": "SHT-ATH-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro", "sku": "SHT-ATH-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Negro", "sku": "SHT-ATH-BLK-XL", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Gris Plomo", "sku": "SHT-ATH-GRY-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Gris Plomo", "sku": "SHT-ATH-GRY-L", "precio_extra": Decimal("0.00")},
                ]
            }
        ]

        # Desactivar únicamente productos obsoletos de UrbanStyle que no pertenezcan a la línea masculina
        active_menswear_names = {p["nombre"] for p in products_data}
        for existing_prod in db.query(Product).filter(Product.tenant_id == tenant_urban.id).all():
            if existing_prod.nombre not in active_menswear_names:
                existing_prod.activo = False
                print(f"  [-] Desactivado producto obsoleto/no masculino de UrbanStyle: {existing_prod.nombre}")
        db.commit()

        all_variants = []
        for p_info in products_data:
            cat_obj = categories_map.get(p_info["categoria_nombre"])
            if not cat_obj:
                continue

            product = db.query(Product).filter(Product.nombre == p_info["nombre"]).first()
            if not product:
                product = Product(
                    tenant_id=tenant_urban.id,
                    nombre=p_info["nombre"],
                    descripcion=p_info["descripcion"],
                    precio_base=p_info["precio_base"],
                    categoria_id=cat_obj.id,
                    temporada=p_info["temporada"],
                    proveedor=p_info["proveedor"],
                    imagen_url=p_info.get("imagen_url"),
                    activo=True
                )
                db.add(product)
                db.commit()
                db.refresh(product)
                print(f"  [OK] Creado producto masculino: {product.nombre}")
            else:
                product.tenant_id = tenant_urban.id
                product.imagen_url = p_info.get("imagen_url")
                product.descripcion = p_info["descripcion"]
                product.precio_base = p_info["precio_base"]
                product.categoria_id = cat_obj.id
                product.temporada = p_info["temporada"]
                product.proveedor = p_info["proveedor"]
                product.activo = True
                db.commit()
                print(f"  [+] Producto masculino actualizado: {product.nombre}")

            # Variantes
            for v_data in p_info["variantes"]:
                variant = db.query(ProductVariant).filter(
                    (ProductVariant.sku == v_data["sku"]) |
                    ((ProductVariant.producto_id == product.id) & (ProductVariant.talla == v_data["talla"]) & (ProductVariant.color == v_data["color"]))
                ).first()
                if not variant:
                    variant = ProductVariant(
                        producto_id=product.id,
                        talla=v_data["talla"],
                        color=v_data["color"],
                        sku=v_data["sku"],
                        precio_extra=v_data["precio_extra"],
                        activo=True
                    )
                    db.add(variant)
                    db.commit()
                    db.refresh(variant)
                else:
                    variant.sku = v_data["sku"]
                    variant.precio_extra = v_data["precio_extra"]
                    variant.activo = True
                    db.commit()
                all_variants.append(variant)

        # 5.5. Sembrar Catálogo Exclusivo de Moda Express Tarija (Empresa 2)
        print("\n[5.5/6] Sembrando Productos y Variantes de Moda Express Tarija (Empresa 2)...")
        moda_products = [
            {
                "nombre": "Camisa Ejecutiva Slim Fit",
                "descripcion": "Camisa formal 100% algodón egipcio antiarrugas para oficina y eventos ejecutivos.",
                "precio_base": Decimal("180.00"),
                "categoria_id": cat_moda.id,
                "temporada": "Línea Ejecutiva 2025",
                "proveedor": "Confecciones del Oriente",
                "imagen_url": "https://images.unsplash.com/photo-1620012253295-c15c429f66bf?w=600",
                "variantes": [
                    {"talla": "S", "color": "Blanco Puro", "sku": "CAM-SLM-WHT-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Blanco Puro", "sku": "CAM-SLM-WHT-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Blanco Puro", "sku": "CAM-SLM-WHT-L", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Azul Cielo", "sku": "CAM-SLM-BLU-M", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Traje Formal Dos Piezas",
                "descripcion": "Terno clásico formal en corte contemporáneo de lana fría con saco estructurado y pantalón recto.",
                "precio_base": Decimal("650.00"),
                "categoria_id": cat_moda.id,
                "temporada": "Línea Ejecutiva 2025",
                "proveedor": "Confecciones del Oriente",
                "imagen_url": "https://images.unsplash.com/photo-1594938298603-c8148c4dae35?w=600",
                "variantes": [
                    {"talla": "38R", "color": "Azul Marino", "sku": "TRJ-FRM-NVY-38", "precio_extra": Decimal("0.00")},
                    {"talla": "40R", "color": "Azul Marino", "sku": "TRJ-FRM-NVY-40", "precio_extra": Decimal("0.00")},
                    {"talla": "42R", "color": "Gris Oxford", "sku": "TRJ-FRM-GRY-42", "precio_extra": Decimal("20.00")},
                ]
            },
            {
                "nombre": "Vestido de Fiesta Elegante",
                "descripcion": "Vestido largo de fiesta en satén con escote drapeado y caída fluida para ocasiones de gala.",
                "precio_base": Decimal("420.00"),
                "categoria_id": cat_moda.id,
                "temporada": "Colección Gala 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "https://images.unsplash.com/photo-1566174053879-31528523f8ae?w=600",
                "variantes": [
                    {"talla": "S", "color": "Rojo Carmín", "sku": "VES-FST-RED-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Rojo Carmín", "sku": "VES-FST-RED-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro Noche", "sku": "VES-FST-BLK-L", "precio_extra": Decimal("0.00")},
                ]
            }
        ]

        moda_variants = []
        for p_data in moda_products:
            prod_m = db.query(Product).filter(
                Product.nombre == p_data["nombre"],
                Product.tenant_id == tenant_moda.id
            ).first()
            if not prod_m:
                prod_m = Product(
                    tenant_id=tenant_moda.id,
                    nombre=p_data["nombre"],
                    descripcion=p_data["descripcion"],
                    precio_base=p_data["precio_base"],
                    categoria_id=p_data["categoria_id"],
                    temporada=p_data["temporada"],
                    proveedor=p_data["proveedor"],
                    imagen_url=p_data.get("imagen_url"),
                    activo=True
                )
                db.add(prod_m)
                db.commit()
                db.refresh(prod_m)
                print(f"  [OK] Creado producto Moda Express: {prod_m.nombre}")
            else:
                prod_m.tenant_id = tenant_moda.id
                prod_m.activo = True
                prod_m.imagen_url = p_data.get("imagen_url")
                prod_m.descripcion = p_data["descripcion"]
                prod_m.precio_base = p_data["precio_base"]
                prod_m.categoria_id = p_data["categoria_id"]
                prod_m.temporada = p_data["temporada"]
                prod_m.proveedor = p_data["proveedor"]
                db.commit()

            for v_data in p_data["variantes"]:
                var_m = db.query(ProductVariant).filter(ProductVariant.sku == v_data["sku"]).first()
                if not var_m:
                    var_m = ProductVariant(
                        producto_id=prod_m.id,
                        talla=v_data["talla"],
                        color=v_data["color"],
                        sku=v_data["sku"],
                        precio_extra=v_data["precio_extra"],
                        activo=True
                    )
                    db.add(var_m)
                    db.commit()
                    db.refresh(var_m)
                else:
                    var_m.sku = v_data["sku"]
                    var_m.precio_extra = v_data["precio_extra"]
                    var_m.activo = True
                    db.commit()
                moda_variants.append(var_m)

        # 6. Sembrar Inventario en Sucursales y Movimientos
        print("\n[6/6] Sembrando Inventario, Reservas y Ventas...")
        central_branch = branches_map.get("Sucursal Central")
        equipetrol_branch = branches_map.get("Sucursal Equipetrol")
        admin_user = users_map.get("admin@ficttstore.com")
        cajero_user = users_map.get("cajero@ficttstore.com")
        cliente_user = users_map.get("cliente@ficttstore.com")

        # Asignar stock en todas las sucursales
        inventory_items = []
        stock_by_branch = {
            "Sucursal Central": 25,
            "Sucursal Equipetrol": 18,
            "Sucursal Montero": 12,
            "Sucursal Cochabamba": 15,
        }
        for v in all_variants:
            for branch in branches_map.values():
                if not branch or branch.tenant_id != tenant_urban.id:
                    continue
                inv = db.query(Inventory).filter(
                    Inventory.sucursal_id == branch.id,
                    Inventory.variante_id == v.id
                ).first()

                if not inv:
                    stock_qty = stock_by_branch.get(branch.nombre, 15)
                    inv = Inventory(
                        tenant_id=branch.tenant_id,
                        sucursal_id=branch.id,
                        variante_id=v.id,
                        stock_actual=stock_qty,
                        stock_reservado=0,
                        stock_minimo=5,
                        ubicacion=f"Pasillo A - Estante {v.talla}",
                        activo=True
                    )
                    db.add(inv)
                    db.commit()
                    db.refresh(inv)

                    # Registrar movimiento de recepción inicial
                    mov = InventoryMovement(
                        tenant_id=branch.tenant_id,
                        inventario_id=inv.id,
                        tipo=MovementType.ENTRADA,
                        cantidad=stock_qty,
                        stock_antes=0,
                        stock_despues=stock_qty,
                        referencia="RECEPCION-INICIAL-2025",
                        nota=f"Carga de inventario inicial para {branch.nombre}",
                        usuario_id=admin_user.id
                    )
                    db.add(mov)
                    db.commit()
                else:
                    inv.tenant_id = branch.tenant_id
                    db.commit()
                inventory_items.append(inv)

        # Inventario de Moda Express (Empresa 2)
        moda_branch_1 = branches_map.get("Moda Express - Tarija Centro")
        moda_branch_2 = branches_map.get("Moda Express - El Molino")
        for v in moda_variants:
            for m_branch in [moda_branch_1, moda_branch_2]:
                if not m_branch:
                    continue
                inv_m = db.query(Inventory).filter(
                    Inventory.sucursal_id == m_branch.id,
                    Inventory.variante_id == v.id
                ).first()
                if not inv_m:
                    inv_m = Inventory(
                        tenant_id=tenant_moda.id,
                        sucursal_id=m_branch.id,
                        variante_id=v.id,
                        stock_actual=20,
                        stock_reservado=0,
                        stock_minimo=4,
                        ubicacion=f"Sector Formal {v.talla}",
                        activo=True
                    )
                    db.add(inv_m)
                    db.commit()
                else:
                    inv_m.tenant_id = tenant_moda.id
                    db.commit()

        print(f"  [OK] Inventario sembrado: {len(inventory_items)} registros de stock listos.")

        # Sembrar Reservas de prueba
        res1 = db.query(Reservation).filter(Reservation.codigo == "RES-2025-0001").first()
        if not res1 and central_branch and cliente_user and len(all_variants) >= 2:
            res1 = Reservation(
                codigo="RES-2025-0001",
                cliente_id=cliente_user.id,
                sucursal_id=central_branch.id,
                estado=ReservationStatus.PREPARADA,
                fecha_hora_esperada=utc_now() + timedelta(days=2),
                fecha_expiracion=utc_now() + timedelta(days=4),
                total_estimado=Decimal("340.00"),
                nota="Cliente pasará a recoger en la tarde",
                activo=True
            )
            db.add(res1)
            db.commit()
            db.refresh(res1)

            # Detalles de la reserva y reserva de stock
            v1 = all_variants[0]
            v2 = all_variants[1]
            det1 = ReservationDetail(
                reserva_id=res1.id,
                variante_id=v1.id,
                cantidad=1,
                precio_unitario=Decimal("120.00"),
                subtotal=Decimal("120.00")
            )
            det2 = ReservationDetail(
                reserva_id=res1.id,
                variante_id=v2.id,
                cantidad=1,
                precio_unitario=Decimal("220.00"),
                subtotal=Decimal("220.00")
            )
            db.add_all([det1, det2])

            # Actualizar stock reservado en inventario
            inv_v1 = db.query(Inventory).filter(Inventory.sucursal_id == central_branch.id, Inventory.variante_id == v1.id).first()
            inv_v2 = db.query(Inventory).filter(Inventory.sucursal_id == central_branch.id, Inventory.variante_id == v2.id).first()
            if inv_v1:
                inv_v1.stock_reservado += 1
            if inv_v2:
                inv_v2.stock_reservado += 1
            db.commit()
            print("  [OK] Creada Reserva de prueba: RES-2025-0001 (Estado: PREPARADA)")

        # Reserva 2 (Pendiente)
        res2 = db.query(Reservation).filter(Reservation.codigo == "RES-2025-0002").first()
        if not res2 and equipetrol_branch and cliente_user and len(all_variants) >= 4:
            v3 = all_variants[3]
            res2 = Reservation(
                codigo="RES-2025-0002",
                cliente_id=cliente_user.id,
                sucursal_id=equipetrol_branch.id,
                estado=ReservationStatus.PENDIENTE,
                fecha_hora_esperada=utc_now() + timedelta(days=1),
                fecha_expiracion=utc_now() + timedelta(days=3),
                total_estimado=Decimal("280.00"),
                nota="Reserva desde aplicación móvil",
                activo=True
            )
            db.add(res2)
            db.commit()
            db.refresh(res2)

            det3 = ReservationDetail(
                reserva_id=res2.id,
                variante_id=v3.id,
                cantidad=1,
                precio_unitario=Decimal("280.00"),
                subtotal=Decimal("280.00")
            )
            db.add(det3)
            inv_v3 = db.query(Inventory).filter(Inventory.sucursal_id == equipetrol_branch.id, Inventory.variante_id == v3.id).first()
            if inv_v3:
                inv_v3.stock_reservado += 1
            db.commit()
            print("  [OK] Creada Reserva de prueba: RES-2025-0002 (Estado: PENDIENTE)")

        # Sembrar Ventas de prueba con métodos activos (EFECTIVO y PAYPAL)
        sale1 = db.query(Sale).filter(Sale.numero_recibo == "VTA-2025-0001").first()
        if not sale1 and central_branch and cajero_user and cliente_user and len(all_variants) >= 2:
            v_sale = all_variants[0]
            sale1 = Sale(
                tenant_id=tenant_urban.id,
                numero_recibo="VTA-2025-0001",
                tipo=SaleType.PRESENCIAL,
                estado=SaleStatus.COMPLETADA,
                sucursal_id=central_branch.id,
                cliente_id=cliente_user.id,
                cajero_id=cajero_user.id,
                metodo_pago=PaymentMethod.EFECTIVO,
                monto_total=Decimal("120.00"),
                impuesto_iva=Decimal("15.60"),
                monto_neto=Decimal("104.40"),
                monto_recibido=Decimal("150.00"),
                cambio=Decimal("30.00"),
                nota="Venta POS mostrador en efectivo",
                created_at=utc_now() - timedelta(days=2)
            )
            db.add(sale1)
            db.commit()
            db.refresh(sale1)

            det_sale = SaleDetail(
                venta_id=sale1.id,
                variante_id=v_sale.id,
                cantidad=1,
                precio_unitario=Decimal("120.00"),
                subtotal=Decimal("120.00")
            )
            db.add(det_sale)

            # Descontar stock
            inv_sale = db.query(Inventory).filter(Inventory.sucursal_id == central_branch.id, Inventory.variante_id == v_sale.id).first()
            if inv_sale:
                antes = inv_sale.stock_actual
                inv_sale.stock_actual = max(0, inv_sale.stock_actual - 1)
                mov_vta = InventoryMovement(
                    tenant_id=tenant_urban.id,
                    inventario_id=inv_sale.id,
                    tipo=MovementType.SALIDA_VENTA,
                    cantidad=1,
                    stock_antes=antes,
                    stock_despues=inv_sale.stock_actual,
                    referencia="VTA-2025-0001",
                    nota="Salida por venta en mostrador",
                    usuario_id=cajero_user.id
                )
                db.add(mov_vta)
            db.commit()
            print("  [OK] Creada Venta de prueba: VTA-2025-0001 (Presencial / Efectivo / 120.00 Bs.)")

        sale2 = db.query(Sale).filter(Sale.numero_recibo == "VTA-2025-0002").first()
        if not sale2 and equipetrol_branch and cajero_user and cliente_user and len(all_variants) >= 4:
            v_sale2 = all_variants[3]
            sale2 = Sale(
                tenant_id=tenant_urban.id,
                numero_recibo="VTA-2025-0002",
                tipo=SaleType.DIGITAL,
                estado=SaleStatus.COMPLETADA,
                sucursal_id=equipetrol_branch.id,
                cliente_id=cliente_user.id,
                cajero_id=None,
                metodo_pago=PaymentMethod.PAYPAL,
                referencia_pago="PAYPAL_SANDBOX_8AM31925X3104210E",
                monto_total=Decimal("280.00"),
                impuesto_iva=Decimal("36.40"),
                monto_neto=Decimal("243.60"),
                monto_recibido=Decimal("280.00"),
                cambio=Decimal("0.00"),
                nota="Compra digital en App Móvil cobrada con PayPal",
                created_at=utc_now() - timedelta(hours=6)
            )
            db.add(sale2)
            db.commit()
            db.refresh(sale2)

            det_sale2 = SaleDetail(
                venta_id=sale2.id,
                variante_id=v_sale2.id,
                cantidad=1,
                precio_unitario=Decimal("280.00"),
                subtotal=Decimal("280.00")
            )
            db.add(det_sale2)

            # Descontar stock para sale2
            inv_sale2 = db.query(Inventory).filter(Inventory.sucursal_id == equipetrol_branch.id, Inventory.variante_id == v_sale2.id).first()
            if inv_sale2:
                antes = inv_sale2.stock_actual
                inv_sale2.stock_actual = max(0, inv_sale2.stock_actual - 1)
                mov_vta2 = InventoryMovement(
                    tenant_id=tenant_urban.id,
                    inventario_id=inv_sale2.id,
                    tipo=MovementType.SALIDA_VENTA,
                    cantidad=1,
                    stock_antes=antes,
                    stock_despues=inv_sale2.stock_actual,
                    referencia="VTA-2025-0002",
                    nota="Salida por venta digital PayPal",
                    usuario_id=admin_user.id
                )
                db.add(mov_vta2)
            db.commit()
            print("  [OK] Creada Venta de prueba: VTA-2025-0002 (Digital / PayPal / 280.00 Bs.)")

        sale3 = db.query(Sale).filter(Sale.numero_recibo == "VTA-2025-0003").first()
        if not sale3 and central_branch and cliente_user and len(all_variants) >= 5:
            v_sale3 = all_variants[4]
            sale3 = Sale(
                tenant_id=tenant_urban.id,
                numero_recibo="VTA-2025-0003",
                tipo=SaleType.DIGITAL,
                estado=SaleStatus.COMPLETADA,
                sucursal_id=central_branch.id,
                cliente_id=cliente_user.id,
                cajero_id=None,
                metodo_pago=PaymentMethod.TARJETA,
                referencia_pago="STRIPE_DIGITAL_AUTH_77192",
                monto_total=Decimal("350.00"),
                impuesto_iva=Decimal("45.50"),
                monto_neto=Decimal("304.50"),
                monto_recibido=Decimal("350.00"),
                cambio=Decimal("0.00"),
                nota="Compra digital en App Móvil pagada con tarjeta",
                created_at=utc_now() - timedelta(days=1, hours=2)
            )
            db.add(sale3)
            db.commit()
            db.refresh(sale3)

            det_sale3 = SaleDetail(
                venta_id=sale3.id,
                variante_id=v_sale3.id,
                cantidad=1,
                precio_unitario=Decimal("350.00"),
                subtotal=Decimal("350.00")
            )
            db.add(det_sale3)
            db.commit()
            print("  [OK] Creada Venta de prueba: VTA-2025-0003 (Digital / Tarjeta / 350.00 Bs.)")

        sale4 = db.query(Sale).filter(Sale.numero_recibo == "VTA-2025-0004").first()
        if not sale4 and equipetrol_branch and cajero_user and cliente_user and len(all_variants) >= 6:
            v_sale4 = all_variants[5]
            sale4 = Sale(
                tenant_id=tenant_urban.id,
                numero_recibo="VTA-2025-0004",
                tipo=SaleType.PRESENCIAL,
                estado=SaleStatus.COMPLETADA,
                sucursal_id=equipetrol_branch.id,
                cliente_id=cliente_user.id,
                cajero_id=cajero_user.id,
                metodo_pago=PaymentMethod.EFECTIVO,
                referencia_pago=None,
                monto_total=Decimal("180.00"),
                impuesto_iva=Decimal("23.40"),
                monto_neto=Decimal("156.60"),
                monto_recibido=Decimal("200.00"),
                cambio=Decimal("20.00"),
                nota="Liquidación de compra en tienda física en Efectivo",
                created_at=utc_now() - timedelta(hours=3)
            )
            db.add(sale4)
            db.commit()
            db.refresh(sale4)

            det_sale4 = SaleDetail(
                venta_id=sale4.id,
                variante_id=v_sale4.id,
                cantidad=1,
                precio_unitario=Decimal("180.00"),
                subtotal=Decimal("180.00")
            )
            db.add(det_sale4)
            db.commit()
            print("  [OK] Creada Venta de prueba: VTA-2025-0004 (Presencial / Efectivo / 180.00 Bs.)")

        # Ventas de Moda Express (Empresa 2)
        sale_m1 = db.query(Sale).filter(Sale.numero_recibo == "VTA-MODA-0001").first()
        cajero_moda = users_map.get("cajero@modaexpress.com")
        if not sale_m1 and moda_branch_1 and cajero_moda and len(moda_variants) >= 1:
            v_m1 = moda_variants[0]
            sale_m1 = Sale(
                tenant_id=tenant_moda.id,
                numero_recibo="VTA-MODA-0001",
                tipo=SaleType.PRESENCIAL,
                estado=SaleStatus.COMPLETADA,
                sucursal_id=moda_branch_1.id,
                cliente_id=None,
                cajero_id=cajero_moda.id,
                metodo_pago=PaymentMethod.EFECTIVO,
                monto_total=Decimal("180.00"),
                impuesto_iva=Decimal("23.40"),
                monto_neto=Decimal("156.60"),
                monto_recibido=Decimal("200.00"),
                cambio=Decimal("20.00"),
                nota="Venta mostrador Moda Express Tarija Centro",
                created_at=utc_now() - timedelta(days=1)
            )
            db.add(sale_m1)
            db.commit()
            db.refresh(sale_m1)

            det_m1 = SaleDetail(
                venta_id=sale_m1.id,
                variante_id=v_m1.id,
                cantidad=1,
                precio_unitario=Decimal("180.00"),
                subtotal=Decimal("180.00")
            )
            db.add(det_m1)
            db.commit()
            print("  [OK] Creada Venta Moda Express: VTA-MODA-0001 (Presencial / 180.00 Bs.)")

        # 7. Sembrado masivo y equitativo por sucursal mes a mes (Enero a Octubre 2026)
        seed_historical_sales_and_reservations(
            db=db,
            tenant_urban=tenant_urban,
            tenant_moda=tenant_moda,
            branches_map=branches_map,
            users_map=users_map,
            all_variants=all_variants,
            moda_variants=moda_variants
        )

        # 8. Sembrado de Tarifas de Delivery y Favoritos
        seed_delivery_rates_and_favorites(
            db=db,
            tenant_urban=tenant_urban,
            tenant_moda=tenant_moda,
            branches_map=branches_map
        )

        # 9. Sembrado de Cambios de Prendas (Garantías Retail)
        seed_product_exchanges(
            db=db,
            tenant_urban=tenant_urban,
            branches_map=branches_map,
            users_map=users_map
        )

        print("\n==================================================")
        print(" [SUCCESS] BASE DE DATOS POBLADA EXITOSAMENTE! ")
        print("==================================================")
        print("Empresas y Credenciales de prueba generadas:")
        print("[SUPERADMIN] Plataforma SaaS Multitenant:")
        print(" * SuperAdmin: admin@sistema.com        / Password123!")
        print("[EMPRESA 1] UrbanStyle Bolivia S.R.L. (Plan Profesional: 499 Bs/mes / IA Habilitada):")
        print(" * Admin:      admin@urbanstyle.com     / Password123!")
        print(" * Cajero:     cajero@urbanstyle.com    / Password123!")
        print(" * Encargado:  encargado@ficttstore.com / Password123!")
        print(" * Cliente:    cliente@ficttstore.com   / Password123!")
        print("[EMPRESA 2] Moda Express Tarija (Plan Básico: 199 Bs/mes / Límite 2 Sucursales / Sin IA):")
        print(" * Admin:      admin@modaexpress.com    / Password123!")
        print(" * Cajero:     cajero@modaexpress.com   / Password123!")
        print(" * Cliente:    cliente@modaexpress.com  / Password123!")
        print("==================================================")

    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] Falló el sembrado de base de datos: {e}")
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    seed_all()
