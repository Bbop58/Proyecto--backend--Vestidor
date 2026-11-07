"""Destructive ML rehearsal ONLY on an explicitly named local QA database."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-disposable-demo',action='store_true',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from sqlalchemy.engine import make_url
    url=make_url(os.environ.get('DATABASE_URL',''))
    if (url.get_backend_name()!='postgresql' or url.host not in {'127.0.0.1','localhost','::1'}
        or not (url.database or '').startswith('fashionstore_test_') or url.query):
        parser.error('Only a disposable local fashionstore_test_ PostgreSQL database is allowed')
    from sqlalchemy import create_engine,select,func
    from sqlalchemy.orm import Session
    from scripts.reset_demo_database import reset_demo_database
    from app.models import Tenant,User,Sale,AnalyticsModel
    from app.core.tenant_scope import bind_tenant
    from app.ml.ml_service import MLAnalyticsService as Service
    from app.ml.analytics_store import load_analysis
    from app.ml.training_result import load_server_model
    from app.ml.supervised_model import predict_method
    from fastapi.testclient import TestClient
    from app.main import app
    from datetime import date
    engine=create_engine(url)
    try:
        seed=reset_demo_database(engine)
        assert not any(seed['checks'].values()) and not seed['tables_changed_on_repeat']
        output={'seed_checks':len(seed['checks']),'seed_repeated_unchanged':True,
            'models_after_fresh':seed['counts']['analytics_models'],'companies':[]}
        with Session(engine) as db:
            companies=[(row.id,row.slug) for row in db.scalars(select(Tenant).where(Tenant.es_operadora.is_(False)))]
            sales_before=db.scalar(select(func.count()).select_from(Sale))
        for company,slug in companies:
            with TestClient(app) as client:
                login=client.post('/api/v1/auth/login',json={'email':'admin@'+slug+'.com',
                    'password':'Password123!','tenant_id':str(company)})
                assert login.status_code==200
                headers={'Authorization':'Bearer '+login.json()['access_token']}
                trained=client.post('/api/v1/analytics/train',headers=headers)
                assert trained.status_code==200,trained.status_code
                for path in ('forecast','segments'):
                    http=client.get('/api/v1/analytics/'+path,headers=headers)
                    assert http.status_code==200 and http.json()['tenant_id']==str(company)
                assert client.get('/api/v1/analytics/forecast?weeks_ahead=12',headers=headers).status_code==422
            # New session reloads database artifacts and stored analysis, no training.
            with Session(engine) as db:
                forecast=Service.get_demand_forecast(db,company)
                segments=Service.get_customer_segments(db,company)
                model=load_server_model(load_analysis(db,company,'FORECAST'),company,'FORECAST')
                result=forecast['resultado'];history=[row['ventas_bs'] for row in result['historico']]
                predicted=predict_method(model['selected_method'],model['forest'],history,
                    date.fromisoformat(result['proyeccion'][0]['fecha']))
                assert [round(value,2) for value in predicted]==[row['ventas_bs'] for row in result['proyeccion']]
                series=result['proyecciones_metodos']
                assert len(series)==3 and {s['metodo'] for s in series}=={'RANDOM_FOREST','ULTIMA_SEMANA','MEDIA_PREVIA'}
                for row in series:
                    predicted=predict_method(row['metodo'],model['forest'],history,
                        date.fromisoformat(row['proyeccion'][0]['fecha']))
                    assert [round(value,2) for value in predicted]==[p['ventas_bs'] for p in row['proyeccion']]
                    assert [p['fecha'] for p in row['proyeccion']]==[p['fecha'] for p in result['proyeccion']]
                    assert row['total_proyectado_bs']==round(sum(p['ventas_bs'] for p in row['proyeccion']),2)
                assert not forecast['datos_cambiaron'] and not segments['datos_cambiaron']
                assert forecast['estado']=='LISTO'
                assert segments['estado']=='LISTO'  # Both default demo cohorts now meet the minimum.
                output['companies'].append({'slug':slug,'forecast_state':forecast['estado'],
                    'forecast_method':result['metodo'],'projected_bob':result['total_proyectado_bs'],
                    'metrics':result['metricas'],'comparison':result['comparacion'],
                    'method_projections':series,
                    'first_forecast_week':result['proyeccion'][0]['fecha'],'cutoff':forecast['fecha_corte'],
                    'source_quality':forecast['calidad'],'segments_state':segments['estado'],
                    'segments_quality':segments['calidad'],
                    'segments_metrics':segments['resultado']['metricas'] if segments['resultado'] else None,
                    'artifact_reloaded_and_predictions_identical':True,'http_login_train_read':True})
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Sale))==sales_before
            assert db.scalar(select(func.count()).select_from(AnalyticsModel))==4
        output.update(sales_unchanged=sales_before,stored_models=4,provider_calls=0)
        args.output.write_text(json.dumps(output,indent=2),encoding='utf-8')
        print(json.dumps(output,indent=2))
    finally:engine.dispose()


if __name__=='__main__':main()
