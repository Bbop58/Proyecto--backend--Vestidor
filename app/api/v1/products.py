import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, status, Query, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission, get_current_user
from app.schemas.product import ProductCreate, ProductUpdate, ProductResponse
from app.schemas.product_variant import ProductVariantCreate, ProductVariantResponse
from app.services.product_service import ProductService
from app.services.audit_service import AuditService

router = APIRouter(prefix="/productos", tags=["Productos"])


@router.get(
    "",
    response_model=List[ProductResponse],
    dependencies=[Depends(require_permission("productos.list"))],
    summary="Listar productos con filtros"
)
def list_products(
    categoria_id: Optional[uuid.UUID] = Query(None, description="Filtrar por categoría"),
    temporada: Optional[str] = Query(None, description="Filtrar por temporada"),
    proveedor: Optional[str] = Query(None, description="Filtrar por proveedor"),
    search: Optional[str] = Query(None, description="Buscar por nombre o descripción"),
    activo: Optional[bool] = Query(None, description="Filtrar por estado activo/inactivo"),
    db: Session = Depends(get_db)
):
    return ProductService.get_all(
        db,
        categoria_id=categoria_id,
        temporada=temporada,
        proveedor=proveedor,
        search=search,
        activo=activo
    )


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("productos.create"))],
    summary="Crear un nuevo producto"
)
def create_product(
    product_in: ProductCreate,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    prod = ProductService.create(db, product_in)
    AuditService.log(
        db=db,
        action=f"Creación de producto: {prod.nombre}",
        module="Productos",
        user=current_user
    )
    return prod


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_permission("productos.list"))],
    summary="Obtener detalle de un producto"
)
def get_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    product = ProductService.get_by_id(db, product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")
    return product


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_permission("productos.update"))],
    summary="Actualizar un producto"
)
def update_product(
    product_id: uuid.UUID,
    product_in: ProductUpdate,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    prod = ProductService.update(db, product_id, product_in)
    AuditService.log(
        db=db,
        action=f"Modificación de producto: {prod.nombre}",
        module="Productos",
        user=current_user
    )
    return prod


@router.delete(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_permission("productos.delete"))],
    summary="Desactivar un producto (Soft Delete)"
)
def delete_product(
    product_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    prod = ProductService.soft_delete(db, product_id)
    AuditService.log(
        db=db,
        action=f"Desactivación de producto: {prod.nombre}",
        module="Productos",
        user=current_user
    )
    return prod


# --- Variantes asociadas a un producto ---

@router.get(
    "/{product_id}/variantes",
    response_model=List[ProductVariantResponse],
    dependencies=[Depends(require_permission("variantes.list"))],
    summary="Listar variantes de un producto"
)
def list_product_variants(
    product_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    return ProductService.get_variants(db, product_id)


@router.post(
    "/{product_id}/variantes",
    response_model=ProductVariantResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("variantes.create"))],
    summary="Agregar una variante a un producto"
)
def add_product_variant(
    product_id: uuid.UUID,
    variant_in: ProductVariantCreate,
    db: Session = Depends(get_db)
):
    return ProductService.add_variant(db, product_id, variant_in)
