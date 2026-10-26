import uuid
from typing import List
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.favorite import FavoriteCreate, FavoriteResponse, FavoriteCheckResponse
from app.schemas.common import MessageResponse
from app.services.favorite_service import favorite_service

router = APIRouter(prefix="/favorites", tags=["Favoritos"])


@router.get("", response_model=List[FavoriteResponse], summary="Listar prendas favoritas del usuario")
def list_favorites(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return favorite_service.list_favorites(db, current_user.id, current_user.tenant_id)


@router.post("", response_model=FavoriteResponse, status_code=status.HTTP_201_CREATED, summary="Agregar prenda a favoritos")
def add_favorite(
    payload: FavoriteCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return favorite_service.add_favorite(db, current_user.id, current_user.tenant_id, payload, request)


@router.delete("/{product_id}", response_model=MessageResponse, summary="Eliminar prenda de favoritos")
def remove_favorite(
    product_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return favorite_service.remove_favorite(db, current_user.id, current_user.tenant_id, product_id, request)


@router.get("/check/{product_id}", response_model=FavoriteCheckResponse, summary="Consultar si un producto está en favoritos")
def check_favorite(
    product_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    is_fav = favorite_service.check_is_favorite(db, current_user.id, current_user.tenant_id, product_id)
    return FavoriteCheckResponse(is_favorite=is_fav, product_id=product_id)
