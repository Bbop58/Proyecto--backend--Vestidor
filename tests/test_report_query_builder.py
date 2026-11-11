"""Typed report catalog tests; no live Gemini or commercial database required."""
import pytest
from types import SimpleNamespace
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.schemas.report_intent import ReportIntent
from app.services.report_intent_service import ReportIntentService
from app.services.report_query_builder import ReportQueryBuilder
from app.core.report_period import apply_report_period, ensure_supported_report_period, parse_relative_period
from app.services.voice_report_service import VoiceReportService


def intent(**updates):
    payload = {
        "status": "ready", "domain": "sales", "metric": "units_sold",
        "dimensions": ["branch", "product"], "filters": [],
        "period": {"kind": "all_time"}, "ranking": None, "detail": "product",
    }
    payload.update(updates)
    return ReportIntent.model_validate(payload)


def test_top_n_is_independent_per_branch_and_uses_parameters():
    report = intent(
        ranking={"top_n": 10, "direction": "desc", "within": "branch"},
        period={"kind": "last_months", "amount": 3},
    )
    sql, params, label = ReportQueryBuilder.build(report)
    assert "ROW_NUMBER() OVER (PARTITION BY grouped.sucursal" in sql
    assert "ORDER BY ranked.sucursal, ranked.posicion_ranking ASC" in sql
    assert "posicion_ranking <= :top_n" in sql
    assert "period_amount" in params and params["top_n"] == 10
    assert label == "unidades vendidas"


def test_variant_detail_adds_columns_without_mutating_intent():
    report = intent(dimensions=["branch", "product"], detail="variant")
    before = list(report.dimensions)
    sql, _, _ = ReportQueryBuilder.build(report)
    assert "vp.sku AS sku" in sql and "vp.talla AS talla" in sql and "vp.color AS color" in sql
    assert report.dimensions == before


def test_revenue_by_product_is_rejected_when_it_would_repeat_whole_sale_total():
    report = intent(metric="gross_revenue", dimensions=["branch", "product"])
    with pytest.raises(HTTPException) as error:
        ReportQueryBuilder.build(report)
    assert error.value.status_code == 422


def test_inventory_is_a_current_snapshot_and_rejects_sales_periods():
    report = intent(domain="inventory", metric="stock_available", dimensions=["branch", "product"])
    sql, _, label = ReportQueryBuilder.build(report)
    assert "stock_actual - i.stock_reservado" in sql
    assert label == "stock disponible"
    dated = intent(domain="inventory", metric="stock_available", dimensions=["branch"],
                   period={"kind": "last_months", "amount": 3})
    with pytest.raises(HTTPException) as error:
        ReportQueryBuilder.build(dated)
    assert error.value.status_code == 422


def test_reservation_units_and_calendar_dimensions_use_reservation_columns():
    report = intent(domain="reservations", metric="units_reserved", dimensions=["branch", "week"])
    sql, _, label = ReportQueryBuilder.build(report)
    assert "COALESCE(SUM(dr.cantidad), 0) AS unidades_reservadas" in sql
    assert "r.created_at AT TIME ZONE 'America/La_Paz'" in sql
    assert "v.created_at" not in sql
    assert label == "unidades reservadas"


def test_spoken_relative_periods_resolve_words_and_week_units():
    assert parse_relative_period("ventas durante los últimos tres meses") == (3, "month")
    assert parse_relative_period("ventas en las últimas dos semanas") == (2, "week")
    assert parse_relative_period("ventas de los últimos 14 días") == (14, "day")
    assert VoiceReportService._question_period_label("top 5 productos ultimos tres meses") == "Últimos 3 meses"
    assert VoiceReportService._question_period_label("ventas ultimas dos semanas") == "Últimas 2 semanas"


def test_canonical_report_rejects_periods_above_the_supported_limit_instead_of_returning_all_time():
    with pytest.raises(HTTPException) as error:
        apply_report_period("SELECT * FROM ventas v", "ventas de los últimos 400 días")
    assert error.value.status_code == 422


@pytest.mark.parametrize("question", [
    "ventas desde 2026-01-01 hasta 2026-02-01",
    "ventas de enero a marzo",
    "ventas entre enero y marzo",
    "ventas hasta noviembre",
    "ventas durante 2025",
    "ventas de los últimos dos años",
    "ventas del primer trimestre",
    "ventas del año pasado",
])
def test_unsupported_periods_request_clarification_instead_of_becoming_all_time(question):
    with pytest.raises(HTTPException) as error:
        ensure_supported_report_period(question)
    assert error.value.status_code == 422


def test_supported_relative_period_is_not_rejected():
    ensure_supported_report_period("ventas de los últimos tres meses por sucursal")
    ensure_supported_report_period("ventas de enero")
    ensure_supported_report_period("ventas de este año por sucursal")


def test_unsupported_period_stops_before_gemini_can_return_all_time(monkeypatch):
    def unexpected_interpret(_question):
        raise AssertionError("Gemini must not classify an unsupported period")

    monkeypatch.setattr(ReportIntentService, "interpret", staticmethod(unexpected_interpret))
    with pytest.raises(HTTPException) as error:
        VoiceReportService._prepare_report_query("ventas desde 2026-01-01 hasta 2026-03-31")
    assert error.value.status_code == 422


def test_weekly_branch_trend_does_not_match_the_non_temporal_branch_summary():
    result = VoiceReportService._match_canonical_exam_query("gráfico de ventas por sucursal por semana")
    assert result is not None
    assert "periodo_semanal" in result[0]
    assert "total_recaudado_bs" not in result[0]


def test_canonical_top_products_defaults_to_product_grain_and_can_request_variants():
    product_sql, _ = VoiceReportService._match_canonical_exam_query("Top 10 productos más vendidos")
    variant_sql, _ = VoiceReportService._match_canonical_exam_query("Top 10 variantes más vendidas con SKU")
    assert "STRING_AGG" in product_sql
    assert "GROUP BY p.id, p.nombre " in product_sql
    assert "vp.sku" not in product_sql
    assert "vp.sku" in variant_sql and "vp.talla" in variant_sql
    assert VoiceReportService._requested_top_n("top diez productos") == 10


def test_ambiguous_category_and_time_words_do_not_trigger_unrelated_canonical_report():
    result = VoiceReportService._match_canonical_exam_query("ventas de todas las sucursales en los ultimos 3 meses")
    assert result is not None and "s.nombre AS sucursal" in result[0]
    unrelated = VoiceReportService._match_canonical_exam_query("ventas durante los últimos 3 meses")
    assert unrelated is None


def test_full_report_path_scopes_tenant_and_keeps_filter_values_bound(monkeypatch):
    report_intent = intent(
        ranking={"top_n": 10, "direction": "desc", "within": "branch"},
        period={"kind": "last_months", "amount": 3},
        filters=[{"field": "branch", "value": "Central; DROP TABLE users", "match": "exact"}],
    )

    class Result:
        def __init__(self, returns_rows=False):
            self.returns_rows = returns_rows

        def keys(self):
            return ["sucursal", "producto", "unidades_vendidas"]

        def fetchall(self):
            return [("Central", "Camisa", 4)]

    class FakeDB:
        def __init__(self):
            self.info = {"tenant_scope": uuid4()}
            self.calls = []

        def get_bind(self):
            return SimpleNamespace(dialect=postgresql.dialect())

        def execute(self, statement, params=None):
            sql = str(statement)
            self.calls.append((sql, params))
            return Result(returns_rows=not sql.startswith("SET LOCAL"))

        def rollback(self):
            raise AssertionError("A valid test report must not roll back")

    monkeypatch.setattr(ReportIntentService, "interpret", staticmethod(lambda _question: report_intent))
    db = FakeDB()
    result = VoiceReportService.execute_voice_report(db, "ranking de unidades vendidas por sucursal y producto")

    query_sql, query_params = db.calls[-1]
    assert "ROW_NUMBER() OVER (PARTITION BY" in query_sql
    assert "tenant_id" in query_sql
    assert "Central; DROP TABLE users" not in query_sql
    assert query_params["filter_0"] == "Central; DROP TABLE users"
    assert query_params["top_n"] == 10 and query_params["period_amount"] == 3
    assert result.total_filas == 1
    assert result.criterios_aplicados[1] == "Periodo: \u00daltimos 3 meses"
