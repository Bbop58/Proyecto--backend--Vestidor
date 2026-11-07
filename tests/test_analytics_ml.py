"""M2: real company source, no fixed accuracy target or global synthetic model."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import select,delete
from sqlalchemy.orm import Session
from app.models import AnalyticsModel,Category,Product,ProductVariant,Branch,Sale,SaleDetail,User,Role
from app.ml.ml_service import MLAnalyticsService as Service
from app.ml.analytics_store import load_analysis
from app.ml.training_result import load_server_model
from tests.test_analytics_store import store_companies


@pytest.fixture
def business_companies(store_companies):
    engine,companies=store_companies
    now=datetime.now(timezone.utc);end=now.date()-timedelta(days=now.weekday())
    with Session(engine) as db:
        for index,company in enumerate(companies):
            role=Role(tenant_id=company,name='cliente',description='QA ML buyer')
            db.add(role);db.flush()
            category=Category(tenant_id=company,nombre='QA ML');db.add(category);db.flush()
            product=Product(tenant_id=company,nombre='QA ML',precio_base=10,categoria_id=category.id)
            branch=Branch(tenant_id=company,nombre='QA',ciudad='QA',direccion='QA')
            db.add_all([product,branch]);db.flush()
            variant=ProductVariant(producto_id=product.id,sku=uuid.uuid4().hex,talla='M',color='QA')
            db.add(variant);db.flush()
            users=[]
            for i in range(12 if index==0 else 8):
                user=User(tenant_id=company,role_id=role.id,email=uuid.uuid4().hex+'@example.com',full_name='QA buyer',hashed_password='unused')
                db.add(user);db.flush();users.append(user.id)
            for week in range(30):
                when=datetime.combine(end-timedelta(weeks=30-week),datetime.min.time(),timezone.utc)+timedelta(hours=16)
                amount=Decimal((week+1)*10*(index+1))
                sale=Sale(tenant_id=company,sucursal_id=branch.id,numero_recibo='ML-'+uuid.uuid4().hex[:20],
                    cliente_id=users[week%len(users)],created_at=when,monto_subtotal=amount,monto_total=amount)
                db.add(sale);db.flush()
                db.add(SaleDetail(venta_id=sale.id,variante_id=variant.id,cantidad=1,precio_unitario=amount,subtotal=amount))
        db.commit()
    yield engine,companies
    with engine.begin() as connection:
        connection.execute(delete(Sale.__table__).where(Sale.tenant_id.in_(companies)))
        connection.execute(delete(User.__table__).where(User.tenant_id.in_(companies)))


def train_inline(kind,data):
    from app.ml.supervised_model import train_forecast
    from app.ml.unsupervised_model import train_segments
    return {'FORECAST':train_forecast,'RFM':train_segments}[kind](data)


def test_two_companies_train_real_data_reload_estimator_and_get_never_trains(business_companies,monkeypatch):
    engine,(a,b)=business_companies
    monkeypatch.setattr('app.ml.ml_service.run_training',train_inline)
    with Session(engine) as db:
        assert Service.get_demand_forecast(db,a)['estado']=='SIN_ANALISIS'
        Service.train_all(db,a);Service.train_all(db,b)
    monkeypatch.setattr('app.ml.ml_service.run_training',lambda *args:pytest.fail('GET must never train'))
    with Session(engine) as db:
        first=Service.get_demand_forecast(db,a);second=Service.get_demand_forecast(db,b)
        assert first['estado']==second['estado']=='LISTO'
        assert first['resultado']['historico'][-1]['ventas_bs']==300
        assert second['resultado']['historico'][-1]['ventas_bs']==600
        assert Service.get_customer_segments(db,b)['estado']=='DATOS_INSUFICIENTES'
        assert Service.get_customer_segments(db,a)['estado']=='LISTO'
        assert not first['datos_cambiaron']
        model=load_server_model(load_analysis(db,a,'FORECAST'),a,'FORECAST')
        assert model['forest'].named_steps['forest'].n_jobs==1
        from app.ml.supervised_model import predict_method
        from datetime import date
        for forecast,company in ((first,a),(second,b)):
            result=forecast['resultado'];history=[p['ventas_bs'] for p in result['historico']]
            loaded=load_server_model(load_analysis(db,company,'FORECAST'),company,'FORECAST')
            assert len(result['proyecciones_metodos'])==3
            for row in result['proyecciones_metodos']:
                expected=predict_method(row['metodo'],loaded['forest'],history,date.fromisoformat(row['proyeccion'][0]['fecha']))
                assert [p['ventas_bs'] for p in row['proyeccion']]==[round(v,2) for v in expected]
        with pytest.raises(ValueError):load_server_model(load_analysis(db,a,'FORECAST'),b,'FORECAST')
        assert len(db.scalars(select(AnalyticsModel)).all())==4


@pytest.mark.parametrize('legacy', [True,False])
def test_saved_legacy_is_readable_but_malformed_new_comparison_requires_update(business_companies,monkeypatch,legacy):
    from copy import deepcopy
    engine,(company,_)=business_companies
    monkeypatch.setattr('app.ml.ml_service.run_training',train_inline)
    with Session(engine) as db:Service.train_all(db,company)
    with Session(engine) as db:
        row=db.scalar(select(AnalyticsModel).where(AnalyticsModel.tenant_id==company,AnalyticsModel.kind=='FORECAST'))
        payload=deepcopy(row.result)
        if legacy:payload['resultado'].pop('proyecciones_metodos')
        else:payload['resultado']['proyecciones_metodos'].pop()
        row.result=payload;db.commit()
    monkeypatch.setattr('app.ml.ml_service.run_training',lambda *args:pytest.fail('GET cannot train'))
    with Session(engine) as db:
        result=Service.get_demand_forecast(db,company)
        assert result['estado']==('LISTO' if legacy else 'REQUIERE_ACTUALIZAR')
        if legacy:
            assert result['resultado']['proyecciones_metodos'] is None
            assert result['resultado']['proyeccion']==payload['resultado']['proyeccion']
        else:assert result['resultado'] is None
        assert Service.get_customer_segments(db,company)['estado']=='LISTO'


@pytest.mark.parametrize('bad', ['missing','total'])
def test_incomplete_new_training_preserves_previous_valid_forecast(business_companies,monkeypatch,bad):
    engine,(company,_)=business_companies
    monkeypatch.setattr('app.ml.ml_service.run_training',train_inline)
    with Session(engine) as db:Service.train_all(db,company)
    with Session(engine) as db:
        stored=load_analysis(db,company,'FORECAST');before=(stored.result,stored.artifact,stored.trained_at)
    def corrupt(kind,data):
        outcome=train_inline(kind,data)
        if kind=='FORECAST':
            if bad=='missing':outcome.result['resultado'].pop('proyecciones_metodos')
            else:outcome.result['resultado']['proyecciones_metodos'][1]['total_proyectado_bs']+=1
        return outcome
    monkeypatch.setattr('app.ml.ml_service.run_training',corrupt)
    with Session(engine) as db:
        with pytest.raises(HTTPException) as error:Service.train_all(db,company)
        assert error.value.status_code==503 and error.value.detail['actualizados']==['RFM']
    with Session(engine) as db:
        stored=load_analysis(db,company,'FORECAST')
        assert (stored.result,stored.artifact,stored.trained_at)==before
        assert Service.get_demand_forecast(db,company)['estado']=='LISTO'


def test_timeout_preserves_previous_and_does_not_block_other_tab(business_companies,monkeypatch):
    engine,(a,_)=business_companies
    monkeypatch.setattr('app.ml.ml_service.run_training',train_inline)
    with Session(engine) as db:Service.train_all(db,a)
    with Session(engine) as db:before=load_analysis(db,a,'FORECAST').trained_at
    def fail_forecast(kind,data):
        if kind=='FORECAST':raise HTTPException(503,'QA timeout')
        return train_inline(kind,data)
    monkeypatch.setattr('app.ml.ml_service.run_training',fail_forecast)
    with Session(engine) as db:
        with pytest.raises(HTTPException) as exc:Service.train_all(db,a)
        assert exc.value.status_code==503 and exc.value.detail['actualizados']==['RFM']
    with Session(engine) as db:
        assert load_analysis(db,a,'FORECAST').trained_at==before
        assert Service.get_demand_forecast(db,a)['estado']=='LISTO'


def test_new_buyer_new_receipt_and_source_week_are_detected(business_companies,monkeypatch):
    engine,(a,_)=business_companies
    monkeypatch.setattr('app.ml.ml_service.run_training',train_inline)
    with Session(engine) as db:Service.train_all(db,a)
    with Session(engine) as db:
        sale=db.scalars(select(Sale).where(Sale.tenant_id==a).order_by(Sale.created_at.desc())).first()
        sale.monto_subtotal+=1;sale.monto_total+=1;sale.detalles[0].precio_unitario+=1;sale.detalles[0].subtotal+=1
        db.commit()
    with Session(engine) as db:
        assert Service.get_demand_forecast(db,a)['datos_cambiaron']
        assert Service.get_customer_segments(db,a)['datos_cambiaron']
        Service.train_all(db,a)
    with Session(engine) as db:assert not Service.get_demand_forecast(db,a)['datos_cambiaron']


def test_open_week_and_new_customer_change_rfm_but_not_closed_week_forecast(business_companies,monkeypatch):
    engine,(a,_)=business_companies;monkeypatch.setattr('app.ml.ml_service.run_training',train_inline)
    with Session(engine) as db:Service.train_all(db,a)
    with Session(engine) as db:
        role=db.scalar(select(Role).where(Role.tenant_id==a))
        customer=User(tenant_id=a,role_id=role.id,email=uuid.uuid4().hex+'@example.com',full_name='New QA buyer',hashed_password='unused')
        db.add(customer);db.commit()
    with Session(engine) as db:
        assert Service.get_customer_segments(db,a)['datos_cambiaron']
        assert not Service.get_demand_forecast(db,a)['datos_cambiaron']
        Service.train_all(db,a)
    with Session(engine) as db:
        old=db.scalars(select(Sale).where(Sale.tenant_id==a)).first()
        sale=Sale(tenant_id=a,sucursal_id=old.sucursal_id,cliente_id=old.cliente_id,
            numero_recibo='ML-'+uuid.uuid4().hex[:20],monto_subtotal=20,monto_total=20)
        db.add(sale);db.flush()
        db.add(SaleDetail(venta_id=sale.id,variante_id=old.detalles[0].variante_id,cantidad=1,precio_unitario=20,subtotal=20))
        db.commit()
    with Session(engine) as db:
        assert Service.get_customer_segments(db,a)['datos_cambiaron']
        assert not Service.get_demand_forecast(db,a)['datos_cambiaron']


def test_corrupt_or_incompatible_stored_result_requires_update(business_companies,monkeypatch):
    engine,(a,_)=business_companies;monkeypatch.setattr('app.ml.ml_service.run_training',train_inline)
    with Session(engine) as db:Service.train_all(db,a)
    with Session(engine) as db:
        row=db.scalar(select(AnalyticsModel).where(AnalyticsModel.tenant_id==a,AnalyticsModel.kind=='FORECAST'))
        row.result={**row.result,'resultado':{'bad':True}};db.commit()
        assert Service.get_demand_forecast(db,a)['estado']=='REQUIERE_ACTUALIZAR'


def test_http_empty_state_and_fixed_horizon(client,db_session,shops,monkeypatch):
    from app.api.deps import get_current_user
    from app.main import app
    app.dependency_overrides[get_current_user]=lambda:shops['a'][0]
    monkeypatch.setattr('app.ml.ml_service.run_training',lambda *args:pytest.fail('GET training'))
    for path in ['forecast','segments']:
        response=client.get('/api/v1/analytics/'+path)
        assert response.status_code==200 and response.json()['estado']=='SIN_ANALISIS'
        assert response.json()['tenant_id']==str(shops['a'][0].tenant_id)
    assert client.get('/api/v1/analytics/forecast?weeks_ahead=8').status_code==422


from tests.test_tenant_sales import shops
