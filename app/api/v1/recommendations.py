import uuid
from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.recommendation import ProductRecommendationResponse
from app.services.recommendation_service import recommendation_service

router = APIRouter(prefix="/recommendations", tags=["Recomendaciones"])


@router.get(
    "/trending",
    response_model=List[ProductRecommendationResponse],
    summary="Prendas en tendencia y más vendidas"
)
def get_trending_recommendations(
    limit: int = Query(8, ge=1, le=50, description="Cantidad máxima de recomendaciones"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return recommendation_service.get_trending_products(
        db=db,
        tenant_id=current_user.tenant_id,
        limit=limit
    )


@router.get(
    "/related/{product_id}",
    response_model=List[ProductRecommendationResponse],
    summary="Recomendaciones complementarias ('Completa tu look')"
)
def get_related_recommendations(
    product_id: uuid.UUID,
    limit: int = Query(4, ge=1, le=20, description="Cantidad de productos relacionados"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return recommendation_service.get_related_products(
        db=db,
        product_id=product_id,
        tenant_id=current_user.tenant_id,
        limit=limit
    )


@router.get(
    "/personalized",
    response_model=List[ProductRecommendationResponse],
    summary="Recomendaciones personalizadas según historial del cliente"
)
def get_personalized_recommendations(
    limit: int = Query(6, ge=1, le=30, description="Cantidad de recomendaciones personalizadas"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return recommendation_service.get_personalized_for_user(
        db=db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        limit=limit
    )
