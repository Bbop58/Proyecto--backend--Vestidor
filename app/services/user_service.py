import uuid
from typing import Optional, List
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.role import Role
from app.schemas.user import (
    UserCreate,
    UserUpdate,
    UserResponse,
    UserAdminCreate,
    UserAdminUpdate
)
from app.core.security import get_password_hash



class UserService:
    @staticmethod
    def get_by_id(db: Session, user_id: uuid.UUID) -> Optional[User]:
        return db.query(User).filter(User.id == user_id).first()

    @staticmethod
    def get_by_email(db: Session, email: str) -> Optional[User]:
        return db.query(User).filter(User.email == email.lower().strip()).first()

    @staticmethod
    def get_all(db: Session) -> List[User]:
        return db.query(User).all()

    @staticmethod
    def create(db: Session, user_in: UserCreate) -> User:
        role_id = user_in.role_id
        if not role_id:
            user_count = db.query(User).count()
            default_role_name = "admin" if user_count == 0 else "cliente"
            default_role = db.query(Role).filter(Role.name == default_role_name).first()
            if not default_role:
                default_role = Role(name=default_role_name, description=f"{default_role_name.capitalize()} del sistema")
                db.add(default_role)
                db.commit()
                db.refresh(default_role)
            role_id = default_role.id


        db_user = User(
            email=user_in.email.lower().strip(),
            full_name=user_in.full_name.strip(),
            hashed_password=get_password_hash(user_in.password),
            role_id=role_id,
            is_active=True,
            is_verified=False
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user

    @staticmethod
    def update(db: Session, db_user: User, user_in: UserUpdate) -> User:
        if user_in.full_name is not None:
            db_user.full_name = user_in.full_name.strip()
        if user_in.email is not None:
            db_user.email = user_in.email.lower().strip()
        if user_in.role_id is not None:
            db_user.role_id = user_in.role_id
        db.commit()
        db.refresh(db_user)
        return db_user
    @staticmethod
    def build_user_response(db: Session, user: User) -> UserResponse:
        from app.schemas.user import UserResponse
        from app.services.role_service import RoleService
        res = UserResponse.model_validate(user)
        if user.role_id:
            res.permissions = RoleService.get_permission_codes(db, user.role_id)
        return res

    @staticmethod
    def create_by_admin(db: Session, user_in: UserAdminCreate) -> User:
        existing = UserService.get_by_email(db, user_in.email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ya existe un usuario registrado con este correo electrónico."
            )

        if user_in.role_id:
            role = db.query(Role).filter(Role.id == user_in.role_id).first()
            if not role:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="El rol especificado no existe."
                )
        else:
            default_role = db.query(Role).filter(Role.name == "cliente").first()
            user_in.role_id = default_role.id if default_role else None

        db_user = User(
            email=user_in.email.lower().strip(),
            full_name=user_in.full_name.strip(),
            hashed_password=get_password_hash(user_in.password),
            role_id=user_in.role_id,
            is_active=user_in.is_active,
            is_verified=True
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user

    @staticmethod
    def update_by_admin(
        db: Session,
        db_user: User,
        user_in: UserAdminUpdate,
        admin_user: User
    ) -> User:
        if user_in.email is not None and user_in.email.lower().strip() != db_user.email:
            existing = UserService.get_by_email(db, user_in.email)
            if existing and existing.id != db_user.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ya existe otro usuario registrado con este correo electrónico."
                )
            db_user.email = user_in.email.lower().strip()

        # Evitar auto-bloqueo del administrador actual
        if db_user.id == admin_user.id:
            if user_in.is_active is False:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No puedes desactivar tu propia cuenta de administrador."
                )
            if user_in.role_id is not None and user_in.role_id != db_user.role_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No puedes cambiar el rol de tu propia cuenta de administrador."
                )

        if user_in.full_name is not None:
            db_user.full_name = user_in.full_name.strip()

        if user_in.role_id is not None:
            role = db.query(Role).filter(Role.id == user_in.role_id).first()
            if not role:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="El rol especificado no existe."
                )
            db_user.role_id = user_in.role_id

        if user_in.is_active is not None:
            db_user.is_active = user_in.is_active

        if user_in.password:
            db_user.hashed_password = get_password_hash(user_in.password)

        db.commit()
        db.refresh(db_user)
        return db_user

    @staticmethod
    def toggle_active(db: Session, db_user: User, admin_user: User) -> User:
        if db_user.id == admin_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No puedes desactivar tu propia cuenta de administrador."
            )
        db_user.is_active = not db_user.is_active
        db.commit()
        db.refresh(db_user)
        return db_user


