from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class AutomaticBackupUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool
    interval_hours: int = Field(strict=True)
    revision: int = Field(strict=True, ge=0)

    @field_validator('interval_hours')
    @classmethod
    def interval(cls, value):
        if not (1 <= value <= 168 or 192 <= value <= 720 and value % 24 == 0):
            raise ValueError('Elige 1–168 horas o 1–30 días completos')
        return value
