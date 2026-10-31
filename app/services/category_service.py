import uuid
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.category import Category
from app.models.product import Product
from app.schemas.category import CategoryCreate, CategoryUpdate


class CategoryService:
    @staticmethod
    def get_all(db: Session, activa_only: bool = False, tenant_id: Optional[uuid.UUID] = None) -> List[Category]:
        query = db.query(Category)
        if tenant_id:
            query = query.filter(Category.tenant_id == tenant_id)
        if activa_only:
            query = query.filter(Category.activa == True)
        return query.order_by(Category.nombre.asc()).all()

    @staticmethod
    def get_by_id(db: Session, category_id: uuid.UUID) -> Optional[Category]:
        return db.query(Category).filter(Category.id == category_id).first()

    @staticmethod
    def get_by_name(db: Session, nombre: str, tenant_id: Optional[uuid.UUID] = None) -> Optional[Category]:
        query = db.query(Category).filter(Category.nombre.ilike(nombre.strip()))
        if tenant_id:
            query = query.filter(Category.tenant_id == tenant_id)
        return query.first()

    @staticmethod
    def create(db: Session, category_in: CategoryCreate, tenant_id: Optional[uuid.UUID] = None) -> Category:
        nombre = category_in.nombre.strip()
        existing = CategoryService.get_by_name(db, nombre, tenant_id=tenant_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ya existe una categoría con el nombre '{nombre}'"
            )

        category = Category(
            tenant_id=tenant_id,
            nombre=nombre,
            descripcion=category_in.descripcion.strip() if category_in.descripcion else None,
            activa=category_in.activa
        )
        db.add(category)
        db.commit()
        db.refresh(category)
        return category

    @staticmethod
    def update(db: Session, category_id: uuid.UUID, category_in: CategoryUpdate) -> Category:
        category = CategoryService.get_by_id(db, category_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Categoría no encontrada"
            )

        if category_in.nombre is not None:
            nombre = category_in.nombre.strip()
            if nombre.lower() != category.nombre.lower():
                existing = CategoryService.get_by_name(db, nombre)
                if existing:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Ya existe una categoría con el nombre '{nombre}'"
                    )
            category.nombre = nombre

        if category_in.descripcion is not None:
            category.descripcion = category_in.descripcion.strip() if category_in.descripcion else None
        if category_in.activa is not None:
            category.activa = category_in.activa

        db.commit()
        db.refresh(category)
        return category

    @staticmethod
    def soft_delete(db: Session, category_id: uuid.UUID) -> Category:
        category = CategoryService.get_by_id(db, category_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Categoría no encontrada"
            )

        # Regla de negocio: Verificar si tiene productos asociados
        has_products = db.query(Product).filter(Product.categoria_id == category_id).first()
        if has_products:
            # Desactivar en lugar de eliminar
            category.activa = False
            db.commit()
            db.refresh(category)
            return category

        category.activa = False
        db.commit()
        db.refresh(category)
        return category
