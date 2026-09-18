import sys
import os
from datetime import date

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal
from app.models.role import Role
from app.models.permission import Permission
from app.models.season import Season

PERMISSIONS_SEED = [
    # Módulo Usuarios
    {
        "code": "users.list",
        "module": "Usuarios",
        "method": "GET",
        "path": "/api/v1/users",
        "description": "Listar usuarios del sistema"
    },
    {
        "code": "users.create",
        "module": "Usuarios",
        "method": "POST",
        "path": "/api/v1/users",
        "description": "Crear nuevos usuarios"
    },
    {
        "code": "users.update",
        "module": "Usuarios",
        "method": "PUT",
        "path": "/api/v1/users/{id}",
        "description": "Actualizar usuarios"
    },
    {
        "code": "users.delete",
        "module": "Usuarios",
        "method": "DELETE",
        "path": "/api/v1/users/{id}",
        "description": "Eliminar usuarios"
    },
    # Módulo Roles y Permisos
    {
        "code": "roles.list",
        "module": "Roles y Permisos",
        "method": "GET",
        "path": "/api/v1/roles",
        "description": "Listar roles y matriz de permisos"
    },
    {
        "code": "roles.create",
        "module": "Roles y Permisos",
        "method": "POST",
        "path": "/api/v1/roles",
        "description": "Crear nuevos roles"
    },
    {
        "code": "roles.update",
        "module": "Roles y Permisos",
        "method": "PUT",
        "path": "/api/v1/roles/{id}",
        "description": "Editar roles y actualizar permisos"
    },
    {
        "code": "roles.delete",
        "module": "Roles y Permisos",
        "method": "DELETE",
        "path": "/api/v1/roles/{id}",
        "description": "Eliminar roles"
    },
    # Módulo Sucursales (Fase 1)
    {
        "code": "sucursales.list",
        "module": "Sucursales",
        "method": "GET",
        "path": "/api/v1/sucursales",
        "description": "Listar y ver detalle de sucursales"
    },
    {
        "code": "sucursales.create",
        "module": "Sucursales",
        "method": "POST",
        "path": "/api/v1/sucursales",
        "description": "Crear nuevas sucursales"
    },
    {
        "code": "sucursales.update",
        "module": "Sucursales",
        "method": "PUT",
        "path": "/api/v1/sucursales/{id}",
        "description": "Editar información de sucursales"
    },
    {
        "code": "sucursales.delete",
        "module": "Sucursales",
        "method": "DELETE",
        "path": "/api/v1/sucursales/{id}",
        "description": "Desactivar sucursales (Soft delete)"
    },
    # Módulo Categorías (Fase 1)
    {
        "code": "categorias.list",
        "module": "Categorías",
        "method": "GET",
        "path": "/api/v1/categorias",
        "description": "Listar categorías del catálogo"
    },
    {
        "code": "categorias.create",
        "module": "Categorías",
        "method": "POST",
        "path": "/api/v1/categorias",
        "description": "Crear nuevas categorías"
    },
    {
        "code": "categorias.update",
        "module": "Categorías",
        "method": "PUT",
        "path": "/api/v1/categorias/{id}",
        "description": "Editar categorías"
    },
    {
        "code": "categorias.delete",
        "module": "Categorías",
        "method": "DELETE",
        "path": "/api/v1/categorias/{id}",
        "description": "Desactivar categorías"
    },
    # Módulo Productos (Fase 1)
    {
        "code": "productos.list",
        "module": "Productos",
        "method": "GET",
        "path": "/api/v1/productos",
        "description": "Listar y consultar catálogo de productos"
    },
    {
        "code": "productos.create",
        "module": "Productos",
        "method": "POST",
        "path": "/api/v1/productos",
        "description": "Crear nuevos productos en catálogo"
    },
    {
        "code": "productos.update",
        "module": "Productos",
        "method": "PUT",
        "path": "/api/v1/productos/{id}",
        "description": "Editar productos existentes"
    },
    {
        "code": "productos.delete",
        "module": "Productos",
        "method": "DELETE",
        "path": "/api/v1/productos/{id}",
        "description": "Desactivar productos del catálogo"
    },
    # Módulo Variantes (Fase 1)
    {
        "code": "variantes.list",
        "module": "Variantes",
        "method": "GET",
        "path": "/api/v1/variantes",
        "description": "Listar tallas y colores de productos"
    },
    {
        "code": "variantes.create",
        "module": "Variantes",
        "method": "POST",
        "path": "/api/v1/productos/{id}/variantes",
        "description": "Crear variantes (tallas, colores, SKU)"
    },
    {
        "code": "variantes.update",
        "module": "Variantes",
        "method": "PUT",
        "path": "/api/v1/variantes/{id}",
        "description": "Editar variantes de productos"
    },
    {
        "code": "variantes.delete",
        "module": "Variantes",
        "method": "DELETE",
        "path": "/api/v1/variantes/{id}",
        "description": "Desactivar variantes de productos"
    },
    # Módulo Proveedores (Fase 2)
    {
        "code": "proveedores.list",
        "module": "Proveedores",
        "method": "GET",
        "path": "/api/v1/proveedores",
        "description": "Listar proveedores registrados"
    },
    {
        "code": "proveedores.create",
        "module": "Proveedores",
        "method": "POST",
        "path": "/api/v1/proveedores",
        "description": "Crear nuevos proveedores"
    },
    {
        "code": "proveedores.update",
        "module": "Proveedores",
        "method": "PUT",
        "path": "/api/v1/proveedores/{id}",
        "description": "Editar información de proveedores"
    },
    {
        "code": "proveedores.delete",
        "module": "Proveedores",
        "method": "DELETE",
        "path": "/api/v1/proveedores/{id}",
        "description": "Desactivar proveedores"
    },
    # Módulo Temporadas (Fase 2)
    {
        "code": "temporadas.list",
        "module": "Temporadas",
        "method": "GET",
        "path": "/api/v1/temporadas",
        "description": "Listar temporadas comerciales"
    },
    {
        "code": "temporadas.create",
        "module": "Temporadas",
        "method": "POST",
        "path": "/api/v1/temporadas",
        "description": "Crear nuevas temporadas"
    },
    {
        "code": "temporadas.update",
        "module": "Temporadas",
        "method": "PUT",
        "path": "/api/v1/temporadas/{id}",
        "description": "Editar y activar temporadas"
    },
    {
        "code": "temporadas.delete",
        "module": "Temporadas",
        "method": "DELETE",
        "path": "/api/v1/temporadas/{id}",
        "description": "Eliminar temporadas"
    },
    # Módulo Inventario (Fase 3)
    {
        "code": "inventario.list",
        "module": "Inventario",
        "method": "GET",
        "path": "/api/v1/inventario",
        "description": "Ver stock e historial de movimientos"
    },
    {
        "code": "inventario.create",
        "module": "Inventario",
        "method": "POST",
        "path": "/api/v1/inventario/recepcion",
        "description": "Recibir productos en una sucursal"
    },
    {
        "code": "inventario.update",
        "module": "Inventario",
        "method": "PUT",
        "path": "/api/v1/inventario/{id}",
        "description": "Ajustar stock y configuración de inventario"
    },
    # Módulo Reservas (Fase 4)
    {
        "code": "reservas.list",
        "module": "Reservas",
        "method": "GET",
        "path": "/api/v1/reservas",
        "description": "Listar y ver reservas"
    },
    {
        "code": "reservas.create",
        "module": "Reservas",
        "method": "POST",
        "path": "/api/v1/reservas",
        "description": "Crear reservas"
    },
    {
        "code": "reservas.update",
        "module": "Reservas",
        "method": "PATCH",
        "path": "/api/v1/reservas/{id}",
        "description": "Preparar, recoger, cancelar o expirar reservas"
    },
    {
        "code": "reservas.delete",
        "module": "Reservas",
        "method": "DELETE",
        "path": "/api/v1/reservas/{id}",
        "description": "Eliminar o anular reservas"
    },
    # Módulo Ventas (Fase 5)
    {
        "code": "ventas.list",
        "module": "Ventas",
        "method": "GET",
        "path": "/api/v1/ventas",
        "description": "Ver listado y detalle de ventas"
    },
    {
        "code": "ventas.create",
        "module": "Ventas",
        "method": "POST",
        "path": "/api/v1/ventas",
        "description": "Registrar ventas presenciales o digitales"
    },
    {
        "code": "ventas.cancel",
        "module": "Ventas",
        "method": "PATCH",
        "path": "/api/v1/ventas/{id}/cancelar",
        "description": "Anular ventas y revertir inventario"
    },
    {
        "code": "ventas.reports",
        "module": "Ventas",
        "method": "GET",
        "path": "/api/v1/ventas/reportes",
        "description": "Ver métricas e informes de ventas"
    },
]

SEASONS_SEED = [
    {
        "nombre": "Primavera-Verano 2025",
        "año": 2025,
        "fecha_inicio": date(2025, 9, 21),
        "fecha_fin": date(2026, 3, 20),
        "activa": True
    },
    {
        "nombre": "Otoño-Invierno 2025",
        "año": 2025,
        "fecha_inicio": date(2025, 3, 21),
        "fecha_fin": date(2025, 9, 20),
        "activa": False
    },
    {
        "nombre": "Escolar 2025",
        "año": 2025,
        "fecha_inicio": date(2025, 1, 15),
        "fecha_fin": date(2025, 4, 30),
        "activa": False
    },
    {
        "nombre": "Colección Especial 2025",
        "año": 2025,
        "fecha_inicio": date(2025, 11, 1),
        "fecha_fin": date(2025, 12, 31),
        "activa": False
    }
]


def seed(db=None):
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        # Roles base
        roles_to_ensure = [
            ("admin", "Administrador con control total del sistema"),
            ("encargado", "Encargado de sucursal e inventario"),
            ("cajero", "Cajero para ventas presenciales"),
            ("cliente", "Cliente para app móvil y reservas"),
        ]

        for r_name, r_desc in roles_to_ensure:
            role = db.query(Role).filter(Role.name == r_name).first()
            if not role:
                role = Role(name=r_name, description=r_desc)
                db.add(role)
        db.commit()

        # Permisos
        for p_data in PERMISSIONS_SEED:
            perm = db.query(Permission).filter(Permission.code == p_data["code"]).first()
            if not perm:
                perm = Permission(**p_data)
                db.add(perm)
            else:
                perm.module = p_data["module"]
                perm.method = p_data["method"]
                perm.path = p_data["path"]
                perm.description = p_data["description"]
        db.commit()

        # Temporadas predefinidas
        for s_data in SEASONS_SEED:
            season = db.query(Season).filter(Season.nombre == s_data["nombre"]).first()
            if not season:
                season = Season(**s_data)
                db.add(season)
        db.commit()

        # Assign all seeded permissions to admin role
        admin_role = db.query(Role).filter(Role.name == "admin").first()
        if admin_role:
            admin_role.permissions = db.query(Permission).all()

        # Default permissions for encargado
        encargado_role = db.query(Role).filter(Role.name == "encargado").first()
        if encargado_role:
            encargado_codes = [
                "sucursales.list", "categorias.list", "productos.list", "variantes.list",
                "proveedores.list", "temporadas.list",
                "inventario.list", "inventario.create", "inventario.update",
                "reservas.list", "reservas.update",
                "ventas.list", "ventas.create", "ventas.reports", "ventas.cancel"
            ]
            encargado_role.permissions = db.query(Permission).filter(Permission.code.in_(encargado_codes)).all()

        # Default permissions for cajero
        cajero_role = db.query(Role).filter(Role.name == "cajero").first()
        if cajero_role:
            cajero_codes = [
                "sucursales.list", "categorias.list", "productos.list", "variantes.list",
                "temporadas.list",
                "inventario.list", "reservas.list", "reservas.update",
                "ventas.list", "ventas.create", "ventas.reports"
            ]
            cajero_role.permissions = db.query(Permission).filter(Permission.code.in_(cajero_codes)).all()

        # Default permissions for cliente
        cliente_role = db.query(Role).filter(Role.name == "cliente").first()
        if cliente_role:
            cliente_codes = [
                "sucursales.list", "categorias.list", "productos.list", "variantes.list",
                "temporadas.list", "reservas.create"
            ]
            cliente_role.permissions = db.query(Permission).filter(Permission.code.in_(cliente_codes)).all()

        db.commit()
        print("[SUCCESS] Permisos, roles y temporadas sembrados exitosamente.")
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Error al sembrar: {e}")
        raise e
    finally:
        if close_db:
            db.close()


if __name__ == "__main__":
    seed()
