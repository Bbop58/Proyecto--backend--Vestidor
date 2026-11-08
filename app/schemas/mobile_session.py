from uuid import UUID
from pydantic import BaseModel, Field, SecretStr
from app.schemas.user import UserResponse


class MobileCredentialRequest(BaseModel):
    # Shape validation lives in the service: validation errors must not echo secrets.
    session_token: SecretStr


class MobileRefreshResponse(BaseModel):
    session_id: UUID
    access_token: str = Field(repr=False)
    token_type: str = 'bearer'
    user: UserResponse


class MobileSessionResponse(MobileRefreshResponse):
    session_token: str = Field(repr=False)
