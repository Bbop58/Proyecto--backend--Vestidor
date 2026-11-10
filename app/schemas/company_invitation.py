import uuid
from pydantic import BaseModel,Field,EmailStr,SecretStr,ConfigDict
from app.schemas.platform import PlatformCompanyCreate

class CompanyInviteCreate(PlatformCompanyCreate):
    owner_email:EmailStr=Field(max_length=150)
    owner_name:str=Field(min_length=2,max_length=255)

class InvitationResend(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation_id:uuid.UUID

class InvitationToken(BaseModel):
    model_config=ConfigDict(extra='forbid')
    token:SecretStr

class InvitationAccept(InvitationToken):
    password:SecretStr=Field(min_length=8,max_length=72)
