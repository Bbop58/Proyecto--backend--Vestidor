import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, status, Query, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import require_permission, get_current_user
from app.models.user import User
from app.schemas.season import SeasonCreate, SeasonUpdate, SeasonResponse
from app.services.season_service import SeasonService

router = APIRouter(prefix="/temporadas", tags=["Temporadas"])


@router.get(
    "",
    response_model=List[SeasonResponse],
    dependencies=[Depends(require_permission("temporadas.list"))],
    summary="Listar temporadas"
)
def list_seasons(
    año: Optional[int] = Query(None, description="Filtrar por año"),
    activa_only: bool = Query(False, description="Filtrar solo temporadas activas"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return SeasonService.get_all(db, año=año, activa_only=activa_only, tenant_id=current_user.tenant_id)


@router.post(
    "",
    response_model=SeasonResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("temporadas.create"))],
    summary="Crear una nueva temporada"
)
def create_season(
    season_in: SeasonCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return SeasonService.create(db, season_in, tenant_id=current_user.tenant_id)


@router.get(
    "/{season_id}",
    response_model=SeasonResponse,
    dependencies=[Depends(require_permission("temporadas.list"))],
    summary="Obtener detalle de una temporada"
)
def get_season(
    season_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    season = SeasonService.get_by_id(db, season_id)
    if not season:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Temporada no encontrada")
    return season


@router.put(
    "/{season_id}",
    response_model=SeasonResponse,
    dependencies=[Depends(require_permission("temporadas.update"))],
    summary="Actualizar una temporada"
)
def update_season(
    season_id: uuid.UUID,
    season_in: SeasonUpdate,
    db: Session = Depends(get_db)
):
    return SeasonService.update(db, season_id, season_in)


@router.delete(
    "/{season_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("temporadas.delete"))],
    summary="Eliminar una temporada"
)
def delete_season(
    season_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    SeasonService.delete(db, season_id)
    return None
