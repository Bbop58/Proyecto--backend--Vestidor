import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.permission import PermissionResponse


class RoleBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    description: Optional[str] = Field(None, max_length=255)


class RoleCreate(RoleBase):
    permission_ids: Optional[List[uuid.UUID]] = []


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=50)
    description: Optional[str] = Field(None, max_length=255)


class RoleResponse(RoleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class RoleWithPermissions(RoleResponse):
    permissions: List[PermissionResponse] = []


class RolePermissionAssign(BaseModel):
    permission_ids: List[uuid.UUID]
