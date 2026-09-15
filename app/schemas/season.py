import uuid
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator


class SeasonBase(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=50, description="Nombre de la temporada")
    año: int = Field(..., ge=2000, le=2100, description="Año (YYYY)")
    fecha_inicio: date = Field(..., description="Fecha de inicio")
    fecha_fin: date = Field(..., description="Fecha de finalización")
    activa: bool = Field(False, description="Temporada activa para el año")

    @model_validator(mode="after")
    def validate_dates(self):
        if self.fecha_fin <= self.fecha_inicio:
            raise ValueError("La fecha_fin debe ser posterior a la fecha_inicio")
        return self


class SeasonCreate(SeasonBase):
    pass


class SeasonUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=2, max_length=50)
    año: Optional[int] = Field(None, ge=2000, le=2100)
    fecha_inicio: Optional[date] = None
    fecha_fin: Optional[date] = None
    activa: Optional[bool] = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin <= self.fecha_inicio:
            raise ValueError("La fecha_fin debe ser posterior a la fecha_inicio")
        return self


class SeasonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre: str
    año: int
    fecha_inicio: date
    fecha_fin: date
    activa: bool
    created_at: datetime
    updated_at: datetime
