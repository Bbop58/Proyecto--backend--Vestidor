import uuid
from typing import List, Optional, Dict
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.role import Role
from app.models.permission import Permission
from app.schemas.role import RoleCreate, RoleUpdate


class RoleService:
    @staticmethod
    def get_roles(db: Session) -> List[Role]:
        return db.query(Role).all()

    @staticmethod
    def get_role_by_id(db: Session, role_id: uuid.UUID) -> Optional[Role]:
        return db.query(Role).filter(Role.id == role_id).first()

    @staticmethod
    def get_role_by_name(db: Session, name: str) -> Optional[Role]:
        return db.query(Role).filter(Role.name == name).first()

    @staticmethod
    def create_role(db: Session, role_in: RoleCreate) -> Role:
        existing = RoleService.get_role_by_name(db, role_in.name)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El rol '{role_in.name}' ya existe"
            )

        role = Role(
            name=role_in.name,
            description=role_in.description
        )

        if role_in.permission_ids:
            permissions = db.query(Permission).filter(Permission.id.in_(role_in.permission_ids)).all()
            role.permissions = permissions

        db.add(role)
        db.commit()
        db.refresh(role)
        return role

    @staticmethod
    def update_role(db: Session, role_id: uuid.UUID, role_in: RoleUpdate) -> Role:
        role = RoleService.get_role_by_id(db, role_id)
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Rol no encontrado"
            )

        if role_in.name and role_in.name != role.name:
            if role.name == "admin":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No se puede cambiar el nombre del rol 'admin'"
                )
            existing = RoleService.get_role_by_name(db, role_in.name)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"El rol '{role_in.name}' ya existe"
                )
            role.name = role_in.name

        if role_in.description is not None:
            role.description = role_in.description

        db.commit()
        db.refresh(role)
        return role

    @staticmethod
    def delete_role(db: Session, role_id: uuid.UUID, current_user_role_id: uuid.UUID) -> bool:
        role = RoleService.get_role_by_id(db, role_id)
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Rol no encontrado"
            )

        if role.name == "admin":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El rol 'admin' no puede ser eliminado"
            )

        if role.id == current_user_role_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No puedes eliminar el rol al que perteneces"
            )

        db.delete(role)
        db.commit()
        return True

    @staticmethod
    def assign_permissions(db: Session, role_id: uuid.UUID, permission_ids: List[uuid.UUID]) -> Role:
        role = RoleService.get_role_by_id(db, role_id)
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Rol no encontrado"
            )

        permissions = db.query(Permission).filter(Permission.id.in_(permission_ids)).all()

        if role.name == "admin":
            roles_update_perm = db.query(Permission).filter(Permission.code == "roles.update").first()
            if roles_update_perm and roles_update_perm not in permissions:
                permissions.append(roles_update_perm)

        role.permissions = permissions
        db.commit()
        db.refresh(role)
        return role

    @staticmethod
    def get_permission_codes(db: Session, role_id: uuid.UUID) -> List[str]:
        role = RoleService.get_role_by_id(db, role_id)
        if not role:
            return []
        return [p.code for p in role.permissions]

    @staticmethod
    def get_all_permissions(db: Session) -> List[Permission]:
        return db.query(Permission).all()

    @staticmethod
    def get_grouped_permissions(db: Session) -> List[Dict]:
        permissions = db.query(Permission).order_by(Permission.module, Permission.code).all()
        grouped: Dict[str, List] = {}
        for p in permissions:
            if p.module not in grouped:
                grouped[p.module] = []
            grouped[p.module].append(p)
        return [{"module": module, "permissions": perms} for module, perms in grouped.items()]
