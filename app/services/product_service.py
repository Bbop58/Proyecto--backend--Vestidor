import uuid
import re
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_
from fastapi import HTTPException, status
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.category import Category
from app.schemas.product import ProductCreate, ProductUpdate
from app.schemas.product_variant import ProductVariantCreate, ProductVariantUpdate


def clean_slug(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    return re.sub(r"[-\s]+", "-", cleaned).upper()


class ProductService:
    @staticmethod
    def get_all(
        db: Session,
        categoria_id: Optional[uuid.UUID] = None,
        temporada: Optional[str] = None,
        proveedor: Optional[str] = None,
        search: Optional[str] = None,
        activo: Optional[bool] = None,
    ) -> List[Product]:
        query = db.query(Product)

        if categoria_id:
            query = query.filter(Product.categoria_id == categoria_id)
        if temporada:
            query = query.filter(Product.temporada.ilike(f"%{temporada.strip()}%"))
        if proveedor:
            query = query.filter(Product.proveedor.ilike(f"%{proveedor.strip()}%"))
        if search:
            search_term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    Product.nombre.ilike(search_term),
                    Product.descripcion.ilike(search_term),
                    Product.temporada.ilike(search_term),
                    Product.proveedor.ilike(search_term),
                    Product.variantes.any(ProductVariant.sku.ilike(search_term))
                )
            )
        if activo is not None:
            query = query.filter(Product.activo == activo)

        return query.order_by(Product.created_at.desc()).all()

    @staticmethod
    def get_by_id(db: Session, product_id: uuid.UUID) -> Optional[Product]:
        return db.query(Product).filter(Product.id == product_id).first()

    @staticmethod
    def create(db: Session, product_in: ProductCreate) -> Product:
        category = db.query(Category).filter(Category.id == product_in.categoria_id).first()
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="La categoría especificada no existe"
            )

        product = Product(
            nombre=product_in.nombre.strip(),
            descripcion=product_in.descripcion.strip() if product_in.descripcion else None,
            precio_base=product_in.precio_base,
            categoria_id=product_in.categoria_id,
            temporada=product_in.temporada.strip() if product_in.temporada else None,
            proveedor=product_in.proveedor.strip() if product_in.proveedor else None,
            imagen_url=product_in.imagen_url.strip() if product_in.imagen_url else None,
            activo=product_in.activo
        )
        db.add(product)
        db.flush()

        # Handle initial variants
        if product_in.variantes:
            for var_in in product_in.variantes:
                ProductService.add_variant(db, product.id, var_in, auto_commit=False)

        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def update(db: Session, product_id: uuid.UUID, product_in: ProductUpdate) -> Product:
        product = ProductService.get_by_id(db, product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Producto no encontrado"
            )

        if product_in.categoria_id is not None:
            category = db.query(Category).filter(Category.id == product_in.categoria_id).first()
            if not category:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="La categoría especificada no existe"
                )
            product.categoria_id = product_in.categoria_id

        if product_in.nombre is not None:
            product.nombre = product_in.nombre.strip()
        if product_in.descripcion is not None:
            product.descripcion = product_in.descripcion.strip() if product_in.descripcion else None
        if product_in.precio_base is not None:
            product.precio_base = product_in.precio_base
        if product_in.temporada is not None:
            product.temporada = product_in.temporada.strip() if product_in.temporada else None
        if product_in.proveedor is not None:
            product.proveedor = product_in.proveedor.strip() if product_in.proveedor else None
        if product_in.imagen_url is not None:
            product.imagen_url = product_in.imagen_url.strip() if product_in.imagen_url else None
        if product_in.activo is not None:
            product.activo = product_in.activo

        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def soft_delete(db: Session, product_id: uuid.UUID) -> Product:
        product = ProductService.get_by_id(db, product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Producto no encontrado"
            )

        product.activo = False
        db.commit()
        db.refresh(product)
        return product

    # ----------------- Variantes -----------------

    @staticmethod
    def get_variants(db: Session, product_id: uuid.UUID) -> List[ProductVariant]:
        return db.query(ProductVariant).filter(ProductVariant.producto_id == product_id).all()

    @staticmethod
    def get_variant_by_id(db: Session, variant_id: uuid.UUID) -> Optional[ProductVariant]:
        return db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()

    @staticmethod
    def add_variant(
        db: Session,
        product_id: uuid.UUID,
        variant_in: ProductVariantCreate,
        auto_commit: bool = True
    ) -> ProductVariant:
        product = ProductService.get_by_id(db, product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Producto no encontrado"
            )

        talla = variant_in.talla.strip().upper()
        color = variant_in.color.strip().capitalize()

        # Check duplicate talla + color on this product
        existing = db.query(ProductVariant).filter(
            ProductVariant.producto_id == product_id,
            ProductVariant.talla == talla,
            ProductVariant.color.ilike(color)
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ya existe una variante con talla '{talla}' y color '{color}' para este producto"
            )

        sku = variant_in.sku.strip().upper() if variant_in.sku else f"{clean_slug(product.nombre)}-{talla}-{clean_slug(color)}"
        
        # Check global SKU uniqueness
        sku_exists = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()
        if sku_exists:
            # Fallback append short unique suffix if autogenerated
            if not variant_in.sku:
                sku = f"{sku}-{uuid.uuid4().hex[:4].upper()}"
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"El código SKU '{sku}' ya está en uso"
                )

        variant = ProductVariant(
            producto_id=product_id,
            talla=talla,
            color=color,
            sku=sku,
            precio_extra=variant_in.precio_extra,
            activo=variant_in.activo
        )
        db.add(variant)

        if auto_commit:
            db.commit()
            db.refresh(variant)

        return variant

    @staticmethod
    def update_variant(
        db: Session,
        variant_id: uuid.UUID,
        variant_in: ProductVariantUpdate
    ) -> ProductVariant:
        variant = ProductService.get_variant_by_id(db, variant_id)
        if not variant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Variante no encontrada"
            )

        if variant_in.talla is not None:
            variant.talla = variant_in.talla.strip().upper()
        if variant_in.color is not None:
            variant.color = variant_in.color.strip().capitalize()
        if variant_in.precio_extra is not None:
            variant.precio_extra = variant_in.precio_extra
        if variant_in.activo is not None:
            variant.activo = variant_in.activo

        if variant_in.sku is not None:
            new_sku = variant_in.sku.strip().upper()
            if new_sku != variant.sku:
                sku_exists = db.query(ProductVariant).filter(
                    ProductVariant.sku == new_sku,
                    ProductVariant.id != variant_id
                ).first()
                if sku_exists:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"El código SKU '{new_sku}' ya está en uso"
                    )
                variant.sku = new_sku

        # Check duplicate combination after change
        duplicate = db.query(ProductVariant).filter(
            ProductVariant.producto_id == variant.producto_id,
            ProductVariant.talla == variant.talla,
            ProductVariant.color.ilike(variant.color),
            ProductVariant.id != variant_id
        ).first()
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ya existe una variante con talla '{variant.talla}' y color '{variant.color}' para este producto"
            )

        db.commit()
        db.refresh(variant)
        return variant

    @staticmethod
    def soft_delete_variant(db: Session, variant_id: uuid.UUID) -> ProductVariant:
        variant = ProductService.get_variant_by_id(db, variant_id)
        if not variant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Variante no encontrada"
            )

        variant.activo = False
        db.commit()
        db.refresh(variant)
        return variant
