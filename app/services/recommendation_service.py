import uuid
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from app.models.product import Product
from app.models.category import Category
from app.models.product_variant import ProductVariant
from app.models.inventory import Inventory
from app.models.sale_detail import SaleDetail
from app.models.sale import Sale, SaleStatus
from app.models.favorite import Favorite
from app.schemas.recommendation import ProductRecommendationResponse


class RecommendationService:
    @staticmethod
    def _get_product_stock(db: Session, product_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
        stock = (
            db.query(func.coalesce(func.sum(Inventory.stock_actual), 0))
            .join(ProductVariant, Inventory.variante_id == ProductVariant.id)
            .filter(
                ProductVariant.producto_id == product_id,
                Inventory.tenant_id == tenant_id,
                Inventory.activo == True
            )
            .scalar()
        )
        return int(stock or 0)

    @staticmethod
    def _to_recommendation_item(
        p: Product,
        motivo: str,
        stock: int
    ) -> ProductRecommendationResponse:
        return ProductRecommendationResponse(
            id=p.id,
            nombre=p.nombre,
            descripcion=p.descripcion,
            precio_base=float(p.precio_base) if p.precio_base else 0.0,
            categoria_nombre=p.categoria.nombre if p.categoria else "General",
            imagen_url=p.imagen_url,
            motivo_recomendacion=motivo,
            stock_total_disponible=stock
        )

    @staticmethod
    def get_trending_products(
        db: Session,
        tenant_id: uuid.UUID,
        limit: int = 8
    ) -> List[ProductRecommendationResponse]:
        """
        Calcula las prendas más vendidas del tenant que están activas.
        Si hay pocas ventas, complementa con los productos más recientes.
        """
        # Top sold products via SaleDetail
        top_sold = (
            db.query(ProductVariant.producto_id, func.sum(SaleDetail.cantidad).label("total_sold"))
            .join(SaleDetail, ProductVariant.id == SaleDetail.variante_id)
            .join(Sale, SaleDetail.venta_id == Sale.id)
            .filter(
                Sale.tenant_id == tenant_id,
                Sale.estado == SaleStatus.COMPLETADA
            )
            .group_by(ProductVariant.producto_id)
            .order_by(desc("total_sold"))
            .limit(limit * 2)
            .all()
        )

        seen_ids = set()
        results: List[ProductRecommendationResponse] = []

        for item in top_sold:
            p_id = item.producto_id
            if p_id in seen_ids:
                continue
            product = db.query(Product).filter(
                Product.id == p_id,
                Product.tenant_id == tenant_id,
                Product.activo == True
            ).first()
            if product:
                seen_ids.add(product.id)
                stock = RecommendationService._get_product_stock(db, product.id, tenant_id)
                results.append(
                    RecommendationService._to_recommendation_item(
                        product,
                        "Más vendido de la tienda",
                        stock
                    )
                )
                if len(results) >= limit:
                    break

        # Fallback / Backfill if not enough sales
        if len(results) < limit:
            fallback_products = (
                db.query(Product)
                .filter(
                    Product.tenant_id == tenant_id,
                    Product.activo == True
                )
                .order_by(Product.created_at.desc())
                .limit(limit * 2)
                .all()
            )
            for p in fallback_products:
                if p.id not in seen_ids:
                    seen_ids.add(p.id)
                    stock = RecommendationService._get_product_stock(db, p.id, tenant_id)
                    results.append(
                        RecommendationService._to_recommendation_item(
                            p,
                            "Prenda destacada de la temporada",
                            stock
                        )
                    )
                    if len(results) >= limit:
                        break

        return results

    @staticmethod
    def get_related_products(
        db: Session,
        product_id: uuid.UUID,
        tenant_id: uuid.UUID,
        limit: int = 4
    ) -> List[ProductRecommendationResponse]:
        """
        Sugiere prendas complementarias de la misma categoría o temporada
        excluyendo el producto consultado.
        """
        product = db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == tenant_id,
            Product.activo == True
        ).first()

        if not product:
            return RecommendationService.get_trending_products(db, tenant_id, limit=limit)

        query = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.activo == True,
            Product.id != product_id
        )

        if product.categoria_id:
            query = query.filter(Product.categoria_id == product.categoria_id)

        related = query.order_by(Product.created_at.desc()).limit(limit).all()

        # If not enough in category, query same temporada
        if len(related) < limit and product.temporada:
            related_ids = {p.id for p in related}
            additional = (
                db.query(Product)
                .filter(
                    Product.tenant_id == tenant_id,
                    Product.activo == True,
                    Product.id != product_id,
                    Product.temporada == product.temporada,
                    Product.id.notin_(related_ids)
                )
                .limit(limit - len(related))
                .all()
            )
            related.extend(additional)

        results = []
        for p in related:
            stock = RecommendationService._get_product_stock(db, p.id, tenant_id)
            results.append(
                RecommendationService._to_recommendation_item(
                    p,
                    "Completa tu look",
                    stock
                )
            )

        return results

    @staticmethod
    def get_personalized_for_user(
        db: Session,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        limit: int = 6
    ) -> List[ProductRecommendationResponse]:
        """
        Analiza categorías preferidas por el cliente (según compras pasadas y favoritos)
        y sugiere prendas disponibles. Si no tiene historial suficiente (cold-start),
        hace fallback a trending.
        """
        # 1. Categories from user favorites
        fav_categories = (
            db.query(Product.categoria_id)
            .join(Favorite, Favorite.product_id == Product.id)
            .filter(
                Favorite.user_id == user_id,
                Favorite.tenant_id == tenant_id
            )
            .all()
        )

        # 2. Categories from user past purchases
        purchase_categories = (
            db.query(Product.categoria_id)
            .join(ProductVariant, ProductVariant.producto_id == Product.id)
            .join(SaleDetail, SaleDetail.variante_id == ProductVariant.id)
            .join(Sale, SaleDetail.venta_id == Sale.id)
            .filter(
                Sale.cliente_id == user_id,
                Sale.tenant_id == tenant_id,
                Sale.estado == SaleStatus.COMPLETADA
            )
            .all()
        )

        category_ids = {c[0] for c in fav_categories if c[0]} | {c[0] for c in purchase_categories if c[0]}

        if not category_ids:
            return RecommendationService.get_trending_products(db, tenant_id, limit=limit)

        # Products in those favorite categories
        personalized = (
            db.query(Product)
            .filter(
                Product.tenant_id == tenant_id,
                Product.activo == True,
                Product.categoria_id.in_(category_ids)
            )
            .order_by(Product.created_at.desc())
            .limit(limit)
            .all()
        )

        results = []
        seen_ids = set()
        for p in personalized:
            seen_ids.add(p.id)
            stock = RecommendationService._get_product_stock(db, p.id, tenant_id)
            results.append(
                RecommendationService._to_recommendation_item(
                    p,
                    "Recomendado según tus preferencias",
                    stock
                )
            )

        # Backfill if not enough
        if len(results) < limit:
            trending = RecommendationService.get_trending_products(db, tenant_id, limit=limit)
            for t in trending:
                if t.id not in seen_ids:
                    results.append(t)
                    if len(results) >= limit:
                        break

        return results


recommendation_service = RecommendationService()
