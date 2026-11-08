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
    def get_all(db: Session, tenant_id: Optional[uuid.UUID] = None) -> List[User]:
        query = db.query(User)
        if tenant_id:
            query = query.filter(User.tenant_id == tenant_id)
        return query.all()

    @staticmethod
    def create(db: Session, user_in: UserCreate) -> User:
        role_id = user_in.role_id
        if not role_id:
            default_role_name = "cliente"
            default_role = db.query(Role).filter(Role.name == default_role_name).first()
            if not default_role:
                default_role = Role(name=default_role_name, description=f"{default_role_name.capitalize()} del sistema")
                db.add(default_role)
                db.commit()
                db.refresh(default_role)
            role_id = default_role.id


        db_user = User(
            tenant_id=getattr(user_in,"tenant_id",None) or db.info.get("tenant_scope"),
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
            raise HTTPException(403,"El perfil personal no permite cambiar el rol")
        db.commit()
        db.refresh(db_user)
        return db_user
    @staticmethod
    def build_user_response(db: Session, user: User) -> UserResponse:
        from app.schemas.user import UserResponse
        from app.services.role_service import RoleService
        res = UserResponse.model_validate(user)
        if not user.is_platform_admin:
            from app.services.entitlement_service import EntitlementService
            entitlement=EntitlementService.resolve(db,user)
            res.service_restricted=entitlement['service_restricted']
            res.subscription_capabilities=entitlement['features']
        if user.role_id and not user.is_platform_admin:
            res.permissions = RoleService.get_permission_codes(db, user.role_id)
            if not user.tenant.activo:
                res.permissions=[code for code in res.permissions if code=='subscriptions.manage']
        if user.tenant:
            res.tenant_name = user.tenant.nombre
            res.tenant_logo_url = user.tenant.logo_url
            res.tenant_color_primario = user.tenant.color_primario or "#4f46e5"
            res.tenant_color_secundario = user.tenant.color_secundario or "#818cf8"
            res.tenant_eslogan = user.tenant.eslogan
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
            tenant_id=getattr(user_in,"tenant_id",None) or db.info.get("tenant_scope"),
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
            from app.services.mobile_session_service import MobileSessionService
            MobileSessionService.lock_user(db, db_user.id)
            db_user.is_active = user_in.is_active
            if not user_in.is_active:
                MobileSessionService.revoke_user(db, db_user.id)

        if user_in.password:
            from app.services.mobile_session_service import MobileSessionService
            MobileSessionService.lock_user(db, db_user.id)
            db_user.hashed_password = get_password_hash(user_in.password)
            MobileSessionService.revoke_user(db, db_user.id)

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
        from app.services.mobile_session_service import MobileSessionService
        MobileSessionService.lock_user(db, db_user.id)
        db.refresh(db_user)
        db_user.is_active = not db_user.is_active
        if not db_user.is_active:
            MobileSessionService.revoke_user(db, db_user.id)
        db.commit()
        db.refresh(db_user)
        return db_user


