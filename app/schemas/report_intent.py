"""Strict, provider-independent intent contract for natural-language reports."""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


ReportDomain = Literal["sales", "inventory", "reservations", "exchanges", "none"]
ReportDimension = Literal[
    "branch", "product", "variant", "category", "payment_method", "sale_type",
    "sale_status", "reservation_status", "customer", "day", "week", "month",
]
ReportMetric = Literal[
    "units_sold", "sales_count", "gross_revenue", "net_revenue", "item_revenue",
    "discount_total", "stock_available", "stock_physical", "stock_reserved", "units_reserved",
    "stock_minimum", "reservation_count", "reservation_value", "exchange_count",
    "exchange_difference", "none",
]


class ReportPeriodIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["all_time", "last_days", "last_weeks", "last_months", "this_month", "last_month", "this_year"] = Field(
        default="all_time", description="Periodo resuelto en la zona horaria de Bolivia"
    )
    amount: Optional[int] = Field(default=None, ge=1, le=366)

    @model_validator(mode="after")
    def validate_period_bound(self):
        if self.kind == "last_months" and self.amount is not None and self.amount > 36:
            raise ValueError("El periodo mensual máximo es de 36 meses")
        if self.kind == "last_days" and self.amount is not None and self.amount > 366:
            raise ValueError("El periodo diario máximo es de 366 días")
        if self.kind == "last_weeks" and self.amount is not None and self.amount > 52:
            raise ValueError("El periodo semanal máximo es de 52 semanas")
        return self


class ReportFilterIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["branch", "category", "product", "payment_method", "sale_status", "sale_type", "reservation_status"]
    value: str = Field(min_length=1, max_length=120)
    match: Literal["exact", "contains"] = "exact"


class ReportRankingIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_n: int = Field(ge=1, le=50)
    direction: Literal["desc", "asc"] = "desc"
    within: Optional[ReportDimension] = None


class ReportIntent(BaseModel):
    """Gemini may classify the request, but cannot supply SQL or column names."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "clarify", "unsupported"]
    domain: ReportDomain
    metric: ReportMetric = Field(description=(
        "Métrica de negocio: units_sold son unidades de ventas completadas; sales_count cuenta ventas; "
        "gross_revenue suma monto_total; net_revenue suma monto_neto (sin IVA, no es utilidad); "
        "item_revenue suma subtotales de prendas; discount_total suma descuentos; "
        "stock_* es inventario actual; units_reserved son unidades en reservas activas."
    ))
    dimensions: list[ReportDimension] = Field(default_factory=list, max_length=4)
    filters: list[ReportFilterIntent] = Field(default_factory=list, max_length=5)
    period: ReportPeriodIntent = Field(default_factory=ReportPeriodIntent)
    ranking: Optional[ReportRankingIntent] = None
    detail: Literal["product", "variant"] = "product"
    clarification: Optional[str] = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def validate_status_shape(self):
        if self.status == "ready":
            if self.domain == "none" or self.metric == "none":
                raise ValueError("Una consulta lista requiere dominio y métrica")
            if len(set(self.dimensions)) != len(self.dimensions):
                raise ValueError("Las dimensiones no pueden repetirse")
        elif not self.clarification:
            raise ValueError("Una consulta que necesita aclaración debe explicar qué falta")
        return self
