from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import get_current_user_optional
from app.schemas.ai import VirtualTryOnRequest, VirtualTryOnResponse
from app.services.ai_tryon_service import AITryOnService

router = APIRouter(prefix="/ai", tags=["Inteligencia Artificial"])


@router.post(
    "/virtual-tryon",
    response_model=VirtualTryOnResponse,
    status_code=status.HTTP_200_OK,
    summary="Vestidor Virtual con Gemini"
)
def virtual_try_on(
    data: VirtualTryOnRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_optional)
):
    """
    Recibe la foto del usuario y el ID del producto que está viendo para generar una imagen
    realista de esa persona vistiendo esa prenda mediante Google Gemini, manteniendo
    su rostro, cuerpo, pose y fondo original intactos.
    """
    cliente_id = current_user.id if current_user else None
    return AITryOnService.process_virtual_tryon(
        db=db,
        request=data,
        cliente_id=cliente_id
    )
