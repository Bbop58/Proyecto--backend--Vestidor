import sys
import os
import uuid
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
from app.seed_permissions import seed as seed_permissions


def utc_now():
    return datetime.now(timezone.utc)


def seed_all():
    print("==================================================")
    print("Iniciando Sembrado Completo de la Base de Datos...")
    print("==================================================")
    
    # 0. Crear tablas si no existen
    print("\n[0/6] Creando esquema de tablas en la base de datos...")
    Base.metadata.create_all(bind=engine)

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
                supplier = Supplier(**s_data)
                db.add(supplier)
                db.commit()
                print(f"  [OK] Creado proveedor: {supplier.nombre}")

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
                cat = Category(**c_data)
                db.add(cat)
                db.commit()
                db.refresh(cat)
                print(f"  [OK] Creada categoría: {cat.nombre}")
            else:
                cat.descripcion = c_data["descripcion"]
                db.commit()
            categories_map[c_data["nombre"]] = cat

        # 5. Sembrar Productos y Variantes de Moda Masculina
        print("\n[5/6] Sembrando Productos y Variantes (Catálogo Masculino)...")
        products_data = [
            # 1. Poleras
            {
                "nombre": "Polera Oversize FICTT Core",
                "descripcion": "Polera 100% algodón peinado 240g de corte oversize con estampado serigráfico de alta densidad.",
                "precio_base": Decimal("120.00"),
                "categoria_nombre": "Poleras & Remeras",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "https://images.unsplash.com/photo-1521572267360-ee0c2909d518?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "S", "color": "Negro", "sku": "POL-CORE-BLK-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Negro", "sku": "POL-CORE-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro", "sku": "POL-CORE-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Negro", "sku": "POL-CORE-BLK-XL", "precio_extra": Decimal("10.00")},
                    {"talla": "M", "color": "Blanco Crudo", "sku": "POL-CORE-WHT-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Blanco Crudo", "sku": "POL-CORE-WHT-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Polera Heavyweight Boxy Fit",
                "descripcion": "Polera de estructura pesada 280g, cuello cerrado y hombros caídos de máxima durabilidad.",
                "precio_base": Decimal("130.00"),
                "categoria_nombre": "Poleras & Remeras",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "https://images.unsplash.com/photo-1583743814966-8936f5b7be1a?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "S", "color": "Gris Melange", "sku": "POL-BOXY-GRY-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Gris Melange", "sku": "POL-BOXY-GRY-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Gris Melange", "sku": "POL-BOXY-GRY-L", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Café Mocha", "sku": "POL-BOXY-MCH-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Café Mocha", "sku": "POL-BOXY-MCH-L", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Verde Oliva", "sku": "POL-BOXY-GRN-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Polera Gráfica Cyber Street",
                "descripcion": "Remera de estética streetwear futurista con serigrafía en la espalda e ilustración frontal.",
                "precio_base": Decimal("135.00"),
                "categoria_nombre": "Poleras & Remeras",
                "temporada": "Colección Streetwear FICTT 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "https://images.unsplash.com/photo-1503342217505-b0a15ec3261c?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "M", "color": "Negro Ácido", "sku": "POL-CYB-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro Ácido", "sku": "POL-CYB-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Negro Ácido", "sku": "POL-CYB-BLK-XL", "precio_extra": Decimal("10.00")},
                    {"talla": "M", "color": "Beige Arena", "sku": "POL-CYB-BGE-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Beige Arena", "sku": "POL-CYB-BGE-L", "precio_extra": Decimal("0.00")},
                ]
            },
            # 2. Hoodies
            {
                "nombre": "Hoodie Heavyweight Canguro FICTT",
                "descripcion": "Buzo con capucha frizado 400g con bolsillo canguro reforzado y cordones con puntas metálicas.",
                "precio_base": Decimal("280.00"),
                "categoria_nombre": "Hoodies & Polerones",
                "temporada": "Colección Streetwear FICTT 2025",
                "proveedor": "Confecciones del Oriente",
                "imagen_url": "https://images.unsplash.com/photo-1556905055-8f358a7a47b2?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "S", "color": "Negro", "sku": "HOD-HVY-BLK-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Negro", "sku": "HOD-HVY-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro", "sku": "HOD-HVY-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Negro", "sku": "HOD-HVY-BLK-XL", "precio_extra": Decimal("15.00")},
                    {"talla": "M", "color": "Gris Jaspe", "sku": "HOD-HVY-GRY-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Gris Jaspe", "sku": "HOD-HVY-GRY-L", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Azul Marino", "sku": "HOD-HVY-NVY-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Polerón Crewneck Minimalist",
                "descripcion": "Sweatshirt cuello redondo en felpa francesa de algodón premium sin capucha para estilo smart casual.",
                "precio_base": Decimal("240.00"),
                "categoria_nombre": "Hoodies & Polerones",
                "temporada": "Otoño-Invierno 2025",
                "proveedor": "Confecciones del Oriente",
                "imagen_url": "https://images.unsplash.com/photo-1620799140408-edc6dcb6d633?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "M", "color": "Verde Bosque", "sku": "CRW-MIN-GRN-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Verde Bosque", "sku": "CRW-MIN-GRN-L", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Negro", "sku": "CRW-MIN-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro", "sku": "CRW-MIN-BLK-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Hoodie Zip-Up Tech Fleece",
                "descripcion": "Chaqueta buzo con cierre frontal completo YKK, tejido térmico Tech Fleece y bolsillos con cremallera.",
                "precio_base": Decimal("310.00"),
                "categoria_nombre": "Hoodies & Polerones",
                "temporada": "Otoño-Invierno 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "https://images.unsplash.com/photo-1578587018452-892bacefd3f2?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "M", "color": "Carbón", "sku": "HOD-ZIP-DRK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Carbón", "sku": "HOD-ZIP-DRK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Gris Claro", "sku": "HOD-ZIP-LGT-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Gris Claro", "sku": "HOD-ZIP-LGT-L", "precio_extra": Decimal("0.00")},
                ]
            },
            # 3. Camisas
            {
                "nombre": "Camisa Casual Lino Cuello Mao",
                "descripcion": "Camisa manga corta en tejido de lino natural liviano, cuello mao y calce relajado para climas cálidos.",
                "precio_base": Decimal("180.00"),
                "categoria_nombre": "Camisas & Sobrecamisas",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "https://images.unsplash.com/photo-1596755094514-f87e34085b2c?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "S", "color": "Blanco", "sku": "CAM-LIN-WHT-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Blanco", "sku": "CAM-LIN-WHT-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Blanco", "sku": "CAM-LIN-WHT-L", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Celeste", "sku": "CAM-LIN-BLU-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Celeste", "sku": "CAM-LIN-BLU-L", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Arena", "sku": "CAM-LIN-SND-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Sobrecamisa Franela Leñadora",
                "descripcion": "Sobrecamisa gruesa de franela escocesa a cuadros, botones a presión y doble bolsillo en el pecho.",
                "precio_base": Decimal("210.00"),
                "categoria_nombre": "Camisas & Sobrecamisas",
                "temporada": "Otoño-Invierno 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "M", "color": "Rojo/Negro", "sku": "CAM-FLN-RED-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Rojo/Negro", "sku": "CAM-FLN-RED-L", "precio_extra": Decimal("0.00")},
                    {"talla": "XL", "color": "Rojo/Negro", "sku": "CAM-FLN-RED-XL", "precio_extra": Decimal("10.00")},
                    {"talla": "M", "color": "Verde/Negro", "sku": "CAM-FLN-GRN-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Verde/Negro", "sku": "CAM-FLN-GRN-L", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Camisa Resort Cuello Cubano",
                "descripcion": "Camisa veraniega con cuello abierto estilo camp collar en viscosa suave con caída fluida.",
                "precio_base": Decimal("165.00"),
                "categoria_nombre": "Camisas & Sobrecamisas",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "https://images.unsplash.com/photo-1607345366928-199ea26cfe3e?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "S", "color": "Negro Abstracto", "sku": "CAM-RST-BLK-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Negro Abstracto", "sku": "CAM-RST-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Negro Abstracto", "sku": "CAM-RST-BLK-L", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Blanco Botánico", "sku": "CAM-RST-WHT-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Blanco Botánico", "sku": "CAM-RST-WHT-L", "precio_extra": Decimal("0.00")},
                ]
            },
            # 4. Pantalones
            {
                "nombre": "Pantalón Cargo Táctico Streetwear",
                "descripcion": "Pantalón cargo con 6 bolsillos multifuncionales, confeccionado en tela ripstop resistente y puño ajustable.",
                "precio_base": Decimal("220.00"),
                "categoria_nombre": "Pantalones & Jeans",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "https://images.unsplash.com/photo-1624378439575-d8705ad7ae80?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "30", "color": "Negro", "sku": "CARGO-TAC-BLK-30", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Negro", "sku": "CARGO-TAC-BLK-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Negro", "sku": "CARGO-TAC-BLK-34", "precio_extra": Decimal("0.00")},
                    {"talla": "36", "color": "Negro", "sku": "CARGO-TAC-BLK-36", "precio_extra": Decimal("10.00")},
                    {"talla": "32", "color": "Verde Militar", "sku": "CARGO-TAC-MIL-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Verde Militar", "sku": "CARGO-TAC-MIL-34", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Beige", "sku": "CARGO-TAC-BGE-32", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Jeans Baggy Skate 90s",
                "descripcion": "Pantalón vaquero denim rígido 14oz con corte ancho noventero y tiro medio.",
                "precio_base": Decimal("235.00"),
                "categoria_nombre": "Pantalones & Jeans",
                "temporada": "Colección Streetwear FICTT 2025",
                "proveedor": "Streetwear Denim Lab",
                "imagen_url": "https://images.unsplash.com/photo-1541099649105-f69ad21f3246?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "30", "color": "Azul Claro Lavado", "sku": "JNS-BGY-LGT-30", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Azul Claro Lavado", "sku": "JNS-BGY-LGT-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Azul Claro Lavado", "sku": "JNS-BGY-LGT-34", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Negro Desgastado", "sku": "JNS-BGY-BLK-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Negro Desgastado", "sku": "JNS-BGY-BLK-34", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Pantalón Chino Slim Fit Stretch",
                "descripcion": "Pantalón chino formal-casual en gabardina de algodón con elastano para máxima comodidad.",
                "precio_base": Decimal("195.00"),
                "categoria_nombre": "Pantalones & Jeans",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "https://images.unsplash.com/photo-1473966968600-fa801b869a1a?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "30", "color": "Beige Khaki", "sku": "CHN-SLM-KHK-30", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Beige Khaki", "sku": "CHN-SLM-KHK-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Beige Khaki", "sku": "CHN-SLM-KHK-34", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Azul Marino", "sku": "CHN-SLM-NVY-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Negro", "sku": "CHN-SLM-BLK-34", "precio_extra": Decimal("0.00")},
                ]
            },
            # 5. Chaquetas
            {
                "nombre": "Chaqueta Bomber MA-1 Streetwear",
                "descripcion": "Chaqueta aviadora bomber con forro acolchado naranja, cremalleras metálicas y bolsillo en manga.",
                "precio_base": Decimal("350.00"),
                "categoria_nombre": "Chaquetas & Abrigos",
                "temporada": "Otoño-Invierno 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "https://images.unsplash.com/photo-1591047139829-d91aecb6caea?w=700&auto=format&fit=crop&q=80",
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
                "descripcion": "Chaqueta vaquera trucker clásica en denim grueso 100% algodón con botones metálicos grabados.",
                "precio_base": Decimal("310.00"),
                "categoria_nombre": "Chaquetas & Abrigos",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Streetwear Denim Lab",
                "imagen_url": "https://images.unsplash.com/photo-1576995853123-5a10305d93c0?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "S", "color": "Azul Clásico", "sku": "JKT-DNM-BLU-S", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Azul Clásico", "sku": "JKT-DNM-BLU-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Azul Clásico", "sku": "JKT-DNM-BLU-L", "precio_extra": Decimal("0.00")},
                    {"talla": "M", "color": "Denim Negro", "sku": "JKT-DNM-BLK-M", "precio_extra": Decimal("0.00")},
                    {"talla": "L", "color": "Denim Negro", "sku": "JKT-DNM-BLK-L", "precio_extra": Decimal("0.00")},
                ]
            },
            # 6. Shorts
            {
                "nombre": "Short Cargo Urbano Ripstop",
                "descripcion": "Bermuda cargo casual por encima de la rodilla, con bolsillos laterales y cintura elastizada con cordón.",
                "precio_base": Decimal("140.00"),
                "categoria_nombre": "Shorts & Bermudas",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Moda Urbana Bolivia",
                "imagen_url": "https://images.unsplash.com/photo-1591195853828-11db59a44f6b?w=700&auto=format&fit=crop&q=80",
                "variantes": [
                    {"talla": "30", "color": "Negro", "sku": "SHT-CRG-BLK-30", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Negro", "sku": "SHT-CRG-BLK-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Negro", "sku": "SHT-CRG-BLK-34", "precio_extra": Decimal("0.00")},
                    {"talla": "32", "color": "Verde Oliva", "sku": "SHT-CRG-OLV-32", "precio_extra": Decimal("0.00")},
                    {"talla": "34", "color": "Verde Oliva", "sku": "SHT-CRG-OLV-34", "precio_extra": Decimal("0.00")},
                ]
            },
            {
                "nombre": "Short Deportivo FICTT Athletics",
                "descripcion": "Short deportivo de secado rápido con forro interior transpirable y bolsillo oculto para teléfono.",
                "precio_base": Decimal("115.00"),
                "categoria_nombre": "Shorts & Bermudas",
                "temporada": "Primavera-Verano 2025",
                "proveedor": "Textilera Andina S.A.",
                "imagen_url": "https://images.unsplash.com/photo-1517838277536-f5f99be501cd?w=700&auto=format&fit=crop&q=80",
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
                product.categoria_id = cat_obj.id
                product.temporada = p_info["temporada"]
                product.proveedor = p_info["proveedor"]
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
                if not branch:
                    continue
                inv = db.query(Inventory).filter(
                    Inventory.sucursal_id == branch.id,
                    Inventory.variante_id == v.id
                ).first()

                if not inv:
                    stock_qty = stock_by_branch.get(branch.nombre, 15)
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
                        nota=f"Carga de inventario inicial para {branch.nombre}",
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
