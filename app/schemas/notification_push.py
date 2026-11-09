import uuid
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class DeviceIdentity(BaseModel):
    model_config=ConfigDict(extra='forbid')
    installation_id:uuid.UUID
    installation_secret:str=Field(min_length=32,max_length=128)

class DeviceRegistration(DeviceIdentity):
    token:str=Field(min_length=16,max_length=4096)
    platform:Literal['android','web']
    refresh_token:str|None=Field(None,max_length=8192)

class DeviceRevocation(DeviceIdentity):
    binding_id:uuid.UUID
