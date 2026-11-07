from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import uuid
import numpy as np
import pytest
from app.ml.analytics_data import ForecastDataset, fingerprint
from app.ml.supervised_model import train_forecast, feature_row, recursive_predict


def dataset(values):
    start = date(2025, 1, 6)
    rows = [{'week_start':start+timedelta(weeks=i),'sales_bob':Decimal(str(v)),
             'receipt_count':int(v>0)} for i,v in enumerate(values)]
    return ForecastDataset(uuid.uuid4(),datetime(2026,1,1,tzinfo=timezone.utc),
        start+timedelta(weeks=len(rows)),rows,
        {'weeks':len(rows),'nonzero_weeks':sum(v>0 for v in values),'excluded_invalid_sales':0},
        fingerprint(rows))


def test_features_only_use_previous_targets_and_recursive_does_not_see_actual_future():
    assert np.array_equal(feature_row(date(2025,1,27),[10,20,30]),
                          feature_row(date(2025,1,27),[999,10,20,30]))
    class Spy:
        def __init__(self):self.calls=[]
        def predict(self,x):self.calls.append(x.copy());return [x[0][2]+1]
    model=Spy();pred=recursive_predict(model,[10,20,30],date(2025,1,27),4)
    assert pred==[31,32,33,34]
    assert [x[0][2] for x in model.calls]==[30,31,32,33]


def test_fixed_four_weeks_real_history_evaluation_and_determinism():
    data=dataset([100+i*3+(i%4)*20 for i in range(40)])
    first=train_forecast(data);second=train_forecast(data)
    assert first.result==second.result
    result=first.result['resultado']
    assert first.result['estado']=='LISTO'
    assert [r['ventas_bs'] for r in result['historico']]==[float(r['sales_bob']) for r in data.rows]
    assert len(result['proyeccion'])==4
    assert result['proyeccion'][0]['fecha']==data.period_end.isoformat()
    assert len(result['comparacion'])==3 and len(result['evaluaciones'])==3
    assert all(e['filas_entrenamiento']>=12 and e['semanas_prueba']==4 for e in result['evaluaciones'])
    assert result['metricas']['muestras_prueba']==12
    assert not any(k in str(result) for k in ['confianza','unidades_estimadas','crecimiento'])


def test_constant_reference_wins_tie_and_zero_is_not_replaced():
    constant=train_forecast(dataset([100]*30)).result['resultado']
    assert constant['metodo']=='ULTIMA_SEMANA'
    assert constant['etiqueta_metodo']=='Método de referencia: última semana'
    assert constant['metricas']['r2'] is None
    values=[100]*20+[0]*12
    result=train_forecast(dataset(values)).result['resultado']
    assert result['historico'][-1]['ventas_bs']==0
    assert result['proyeccion'][0]['ventas_bs']==0


@pytest.mark.parametrize('values',[[],[100]*26,[0]*40,[100]*7+[0]*25])
def test_insufficient_history_never_generates_projection(values):
    result=train_forecast(dataset(values)).result
    assert result['estado']=='DATOS_INSUFICIENTES' and result['resultado'] is None


def test_invalid_nonfinite_or_nonweekly_data_is_rejected():
    data=dataset([100]*30);data.rows[2]['sales_bob']=Decimal('NaN')
    with pytest.raises(ValueError):train_forecast(data)
    data=dataset([100]*30);data.rows[2]['week_start']+=timedelta(days=1)
    with pytest.raises(ValueError):train_forecast(data)


def test_invalid_source_receipts_block_new_model_with_explicit_warning():
    data=dataset([100]*30);data.quality['excluded_invalid_sales']=1
    result=train_forecast(data).result
    assert result['estado']=='DATOS_INSUFICIENTES' and result['advertencias']


def test_temporal_folds_fit_only_prefix_and_predict_recursively(monkeypatch):
    import app.ml.supervised_model as module
    data=dataset([100+i*7 for i in range(32)])
    prefixes=[];starts=[]
    original_fit=module.fit_forest;original_recursive=module.recursive_predict
    def fit(values,dates):prefixes.append(list(values));return original_fit(values,dates)
    def predict(model,values,start,weeks=4):
        starts.append((list(values),start));return original_recursive(model,values,start,weeks)
    monkeypatch.setattr(module,'fit_forest',fit);monkeypatch.setattr(module,'recursive_predict',predict)
    result=train_forecast(data).result['resultado']
    assert [len(v) for v in prefixes]==[20,24,28,32]
    assert [len(v) for v,start in starts][:3]==[20,24,28]
    assert all(start==data.rows[len(v)]['week_start'] for v,start in starts[:3])
    for comparison in result['comparacion']:
        assert comparison['muestras_prueba']==12


def test_metrics_allow_negative_r2_without_fake_accuracy():
    from app.ml.supervised_model import metrics
    assert metrics([1,2,3,4],[100]*4)['r2']<0
