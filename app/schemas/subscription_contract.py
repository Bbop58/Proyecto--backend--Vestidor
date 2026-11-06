from uuid import UUID
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,StrictBool,field_validator


class ContractBody(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class ContractQuoteRequest(ContractBody):
    version_id: UUID
    kind: Literal['ALTA','RENOVACION','CAMBIO']


class ContractChangeRequest(ContractBody):
    quote_token: str=Field(min_length=20,max_length=12000)
    acceptance: StrictBool
    idempotency_key: UUID

    @field_validator('acceptance')
    @classmethod
    def must_accept(cls,value):
        if value is not True:raise ValueError('Debes aceptar explícitamente las condiciones')
        return value


class ContractDecision(ContractBody):
    decision: Literal['APROBADA','RECHAZADA']
    expected_version: int=Field(ge=0,strict=True)
    idempotency_key: UUID
    reason: str=Field(min_length=5,max_length=500)


class ContractCancellation(ContractBody):
    expected_version: int=Field(ge=0,strict=True)
    reason: str=Field(min_length=5,max_length=500)
