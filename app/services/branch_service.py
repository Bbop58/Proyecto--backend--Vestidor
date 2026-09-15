import uuid
import re
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.branch import Branch
from app.schemas.branch import BranchCreate, BranchUpdate


class BranchService:
    @staticmethod
    def get_all(db: Session, activa_only: bool = False) -> List[Branch]:
        query = db.query(Branch)
        if activa_only:
            query = query.filter(Branch.activa == True)
        return query.order_by(Branch.nombre.asc()).all()

    @staticmethod
    def get_by_id(db: Session, branch_id: uuid.UUID) -> Optional[Branch]:
        return db.query(Branch).filter(Branch.id == branch_id).first()

    @staticmethod
    def get_by_name(db: Session, nombre: str) -> Optional[Branch]:
        return db.query(Branch).filter(Branch.nombre.ilike(nombre.strip())).first()

    @staticmethod
    def create(db: Session, branch_in: BranchCreate) -> Branch:
        nombre = branch_in.nombre.strip()
        if not re.match(r"^[\w\sÁÉÍÓÚáéíóúÑñ.,-]+$", nombre, re.UNICODE):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El nombre de la sucursal contiene caracteres inválidos"
            )

        existing = BranchService.get_by_name(db, nombre)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ya existe una sucursal con el nombre '{nombre}'"
            )

        branch = Branch(
            nombre=nombre,
            ciudad=branch_in.ciudad.strip(),
            direccion=branch_in.direccion.strip(),
            telefono=branch_in.telefono.strip() if branch_in.telefono else None,
            activa=branch_in.activa
        )
        db.add(branch)
        db.commit()
        db.refresh(branch)
        return branch

    @staticmethod
    def update(db: Session, branch_id: uuid.UUID, branch_in: BranchUpdate) -> Branch:
        branch = BranchService.get_by_id(db, branch_id)
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sucursal no encontrada"
            )

        if branch_in.nombre is not None:
            nombre = branch_in.nombre.strip()
            if nombre.lower() != branch.nombre.lower():
                existing = BranchService.get_by_name(db, nombre)
                if existing:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Ya existe una sucursal con el nombre '{nombre}'"
                    )
            branch.nombre = nombre

        if branch_in.ciudad is not None:
            branch.ciudad = branch_in.ciudad.strip()
        if branch_in.direccion is not None:
            branch.direccion = branch_in.direccion.strip()
        if branch_in.telefono is not None:
            branch.telefono = branch_in.telefono.strip() if branch_in.telefono else None
        if branch_in.activa is not None:
            branch.activa = branch_in.activa

        db.commit()
        db.refresh(branch)
        return branch

    @staticmethod
    def soft_delete(db: Session, branch_id: uuid.UUID) -> Branch:
        branch = BranchService.get_by_id(db, branch_id)
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sucursal no encontrada"
            )

        # Regla de negocio: soft delete
        branch.activa = False
        db.commit()
        db.refresh(branch)
        return branch
