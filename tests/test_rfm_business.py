from datetime import datetime,timezone
from decimal import Decimal
import uuid
import pytest
from app.ml.analytics_data import RfmDataset,fingerprint
from app.ml.unsupervised_model import train_segments, segment_label


def dataset(n=24):
    rows=[{'customer_id':str(uuid.UUID(int=i+1)),'name':f'QA {i}',
           'recency_days':i*5,'frequency':1+i%6,'monetary_bob':Decimal(30+i*17)} for i in range(n)]
    return RfmDataset(uuid.uuid4(),datetime(2026,1,1,tzinfo=timezone.utc),rows,
        {'eligible_buyers':n,'guest_receipts':2,'customers_without_purchases':3,'excluded_invalid_sales':0},fingerprint(rows))


def test_real_ids_four_clusters_descriptive_labels_and_repeatability():
    data=dataset();first=train_segments(data);second=train_segments(data)
    assert first.result==second.result
    result=first.result['resultado']
    assert len(result['clusters'])==4
    assert sum(c['total_clientes'] for c in result['clusters'])==24
    assert {r['cliente_id'] for r in result['puntos_muestra']}=={r['customer_id'] for r in data.rows}
    assert -1<=result['metricas']['silhouette']<=1
    assert first.result['calidad']['customers_without_purchases']==3


@pytest.mark.parametrize('n',[0,8,11])
def test_small_real_company_is_insufficient_not_fabricated(n):
    result=train_segments(dataset(n)).result
    assert result['estado']=='DATOS_INSUFICIENTES' and result['resultado'] is None
    assert result['calidad']['eligible_buyers']==n


def test_constants_insufficient_and_nonfinite_rejected():
    data=dataset()
    for row in data.rows:row.update(recency_days=1,frequency=1,monetary_bob=Decimal(20))
    assert train_segments(data).result['estado']=='DATOS_INSUFICIENTES'
    data.rows[0]['monetary_bob']=Decimal('Infinity')
    with pytest.raises(ValueError):train_segments(data)


def test_no_forced_business_archetypes():
    assert segment_label(15,4)=='VIP'
    assert segment_label(30,4)=='VIP'
    assert segment_label(31,4)=='Frecuentes'
    assert segment_label(90,8)=='En riesgo'
    assert segment_label(45,1)=='Ocasionales'
    data=dataset()
    for i,row in enumerate(data.rows):row.update(recency_days=40+i,frequency=1+i%2)
    labels={c['nombre'] for c in train_segments(data).result['resultado']['clusters']}
    assert labels=={'Ocasionales'}


def test_large_dataset_sampling_is_bounded_and_explicit():
    result=train_segments(dataset(1050)).result['resultado']
    assert len(result['puntos_muestra'])==150
    assert result['metricas']['muestra_silhouette']<=1000
    assert result['metricas']['total_clientes']==1050
    assert result['muestra_grafico']==150


def test_weak_separation_is_disclosed(monkeypatch):
    monkeypatch.setattr('app.ml.unsupervised_model.silhouette_score',lambda *a,**kw:0.1)
    result=train_segments(dataset()).result
    assert result['estado']=='LISTO'
    assert any('débil' in warning for warning in result['advertencias'])
