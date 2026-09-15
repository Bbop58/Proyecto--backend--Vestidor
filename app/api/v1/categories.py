import uuid
from typing import List
from fastapi import APIRouter, Depends, status, Query, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryResponse
from app.services.category_service import CategoryService

router = APIRouter(prefix="/categorias", tags=["Categorías"])


@router.get(
    "",
    response_model=List[CategoryResponse],
    dependencies=[Depends(require_permission("categorias.list"))],
    summary="Listar categorías"
)
def list_categories(
    activa_only: bool = Query(False, description="Filtrar solo categorías activas"),
    db: Session = Depends(get_db)
):
    return CategoryService.get_all(db, activa_only=activa_only)


@router.post(
    "",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("categorias.create"))],
    summary="Crear una nueva categoría"
)
def create_category(
    category_in: CategoryCreate,
    db: Session = Depends(get_db)
):
    return CategoryService.create(db, category_in)


@router.get(
    "/{category_id}",
    response_model=CategoryResponse,
    dependencies=[Depends(require_permission("categorias.list"))],
    summary="Obtener detalle de una categoría"
)
def get_category(
    category_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    category = CategoryService.get_by_id(db, category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoría no encontrada")
    return category


@router.put(
    "/{category_id}",
    response_model=CategoryResponse,
    dependencies=[Depends(require_permission("categorias.update"))],
    summary="Actualizar una categoría"
)
def update_category(
    category_id: uuid.UUID,
    category_in: CategoryUpdate,
    db: Session = Depends(get_db)
):
    return CategoryService.update(db, category_id, category_in)


@router.delete(
    "/{category_id}",
    response_model=CategoryResponse,
    dependencies=[Depends(require_permission("categorias.delete"))],
    summary="Desactivar una categoría (Soft Delete)"
)
def delete_category(
    category_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    return CategoryService.soft_delete(db, category_id)
