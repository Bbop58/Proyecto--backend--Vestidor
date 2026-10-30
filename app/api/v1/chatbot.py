from typing import Optional
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user_optional
from app.models.user import User
from app.schemas.chatbot import ChatbotRequest, ChatbotResponse
from app.services.chatbot_service import ChatbotService

router = APIRouter(prefix="/chatbot", tags=["Chatbot Inteligente con IA"])


@router.post(
    "/message",
    response_model=ChatbotResponse,
    status_code=status.HTTP_200_OK,
    summary="Enviar mensaje al Asistente Virtual Inteligente (Gemini 1.5 Flash)"
)
def send_chatbot_message(
    payload: ChatbotRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Procesa un mensaje conversacional con el Asistente Virtual Inteligente.
    Admite tanto clientes anónimos (en catálogo/web/móvil) como usuarios autenticados.
    Se adapta a la marca del tenant (UrbanStyle o Moda Express) y al rol del usuario.
    """
    return ChatbotService.process_message(
        db=db,
        message=payload.message,
        history=payload.history,
        tenant_slug=payload.tenant_slug,
        current_user=current_user
    )

