import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from app.database import SessionLocal
from app.models.user import User
from app.models.role import Role

db = SessionLocal()
users = db.query(User).all()
if not users:
    print("No hay usuarios registrados en la base de datos.")
else:
    for u in users:
        role = db.query(Role).filter(Role.id == u.role_id).first() if u.role_id else None
        role_name = role.name if role else "Sin rol"
        perms = len(role.permissions) if role and role.permissions else 0
        print(f"  Email: {u.email} | Rol: {role_name} | Permisos: {perms}")
db.close()
