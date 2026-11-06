import uuid
import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, model_validator


class NotificationBase(BaseModel):
    title: str
    message: str
    type: str = "GENERAL"
    reference_id: Optional[str] = None


class NotificationCreate(NotificationBase):
    user_id: uuid.UUID


class NotificationResponse(NotificationBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    is_read: bool
    created_at: datetime

    @model_validator(mode='after')
    def restore_legacy_reservation_text(self) -> 'NotificationResponse':
        # Repair only known historical templates in the response, never ORM rows
        # or arbitrary user text (branch names may already contain valid Unicode).
        if self.type == 'RESERVATION_EXPIRING':
            if self.title == 'Reserva Registrada â\x8f³':
                self.title = 'Reserva Registrada ⏳'
            self.message = re.sub(
                r'^(Tu reserva #\S+ fue registrada con )Ã©xito( por Bs )',
                r'\1éxito\2', self.message)
        elif self.type == 'RESERVATION_READY':
            if self.title == 'Â¡Tu reserva estÃ¡ lista para recoger! ðŸ“¦':
                self.title = '¡Tu reserva está lista para recoger! 📦'
            self.message = re.sub(
                r'^(Tu reserva #\S+ ya )estÃ¡( preparada en )',
                r'\1está\2', self.message)
        return self


class UnreadCountResponse(BaseModel):
    unread_count: int
