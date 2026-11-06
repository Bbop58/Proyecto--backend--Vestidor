from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_revision: int = Field(ge=0, strict=True)


class CompanyPayPalUpdate(RevisionRequest):
    client_id: str = Field(min_length=3, max_length=255)
    client_secret: SecretStr | None = None
    mode: Literal['sandbox', 'live'] = 'sandbox'
