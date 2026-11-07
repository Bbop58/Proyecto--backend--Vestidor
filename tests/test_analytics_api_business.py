"""M3: retired routes cannot mutate product prices, offers or inventory."""
import pytest
from app.main import app
from app.api.deps import get_current_user
from tests.test_tenant_sales import shops,snapshot


@pytest.mark.parametrize('method,path',[
    ('post','/forecast/predict'),('get','/markdown-recommendations'),
    ('post','/markdown-recommendations/auto-apply-all'),
    ('post','/markdown-recommendations/not-a-uuid/apply'),
    ('post','/markdown-recommendations/not-a-uuid/dismiss'),
])
def test_retired_ml_routes_are_gone_without_commercial_effects(client,db_session,shops,method,path):
    actor,variant,_=shops['a'];app.dependency_overrides[get_current_user]=lambda:actor
    product=variant.producto
    product.en_oferta=True;product.precio_oferta=7;db_session.commit()
    before=(snapshot(db_session),product.precio_base,product.precio_oferta,product.en_oferta)
    response=getattr(client,method)('/api/v1/analytics'+path,**({'json':{'precio_oferta':1}} if method=='post' else {}))
    assert response.status_code==410,response.text
    db_session.refresh(product)
    assert (snapshot(db_session),product.precio_base,product.precio_oferta,product.en_oferta)==before


def test_query_cannot_select_another_company_and_get_does_not_train(client,shops,monkeypatch):
    from app.ml.ml_service import MLAnalyticsService
    app.dependency_overrides[get_current_user]=lambda:shops['a'][0]
    monkeypatch.setattr(MLAnalyticsService,'train_all',lambda *a:pytest.fail('GET cannot train'))
    response=client.get('/api/v1/analytics/forecast?tenant_id='+str(shops['b'][0].tenant_id))
    assert response.status_code==200
    assert response.json()['tenant_id']==str(shops['a'][0].tenant_id)
