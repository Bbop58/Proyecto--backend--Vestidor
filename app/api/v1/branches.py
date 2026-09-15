import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission
from app.schemas.branch import BranchCreate, BranchUpdate, BranchResponse
from app.services.branch_service import BranchService

router = APIRouter(prefix="/sucursales", tags=["Sucursales"])


@router.get(
    "",
    response_model=List[BranchResponse],
    dependencies=[Depends(require_permission("sucursales.list"))],
    summary="Listar todas las sucursales"
)
def list_branches(
    activa_only: bool = Query(False, description="Filtrar solo sucursales activas"),
    db: Session = Depends(get_db)
):
    return BranchService.get_all(db, activa_only=activa_only)


@router.post(
    "",
    response_model=BranchResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("sucursales.create"))],
    summary="Crear una nueva sucursal"
)
def create_branch(
    branch_in: BranchCreate,
    db: Session = Depends(get_db)
):
    return BranchService.create(db, branch_in)


@router.get(
    "/{branch_id}",
    response_model=BranchResponse,
    dependencies=[Depends(require_permission("sucursales.list"))],
    summary="Obtener detalle de una sucursal"
)
def get_branch(
    branch_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    branch = BranchService.get_by_id(db, branch_id)
    if not branch:
        from fastapi import HTTPException
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sucursal no encontrada")
    return branch


@router.put(
    "/{branch_id}",
    response_model=BranchResponse,
    dependencies=[Depends(require_permission("sucursales.update"))],
    summary="Actualizar una sucursal"
)
def update_branch(
    branch_id: uuid.UUID,
    branch_in: BranchUpdate,
    db: Session = Depends(get_db)
):
    return BranchService.update(db, branch_id, branch_in)


@router.delete(
    "/{branch_id}",
    response_model=BranchResponse,
    dependencies=[Depends(require_permission("sucursales.delete"))],
    summary="Desactivar una sucursal (Soft Delete)"
)
def delete_branch(
    branch_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    return BranchService.soft_delete(db, branch_id)
