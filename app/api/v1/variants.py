import uuid
from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission
from app.schemas.product_variant import ProductVariantUpdate, ProductVariantResponse
from app.services.product_service import ProductService

router = APIRouter(prefix="/variantes", tags=["Variantes de Producto"])


@router.get(
    "/{variant_id}",
    response_model=ProductVariantResponse,
    dependencies=[Depends(require_permission("variantes.list"))],
    summary="Obtener detalle de una variante"
)
def get_variant(
    variant_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    variant = ProductService.get_variant_by_id(db, variant_id)
    if not variant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variante no encontrada")
    return variant


@router.put(
    "/{variant_id}",
    response_model=ProductVariantResponse,
    dependencies=[Depends(require_permission("variantes.update"))],
    summary="Actualizar una variante de producto"
)
def update_variant(
    variant_id: uuid.UUID,
    variant_in: ProductVariantUpdate,
    db: Session = Depends(get_db)
):
    return ProductService.update_variant(db, variant_id, variant_in)


@router.delete(
    "/{variant_id}",
    response_model=ProductVariantResponse,
    dependencies=[Depends(require_permission("variantes.delete"))],
    summary="Desactivar una variante de producto (Soft Delete)"
)
def delete_variant(
    variant_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    return ProductService.soft_delete_variant(db, variant_id)
