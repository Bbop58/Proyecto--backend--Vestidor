from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.voice_search import (
    VoiceCatalogSearchRequest,
    VoiceCatalogSearchResponse
)
from app.services.voice_catalog_search_service import voice_catalog_search_service

router = APIRouter(prefix="/catalog", tags=["Catálogo e IA"])


@router.post(
    "/voice-search",
    response_model=VoiceCatalogSearchResponse,
    summary="Búsqueda semántica de prendas por voz en el catálogo"
)
def search_catalog_by_voice(
    payload: VoiceCatalogSearchRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Interpreta comandos de voz transcritos para buscar prendas filtrando
    por categoría, color, talla, precio o estilo de manera inteligente.
    """
    return voice_catalog_search_service.search_catalog_by_voice(
        db=db,
        tenant_id=current_user.tenant_id,
        payload=payload,
        user_id=current_user.id,
        request=request
    )
