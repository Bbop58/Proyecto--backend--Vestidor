import uuid
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.supplier import Supplier
from app.models.product import Product
from app.schemas.supplier import SupplierCreate, SupplierUpdate


class SupplierService:
    @staticmethod
    def get_all(db: Session, activo_only: bool = False) -> List[Supplier]:
        query = db.query(Supplier)
        if activo_only:
            query = query.filter(Supplier.activo == True)
        return query.order_by(Supplier.nombre.asc()).all()

    @staticmethod
    def get_by_id(db: Session, supplier_id: uuid.UUID) -> Optional[Supplier]:
        return db.query(Supplier).filter(Supplier.id == supplier_id).first()

    @staticmethod
    def get_by_name(db: Session, nombre: str) -> Optional[Supplier]:
        return db.query(Supplier).filter(Supplier.nombre.ilike(nombre.strip())).first()

    @staticmethod
    def create(db: Session, supplier_in: SupplierCreate) -> Supplier:
        nombre = supplier_in.nombre.strip()
        existing = SupplierService.get_by_name(db, nombre)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ya existe un proveedor con el nombre '{nombre}'"
            )

        supplier = Supplier(
            nombre=nombre,
            contacto=supplier_in.contacto.strip() if supplier_in.contacto else None,
            telefono=supplier_in.telefono.strip() if supplier_in.telefono else None,
            email=str(supplier_in.email).strip().lower() if supplier_in.email else None,
            direccion=supplier_in.direccion.strip() if supplier_in.direccion else None,
            activo=supplier_in.activo
        )
        db.add(supplier)
        db.commit()
        db.refresh(supplier)
        return supplier

    @staticmethod
    def update(db: Session, supplier_id: uuid.UUID, supplier_in: SupplierUpdate) -> Supplier:
        supplier = SupplierService.get_by_id(db, supplier_id)
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Proveedor no encontrado"
            )

        if supplier_in.nombre is not None:
            nombre = supplier_in.nombre.strip()
            if nombre.lower() != supplier.nombre.lower():
                existing = SupplierService.get_by_name(db, nombre)
                if existing:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Ya existe un proveedor con el nombre '{nombre}'"
                    )
            supplier.nombre = nombre

        if supplier_in.contacto is not None:
            supplier.contacto = supplier_in.contacto.strip() if supplier_in.contacto else None
        if supplier_in.telefono is not None:
            supplier.telefono = supplier_in.telefono.strip() if supplier_in.telefono else None
        if supplier_in.email is not None:
            supplier.email = str(supplier_in.email).strip().lower() if supplier_in.email else None
        if supplier_in.direccion is not None:
            supplier.direccion = supplier_in.direccion.strip() if supplier_in.direccion else None
        if supplier_in.activo is not None:
            supplier.activo = supplier_in.activo

        db.commit()
        db.refresh(supplier)
        return supplier

    @staticmethod
    def soft_delete(db: Session, supplier_id: uuid.UUID) -> Supplier:
        supplier = SupplierService.get_by_id(db, supplier_id)
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Proveedor no encontrado"
            )

        # Regla de negocio: Verificar si tiene productos asociados
        has_products = db.query(Product).filter(Product.proveedor.ilike(supplier.nombre)).first()
        if has_products:
            # Soft delete
            supplier.activo = False
            db.commit()
            db.refresh(supplier)
            return supplier

        supplier.activo = False
        db.commit()
        db.refresh(supplier)
        return supplier
