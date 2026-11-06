from decimal import Decimal
from typing import Literal
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from app.core.plan_features import PLAN_FEATURES


class CatalogRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    reason: str=Field(min_length=5,max_length=500)


class CatalogPlanCreate(CatalogRequest):
    codigo: str=Field(min_length=2,max_length=50,pattern=r'^[A-Z][A-Z0-9_]*$')
    nombre: str=Field(min_length=2,max_length=100)


class CatalogPlanResponse(BaseModel):
    id: UUID
    codigo: str
    nombre: str
    activo: bool


class PlanDraftCreate(CatalogRequest):
    precio_mensual_bob: Decimal=Field(ge=0,max_digits=10,decimal_places=2,allow_inf_nan=False)
    max_sucursales: int=Field(ge=1,le=2147483647,strict=True)
    moneda: Literal['BOB']='BOB'
    periodicidad: Literal['MENSUAL']='MENSUAL'
    features: dict[str,StrictBool]
    descripcion: str | None=Field(None,max_length=2000)

    @field_validator('features')
    @classmethod
    def known_features(cls,value):
        if set(value)!=set(PLAN_FEATURES):
            raise ValueError('Las capacidades deben ser exactamente reportes_ia, analitica_avanzada y offline')
        return value


class PlanDraftUpdate(PlanDraftCreate):
    expected_version: int=Field(ge=0,strict=True)


class PlanVersionDecision(CatalogRequest):
    expected_version: int=Field(ge=0,strict=True)


class PlanVersionResponse(BaseModel):
    id: UUID
    plan_id: UUID
    revision: int
    estado: Literal['BORRADOR','PUBLICADA','RETIRADA']
    lock_version: int
    precio_mensual_bob: Decimal
    moneda: str
    periodicidad: str
    max_sucursales: int
    features: dict[str,bool]
    descripcion: str | None
    origen: Literal['PLATAFORMA','REFERENCIA_LEGADA']
    es_gratuito: bool
    published_at: datetime | None
    created_at: datetime
