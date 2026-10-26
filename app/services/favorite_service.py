import uuid
from typing import List
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.favorite import Favorite
from app.models.product import Product
from app.schemas.favorite import FavoriteCreate, FavoriteResponse
from app.services.audit_service import audit_service


class FavoriteService:
    @staticmethod
    def list_favorites(db: Session, user_id: uuid.UUID, tenant_id: uuid.UUID) -> List[FavoriteResponse]:
        favorites = (
            db.query(Favorite)
            .filter(Favorite.user_id == user_id, Favorite.tenant_id == tenant_id)
            .order_by(Favorite.created_at.desc())
            .all()
        )
        result = []
        for f in favorites:
            p = f.producto
            cat_name = p.categoria.nombre if p and p.categoria else "General"
            
            result.append(
                FavoriteResponse(
                    id=f.id,
                    user_id=f.user_id,
                    product_id=f.product_id,
                    tenant_id=f.tenant_id,
                    created_at=f.created_at,
                    product_nombre=p.nombre if p else "Producto",
                    precio_base=float(p.precio_base) if p and p.precio_base else 0.0,
                    categoria_nombre=cat_name,
                    imagen_url=p.imagen_url if p else None
                )
            )
        return result

    @staticmethod
    def add_favorite(db: Session, user_id: uuid.UUID, tenant_id: uuid.UUID, data: FavoriteCreate, request=None) -> FavoriteResponse:
        product = (
            db.query(Product)
            .filter(Product.id == data.product_id, Product.tenant_id == tenant_id, Product.activo == True)
            .first()
        )
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Producto no encontrado o inactivo para esta tienda"
            )

        existing = (
            db.query(Favorite)
            .filter(Favorite.user_id == user_id, Favorite.product_id == data.product_id, Favorite.tenant_id == tenant_id)
            .first()
        )
        if existing:
            return FavoriteResponse(
                id=existing.id,
                user_id=existing.user_id,
                product_id=existing.product_id,
                tenant_id=existing.tenant_id,
                created_at=existing.created_at,
                product_nombre=product.nombre,
                precio_base=float(product.precio_base) if product.precio_base else 0.0,
                categoria_nombre=product.categoria.nombre if product.categoria else "General",
                imagen_url=product.imagen_url
            )

        fav = Favorite(
            user_id=user_id,
            product_id=data.product_id,
            tenant_id=tenant_id
        )
        db.add(fav)
        db.commit()
        db.refresh(fav)

        audit_service.log(
            db=db,
            user_id=user_id,
            action="AGREGAR_FAVORITO",
            module="Favoritos",
            details=f"Usuario guardó en favoritos el producto {product.nombre} (ID: {product.id})",
            tenant_id=tenant_id,
            request=request
        )

        return FavoriteResponse(
            id=fav.id,
            user_id=fav.user_id,
            product_id=fav.product_id,
            tenant_id=fav.tenant_id,
            created_at=fav.created_at,
            product_nombre=product.nombre,
            precio_base=float(product.precio_base) if product.precio_base else 0.0,
            categoria_nombre=product.categoria.nombre if product.categoria else "General",
            imagen_url=product.imagen_url
        )

    @staticmethod
    def remove_favorite(db: Session, user_id: uuid.UUID, tenant_id: uuid.UUID, product_id: uuid.UUID, request=None) -> dict:
        fav = (
            db.query(Favorite)
            .filter(Favorite.user_id == user_id, Favorite.product_id == product_id, Favorite.tenant_id == tenant_id)
            .first()
        )
        if not fav:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El producto no está en tu lista de favoritos"
            )

        db.delete(fav)
        db.commit()

        audit_service.log(
            db=db,
            user_id=user_id,
            action="ELIMINAR_FAVORITO",
            module="Favoritos",
            details=f"Usuario eliminó de favoritos el producto ID: {product_id}",
            tenant_id=tenant_id,
            request=request
        )

        return {"message": "Producto eliminado de favoritos exitosamente"}

    @staticmethod
    def check_is_favorite(db: Session, user_id: uuid.UUID, tenant_id: uuid.UUID, product_id: uuid.UUID) -> bool:
        fav = (
            db.query(Favorite)
            .filter(Favorite.user_id == user_id, Favorite.product_id == product_id, Favorite.tenant_id == tenant_id)
            .first()
        )
        return fav is not None


favorite_service = FavoriteService()
