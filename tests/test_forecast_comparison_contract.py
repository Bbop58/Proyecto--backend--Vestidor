"""G1: authentic per-method forecasts and safe additive persisted JSON contract."""
from copy import deepcopy
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.ml.supervised_model import train_forecast, predict_method, METHODS
from app.ml.training_result import load_server_model
from app.ml.analytics_store import StoredAnalysis
from app.schemas.analytics import BusinessForecastResult
from tests.test_forecast_business import dataset


def complete_result():
    dates = [(date(2026, 11, 2) + timedelta(weeks=i)).isoformat() for i in range(4)]
    points = [{'fecha': day, 'ventas_bs': 10.0} for day in dates]
    metrics = {'mae': 0.0, 'rmse': 0.0, 'r2': None, 'muestras_prueba': 12}
    return {'metodo': 'ULTIMA_SEMANA', 'etiqueta_metodo': 'Última semana',
        'historico': [{'fecha': '2026-10-26', 'ventas_bs': 10.0}],
        'proyeccion': deepcopy(points), 'total_proyectado_bs': 40.0,
        'metricas': metrics, 'comparacion': [{'metodo': m, **metrics} for m in METHODS],
        'evaluaciones': [], 'filas_entrenamiento_final': 25,
        'proyecciones_metodos': [{'metodo': m, 'etiqueta_metodo': m,
            'proyeccion': deepcopy(points), 'total_proyectado_bs': 40.0} for m in METHODS]}


@pytest.mark.parametrize('values', [
    [100 + i * 3 + (i % 4) * 20 for i in range(40)],
    [100] * 30, [100] * 20 + [0] * 12,
    [10000000.13 + i * 0.07 for i in range(32)],
])
def test_three_real_series_reuse_final_forest_and_match_legacy_winner(values, monkeypatch):
    import app.ml.supervised_model as module
    fitted = []
    original = module.fit_forest
    def fit(history, dates):
        fitted.append(len(history))
        return original(history, dates)
    monkeypatch.setattr(module, 'fit_forest', fit)
    data = dataset(values)
    trained = train_forecast(data)
    result = trained.result['resultado']
    assert len(fitted) == 4  # Three historical folds and one final forest, not seven.
    stored = StoredAnalysis('LISTO', trained.artifact, trained.result,
        {'dataset_hash': data.fingerprint})
    model = load_server_model(stored, data.tenant_id, 'FORECAST')
    series = result['proyecciones_metodos']
    assert len(series) == 3 and {s['metodo'] for s in series} == set(METHODS)
    for series in series:
        expected = predict_method(series['metodo'], model['forest'], values, data.period_end)
        assert [p['ventas_bs'] for p in series['proyeccion']] == [round(v, 2) for v in expected]
        assert [p['fecha'] for p in series['proyeccion']] == [p['fecha'] for p in result['proyeccion']]
        assert series['total_proyectado_bs'] == round(sum(round(v, 2) for v in expected), 2)
    winner = next(s for s in result['proyecciones_metodos'] if s['metodo'] == result['metodo'])
    assert winner['proyeccion'] == result['proyeccion']
    assert winner['total_proyectado_bs'] == result['total_proyectado_bs']
    assert BusinessForecastResult.model_validate(result).model_dump()['proyecciones_metodos'] == result['proyecciones_metodos']
    if len(set(values)) == 1:
        assert result['metodo'] == 'ULTIMA_SEMANA'
        assert all(s['proyeccion'] == winner['proyeccion'] for s in result['proyecciones_metodos'])


@pytest.mark.parametrize('present', [False, True])
def test_legacy_analysis_does_not_fabricate_missing_series(present):
    result = complete_result()
    if present: result['proyecciones_metodos'] = None
    else: result.pop('proyecciones_metodos')
    parsed = BusinessForecastResult.model_validate(result).model_dump()
    assert parsed['proyecciones_metodos'] is None
    assert parsed['proyeccion'] == result['proyeccion']


def test_selection_uses_unrounded_errors_even_when_persisted_mae_rounds_to_a_tie(monkeypatch):
    import app.ml.supervised_model as module
    def predict(method, model, history, start):
        # The three temporal folds all observe100. A reference has slightly
        # lower absolute error, although both MAEs serialize as1.0000.
        return [101.00001 if method == 'MEDIA_PREVIA' else 101.00004] * 4
    monkeypatch.setattr(module, 'predict_method', predict)
    result = train_forecast(dataset([100] * 32)).result['resultado']
    assert result['metodo'] == 'MEDIA_PREVIA'
    assert len({row['mae'] for row in result['comparacion']}) == 1
    assert BusinessForecastResult.model_validate(result).metodo == 'MEDIA_PREVIA'


@pytest.mark.parametrize('bad', [
    'missing', 'duplicate', 'unknown', 'empty', 'date', 'nonweekly', 'negative',
    'nan', 'infinity', 'total', 'winner', 'metrics', 'comparison', 'cents',
])
def test_present_but_incoherent_comparison_is_not_accepted_as_legacy(bad):
    result = complete_result()
    rows = result['proyecciones_metodos']
    if bad == 'missing': rows.pop()
    elif bad == 'duplicate': rows[1]['metodo'] = rows[0]['metodo']
    elif bad == 'unknown': rows[0]['metodo'] = 'INVENTADO'
    elif bad == 'empty': rows.clear()
    elif bad == 'date': rows[1]['proyeccion'][0]['fecha'] = '2026-11-03'
    elif bad == 'nonweekly':
        for row in [*rows, result]: row['proyeccion'][0]['fecha'] = '2026-11-03'
    elif bad in ('negative', 'nan', 'infinity', 'cents'):
        rows[1]['proyeccion'][0]['ventas_bs'] = {'negative': -1, 'nan': float('nan'),
            'infinity': float('inf'), 'cents': 10.001}[bad]
    elif bad == 'total': rows[1]['total_proyectado_bs'] = 41
    elif bad == 'winner': result['total_proyectado_bs'] = 50
    elif bad == 'metrics': result['metricas'] = {**result['metricas'], 'mae': 1}
    elif bad == 'comparison': result['comparacion'].pop()
    with pytest.raises(ValidationError): BusinessForecastResult.model_validate(result)
