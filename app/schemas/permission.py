import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class PermissionBase(BaseModel):
    code: str
    module: str
    method: str
    path: str
    description: Optional[str] = None


class PermissionResponse(PermissionBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class GroupedPermissionsResponse(BaseModel):
    module: str
    permissions: list[PermissionResponse]
