import uuid
from typing import Optional, List
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.role import Role
from app.schemas.user import UserCreate, UserUpdate, UserResponse
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

