import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    user_role: Optional[str] = None
    action: str
    module: str
