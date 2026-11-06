import uuid
import re
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.branch import Branch
from app.schemas.branch import BranchCreate, BranchUpdate


class BranchService:
    @staticmethod
    def _check_quota(db,tenant_id,reactivating_id=None):
        from sqlalchemy import select,func
        from app.models.subscription_plan import SubscriptionPlan
        from app.models.subscription_plan_version import SubscriptionPlanVersion
        from app.services.subscription_contract_service import SubscriptionContractService as Contracts
        connection=db.connection();Contracts.lock_company(connection,tenant_id)
        if reactivating_id and connection.execute(select(Branch.activa).where(Branch.id==reactivating_id,Branch.tenant_id==tenant_id)).scalar_one():
            return
        sub=Contracts._subscription(connection,tenant_id)
        from types import SimpleNamespace
        from app.models.tenant import Tenant
        from app.services.entitlement_service import EntitlementService
        if not sub:raise HTTPException(403,'Falta un período contratado vigente')
        effective=EntitlementService.check(db,SimpleNamespace(id=None,tenant_id=tenant_id,
            tenant=db.get(Tenant,tenant_id),is_active=True,role=None))
        current=effective['term']
        plan_query=select(SubscriptionPlan.nombre,SubscriptionPlan.max_sucursales)
        if current and current['version_id']:
            plan_query=plan_query.join(SubscriptionPlanVersion,SubscriptionPlanVersion.plan_id==SubscriptionPlan.id).where(SubscriptionPlanVersion.id==current['version_id'])
        else:plan_query=plan_query.where(SubscriptionPlan.id==sub['plan_id'])
        plan=connection.execute(plan_query).one()
        cap=current['max_sucursales'] if current else plan.max_sucursales
        active=connection.execute(select(func.count(Branch.id)).where(Branch.tenant_id==tenant_id,Branch.activa.is_(True))).scalar_one()
        if active>=cap:raise HTTPException(400,f"Límite de sucursales alcanzado: el contrato vigente '{plan.nombre}' permite {cap} sucursales activas")

    @staticmethod
    def get_all(db: Session, activa_only: bool = False, tenant_id: Optional[uuid.UUID] = None) -> List[Branch]:
        query = db.query(Branch)
        if tenant_id:
            query = query.filter(Branch.tenant_id == tenant_id)
        if activa_only:
            query = query.filter(Branch.activa == True)
        return query.order_by(Branch.nombre.asc()).all()

    @staticmethod
    def get_by_id(db: Session, branch_id: uuid.UUID, tenant_id: Optional[uuid.UUID] = None) -> Optional[Branch]:
        query = db.query(Branch).filter(Branch.id == branch_id)
        if tenant_id:
            query = query.filter(Branch.tenant_id == tenant_id)
        return query.first()

    @staticmethod
    def get_by_name(db: Session, nombre: str, tenant_id: Optional[uuid.UUID] = None) -> Optional[Branch]:
        query = db.query(Branch).filter(Branch.nombre.ilike(nombre.strip()))
        if tenant_id:
            query = query.filter(Branch.tenant_id == tenant_id)
        return query.first()

    @staticmethod
    def create(db: Session, branch_in: BranchCreate, tenant_id: Optional[uuid.UUID] = None) -> Branch:
        nombre = branch_in.nombre.strip()
        if not re.match(r"^[\w\sÁÉÍÓÚáéíóúÑñ.,-]+$", nombre, re.UNICODE):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El nombre de la sucursal contiene caracteres inválidos"
            )

        if tenant_id and branch_in.activa:
            BranchService._check_quota(db,tenant_id)

        existing = BranchService.get_by_name(db, nombre, tenant_id=tenant_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ya existe una sucursal con el nombre '{nombre}' en su empresa"
            )

        branch = Branch(
            tenant_id=tenant_id,
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

        if branch_in.activa is True and not branch.activa:
            BranchService._check_quota(db,branch.tenant_id,branch.id)

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

        from app.services.subscription_contract_service import SubscriptionContractService
        SubscriptionContractService.lock_company(db.connection(),branch.tenant_id)
        # Regla de negocio: soft delete
        branch.activa = False
        db.commit()
        db.refresh(branch)
        return branch
