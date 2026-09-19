from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.api.deps import get_current_user_optional
from app.schemas.ai import VirtualTryOnRequest, VirtualTryOnResponse
from app.services.ai_tryon_service import AITryOnService

router = APIRouter(prefix="/ai", tags=["Inteligencia Artificial"])


@router.post(
    "/virtual-tryon",
    response_model=VirtualTryOnResponse,
    status_code=status.HTTP_200_OK,
    summary="Vestidor Virtual con IA (Prueba de calce, talla y estilo)"
)
async def virtual_try_on(
    data: VirtualTryOnRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_optional)
):
    """
    Recibe la fotografía del usuario y el ID de variante de prenda para generar:
    1. Análisis de silueta y contextura corporal.
    2. Recomendación de talla ideal (S, M, L, XL, XXL).
    3. Calce y porcentaje de compatibilidad.
    4. Consejos de combinación y prendas sugeridas del catálogo.
    """
    cliente_id = current_user.id if current_user else None
    return await AITryOnService.process_virtual_tryon(
        db=db,
        request=data,
        cliente_id=cliente_id
    )
