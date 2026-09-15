import uuid
from typing import List
from fastapi import APIRouter, Depends, status, Query, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission
from app.schemas.supplier import SupplierCreate, SupplierUpdate, SupplierResponse
from app.services.supplier_service import SupplierService

router = APIRouter(prefix="/proveedores", tags=["Proveedores"])


@router.get(
    "",
    response_model=List[SupplierResponse],
    dependencies=[Depends(require_permission("proveedores.list"))],
    summary="Listar proveedores"
)
def list_suppliers(
    activo_only: bool = Query(False, description="Filtrar solo proveedores activos"),
    db: Session = Depends(get_db)
):
    return SupplierService.get_all(db, activo_only=activo_only)


@router.post(
    "",
    response_model=SupplierResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("proveedores.create"))],
    summary="Crear un nuevo proveedor"
)
def create_supplier(
    supplier_in: SupplierCreate,
    db: Session = Depends(get_db)
):
    return SupplierService.create(db, supplier_in)


@router.get(
    "/{supplier_id}",
    response_model=SupplierResponse,
    dependencies=[Depends(require_permission("proveedores.list"))],
    summary="Obtener detalle de un proveedor"
)
def get_supplier(
    supplier_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    supplier = SupplierService.get_by_id(db, supplier_id)
    if not supplier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proveedor no encontrado")
    return supplier


@router.put(
    "/{supplier_id}",
    response_model=SupplierResponse,
    dependencies=[Depends(require_permission("proveedores.update"))],
    summary="Actualizar un proveedor"
)
def update_supplier(
    supplier_id: uuid.UUID,
    supplier_in: SupplierUpdate,
    db: Session = Depends(get_db)
):
    return SupplierService.update(db, supplier_id, supplier_in)


@router.delete(
    "/{supplier_id}",
    response_model=SupplierResponse,
    dependencies=[Depends(require_permission("proveedores.delete"))],
    summary="Desactivar un proveedor (Soft Delete)"
)
def delete_supplier(
    supplier_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    return SupplierService.soft_delete(db, supplier_id)
