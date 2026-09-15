import sys
import os
import uuid
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal
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
from app.seed_permissions import seed as seed_permissions


def utc_now():
    return datetime.now(timezone.utc)


def seed_all():
    print("==================================================")
    print("Iniciando Sembrado Completo de la Base de Datos...")
    print("==================================================")
    
    # 1. Sembrar roles, permisos y temporadas
    print("\n[1/6] Sembrando Roles, Permisos y Temporadas base...")
    seed_permissions()

    db = SessionLocal()
    try:
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
                "is_verified": True
            },
            {
                "email": "encargado@ficttstore.com",
                "full_name": "Carlos Encargado Sucursal",
                "role_id": encargado_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True
            },
            {
                "email": "cajero@ficttstore.com",
                "full_name": "María Cajera Central",
                "role_id": cajero_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True
            },
            {
                "email": "cliente@ficttstore.com",
                "full_name": "Juan Pérez Cliente",
                "role_id": cliente_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True
            },
            {
                "email": "sofia.cliente@ficttstore.com",
                "full_name": "Sofía Morales",
                "role_id": cliente_role.id,
                "password": get_password_hash("Password123!"),
                "is_active": True,
                "is_verified": True
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
                    is_verified=u_data["is_verified"]
                )
                db.add(user)
                db.commit()
                db.refresh(user)
                print(f"  [OK] Creado usuario: {user.email} (Rol: {user.role.name})")
            else:
                user.role_id = u_data["role_id"]
                user.hashed_password = u_data["password"]
                user.full_name = u_data["full_name"]
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
                "activa": True
            },
            {
                "nombre": "Sucursal Equipetrol",
                "ciudad": "Santa Cruz de la Sierra",
                "direccion": "Av. San Martín #450, Esq. Calle 7",
                "telefono": "+591 3 3498123",
                "activa": True
            },
            {
                "nombre": "Sucursal Montero",
                "ciudad": "Montero",
                "direccion": "Calle Comercio #120, Zona Comercial",
                "telefono": "+591 3 9223344",
                "activa": True
            },
            {
                "nombre": "Sucursal Cochabamba",
                "ciudad": "Cochabamba",
                "direccion": "Av. América Este #780",
                "telefono": "+591 4 4112233",
                "activa": True
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
            branches_map[b_data["nombre"]] = branch

        # 4. Sembrar Proveedores y Categorías
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
                "nombre": "Importadora Calzados & Streetwear",
                "contacto": "Valeria Rios",
                "telefono": "+591 4 4220011",
                "email": "info@calzadosstreet.com",
                "direccion": "Av. Heroínas #560, Cochabamba",
                "activo": True
            }
        ]

        for s_data in suppliers_data:
            supplier = db.query(Supplier).filter(Supplier.nombre == s_data["nombre"]).first()
            if not supplier:
                supplier = Supplier(**s_data)
                db.add(supplier)
                db.commit()
                print(f"  [OK] Creado proveedor: {supplier.nombre}")

        categories_data = [
            {"nombre": "Poleras & Remeras", "descripcion": "Poleras oversized, slim fit y urbanas"},
            {"nombre": "Pantalones & Jeans", "descripcion": "Cargos, joggers, jeans rectos y relaxed"},
            {"nombre": "Sudaderas & Hoodies", "descripcion": "Polerones con capucha y crewnecks premium"},
            {"nombre": "Camisas", "descripcion": "Camisas manga corta, manga larga y estilo casual"},
            {"nombre": "Calzados Urbanos", "descripcion": "Zapatillas deportivas y de vestir urbano"},
            {"nombre": "Accesorios & Gorras", "descripcion": "Gorras snapback, bucket hats, cinturones y medias"}
        ]

        categories_map = {}
        for c_data in categories_data:
            cat = db.query(Category).filter(Category.nombre == c_data["nombre"]).first()
            if not cat:
                cat = Category(**c_data)
                db.add(cat)
                db.commit()
                db.refresh(cat)
                print(f"  [OK] Creada categoría: {cat.nombre}")
            categories_map[c_data["nombre"]] = cat

        # 5. Sembrar Productos y Variantes
        print("\n[5/6] Sembrando Productos y Variantes...")
        products_data = [
            {
                "nombre": "Polera Oversize FICTT Core",
                "descripcion": "Polera 100% algodón peinado 240g con estampado serigráfico de alta densidad.",
                "precio_base": Decimal("120.00"),
                "categoria_nombre": "Poleras & Remeras",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "https://images.unsplash.com/photo-1521572267360-ee0c2909d518?w=500&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "S", "color": "Negro", "sku": "POL-CORE-BLK-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Negro", "sku": "POL-CORE-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro", "sku": "POL-CORE-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Negro", "sku": "POL-CORE-BLK-XL", "precio_extra": Decimal("10.00")},
                    {"talla": "M", "color": "Blanco", "sku": "POL-CORE-WHT-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Blanco", "sku": "POL-CORE-WHT-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Pantalón Cargo Táctico Streetwear",
                "descripcion": "Pantalón cargo con 6 bolsillos multifuncionales, tela ripstop resistente y puño ajustable.",
                "precio_base": Decimal("220.00"),
                "categoria_nombre": "Pantalones & Jeans",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "https://images.unsplash.com/photo-1624378439575-d8705ad7ae80?w=500&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "30", "color": "Negro", "sku": "CARGO-TAC-BLK-30", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Negro", "sku": "CARGO-TAC-BLK-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Negro", "sku": "CARGO-TAC-BLK-34", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Verde Militar", "sku": "CARGO-TAC-MIL-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Beige", "sku": "CARGO-TAC-BGE-34", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Hoodie Premium Heavyweight FICTT",
                "descripcion": "Buzo con capucha frizado 400g con bolsillo canguro y cordones metálicos.",
                "precio_base": Decimal("280.00"),
                "categoria_nombre": "Sudaderas & Hoodies",
                "temporada": "Colección Especial 2025",
                "proveedor": "Confecciones del Oriente",
                "imagen_url": "https://images.unsplash.com/photo-1556905055-8f358a7a47b2?w=500&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "M", "color": "Gris Jaspe", "sku": "HD-PREM-GRY-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Gris Jaspe", "sku": "HD-PREM-GRY-L", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Negro", "sku": "HD-PREM-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro", "sku": "HD-PREM-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Negro", "sku": "HD-PREM-BLK-XL", "precio_extra": Decimal("15.00")},
                ]
            },
            {
                "nombre": "Camisa Casual Lino Rayada",
                "descripcion": "Camisa manga corta en tejido de lino liviano, cuello mao y corte relajado.",
                "precio_base": Decimal("180.00"),
                "categoria_nombre": "Camisas",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "https://images.unsplash.com/photo-1596755094514-f87e34085b2c?w=500&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "S", "color": "Celeste", "sku": "CAM-LIN-BLU-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Celeste", "sku": "CAM-LIN-BLU-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Celeste", "sku": "CAM-LIN-BLU-L", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Blanco", "sku": "CAM-LIN-WHT-M", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Zapatillas Urban Pro Runner",
                "descripcion": "Calzado deportivo urbano con suela EVA ultra liviana y capellada transpirable.",
                "precio_base": Decimal("350.00"),
                "categoria_nombre": "Calzados Urbanos",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Importadora Calzados & Streetwear",
                "imagen_url": "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=500&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "40", "color": "Blanco/Negro", "sku": "ZAP-URB-BW-40", "precio_extra": Decimal("0.00")},
                    {"talla": "41", "color": "Blanco/Negro", "sku": "ZAP-URB-BW-41", "precio_extra": Decimal("0.00")},
                    {"talla": "42", "color": "Blanco/Negro", "sku": "ZAP-URB-BW-42", "precio_extra": Decimal("0.00")},
                    {"talla": "41", "color": "Total Black", "sku": "ZAP-URB-TB-41", "precio_extra": Decimal("0.00")},
                    {"talla": "42", "color": "Total Black", "sku": "ZAP-URB-TB-42", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Gorra Snapback FICTT 3D",
                "descripcion": "Gorra estructurada de 6 paneles con bordado 3D frontal y visera plana.",
                "precio_base": Decimal("90.00"),
                "categoria_nombre": "Accesorios & Gorras",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "https://images.unsplash.com/photo-1588850561407-ed78c282e89b?w=500&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "UNICA", "color": "Negro", "sku": "GOR-3D-BLK-U", "precio_extra": Decimal("0.00")},
                    {"talla": "UNICA", "color": "Azul Marino", "sku": "GOR-3D-NVY-U", "precio_extra": Decimal("0.00")},
                ]
            }
        ]

        all_variants = []
        for p_info in products_data:
            cat_obj = categories_map.get(p_info["categoria_nombre"])
            if not cat_obj:
                continue

            product = db.query(Product).filter(Product.nombre == p_info["nombre"]).first()
            if not product:
                product = Product(
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
                print(f"  [OK] Creado producto: {product.nombre}")
            else:
                product.imagen_url = p_info.get("imagen_url")
                product.descripcion = p_info["descripcion"]
                product.precio_base = p_info["precio_base"]
                db.commit()
                print(f"  [+] Producto actualizado: {product.nombre}")

            # Variantes
            for v_data in p_info["variantes"]:
                variant = db.query(ProductVariant).filter(ProductVariant.sku == v_data["sku"]).first()
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
                all_variants.append(variant)

        # 6. Sembrar Inventario en Sucursales y Movimientos
        print("\n[6/6] Sembrando Inventario, Reservas y Ventas...")
        central_branch = branches_map.get("Sucursal Central")
        equipetrol_branch = branches_map.get("Sucursal Equipetrol")
        admin_user = users_map.get("admin@ficttstore.com")
        cajero_user = users_map.get("cajero@ficttstore.com")
        cliente_user = users_map.get("cliente@ficttstore.com")

        # Asignar stock en Central y Equipetrol
        inventory_items = []
        for v in all_variants:
            for branch in [central_branch, equipetrol_branch]:
                if not branch:
                    continue
                inv = db.query(Inventory).filter(
                    Inventory.sucursal_id == branch.id,
                    Inventory.variante_id == v.id
                ).first()

                if not inv:
                    stock_qty = 25 if branch.nombre == "Sucursal Central" else 15
                    inv = Inventory(
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
                        inventario_id=inv.id,
                        tipo=MovementType.ENTRADA,
                        cantidad=stock_qty,
                        stock_antes=0,
                        stock_despues=stock_qty,
                        referencia="RECEPCION-INICIAL-2025",
                        nota="Carga de inventario inicial por apertura de catálogo",
                        usuario_id=admin_user.id
                    )
                    db.add(mov)
                    db.commit()
                inventory_items.append(inv)

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
        if not res2 and equipetrol_branch and cliente_user and len(all_variants) >= 3:
            v3 = all_variants[2]
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

        # Sembrar Ventas de prueba
        sale1 = db.query(Sale).filter(Sale.numero_recibo == "VTA-2025-0001").first()
        if not sale1 and central_branch and cajero_user and cliente_user and len(all_variants) >= 2:
            v_sale = all_variants[0]
            sale1 = Sale(
                numero_recibo="VTA-2025-0001",
                tipo=SaleType.PRESENCIAL,
                estado=SaleStatus.COMPLETADA,
                sucursal_id=central_branch.id,
                cliente_id=cliente_user.id,
                cajero_id=cajero_user.id,
                metodo_pago=PaymentMethod.EFECTIVO,
                monto_total=Decimal("120.00"),
                monto_recibido=Decimal("150.00"),
                cambio=Decimal("30.00"),
                nota="Venta POS mostrador con factura",
                created_at=utc_now() - timedelta(hours=3)
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
            print("  [OK] Creada Venta de prueba: VTA-2025-0001 (Presencial / Efectivo / $120.00)")

        sale2 = db.query(Sale).filter(Sale.numero_recibo == "VTA-2025-0002").first()
        if not sale2 and equipetrol_branch and cajero_user and cliente_user and len(all_variants) >= 3:
            v_sale2 = all_variants[2]
            sale2 = Sale(
                numero_recibo="VTA-2025-0002",
                tipo=SaleType.DIGITAL,
                estado=SaleStatus.COMPLETADA,
                sucursal_id=equipetrol_branch.id,
                cliente_id=cliente_user.id,
                cajero_id=cajero_user.id,
                metodo_pago=PaymentMethod.QR,
                referencia_pago="QR-BNB-99882312",
                monto_total=Decimal("280.00"),
                monto_recibido=Decimal("280.00"),
                cambio=Decimal("0.00"),
                nota="Venta con pago QR generado",
                created_at=utc_now() - timedelta(hours=1)
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
            db.commit()
            print("  [OK] Creada Venta de prueba: VTA-2025-0002 (Digital / QR / $280.00)")

        print("\n==================================================")
        print(" [SUCCESS] BASE DE DATOS POBLADA EXITOSAMENTE! ")
        print("==================================================")
        print("Credenciales de prueba generadas:")
        print(" * Admin:     admin@ficttstore.com     / Password123!")
        print(" * Encargado: encargado@ficttstore.com / Password123!")
        print(" * Cajero:    cajero@ficttstore.com    / Password123!")
        print(" * Cliente:   cliente@ficttstore.com   / Password123!")
        print("==================================================")

    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] Falló el sembrado de base de datos: {e}")
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    seed_all()
