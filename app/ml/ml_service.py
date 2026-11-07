"""Company-scoped manual analysis; no commercial price or stock mutations."""
from datetime import datetime,timezone
from app.ml.analytics_data import company_id,build_weekly_sales,build_customer_rfm
from app.ml.analytics_store import analysis_update,save_analysis,load_analysis,make_metadata
from app.ml.training_runner import run_training
from fastapi import HTTPException


class MLAnalyticsService:
    @classmethod
    def train_all(cls,db,tenant_id):
        tenant=company_id(db,tenant_id)
        engine=db.get_bind().engine
        cutoff=datetime.now(timezone.utc)
        results={};errors={}
        with analysis_update(engine,tenant) as work:
            datasets={'FORECAST':build_weekly_sales(work,tenant,cutoff),
                      'RFM':build_customer_rfm(work,tenant,cutoff)}
            for kind,dataset in datasets.items():
                try:
                    trained=run_training(kind,dataset)
                    if kind=='FORECAST' and trained.result.get('estado')=='LISTO':
                        # Validate before replacing the last good analysis. Old
                        # JSON remains readable, but a new training must publish
                        # all three authentic series, not a legacy-only payload.
                        from app.schemas.analytics import BusinessForecastResult
                        forecast=BusinessForecastResult.model_validate(trained.result['resultado'])
                        if forecast.proyecciones_metodos is None:
                            raise ValueError('New forecast requires all three method projections')
                    with work.begin_nested():
                        save_analysis(work,tenant,kind,trained.artifact,trained.result,
                            make_metadata(dataset.fingerprint,cutoff))
                    results[kind]=trained.result
                except HTTPException as error:
                    errors[kind]=error.detail
                except Exception:
                    errors[kind]='No se pudo guardar el análisis; se conserva el anterior.'
        # Both successful writes are committed before surfacing partial failure.
        if errors:
            raise HTTPException(503,{'mensaje':'Una o más actualizaciones fallaron; consulta cada pestaña.',
                'errores':errors,'actualizados':list(results)})
        return {'estado':'ACTUALIZADO','tenant_id':str(tenant),'fecha_corte':cutoff.isoformat(),
                'analisis':results}

    @classmethod
    def _read(cls,db,tenant_id,kind):
        tenant=company_id(db,tenant_id)
        stored=load_analysis(db,tenant,kind)
        response={'estado':stored.state,'tenant_id':str(tenant),'origen_datos':'BASE_DATOS_EMPRESA',
            'entorno':'DEMO','fecha_corte':None,'fecha_entrenamiento':None,'datos_cambiaron':False,
            'calidad':{},'advertencias':[],'mensaje':'Pulsa Actualizar análisis para generar resultados.',
            'resultado':None}
        if stored.trained_at:response['fecha_entrenamiento']=stored.trained_at.isoformat()
        if stored.state!='LISTO':return response
        # GET consumes the validated JSON only. No unpickle or training in reads.
        from app.schemas.analytics import StoredBusinessAnalysis,BusinessForecastResult,BusinessSegmentResult
        try:
            payload=StoredBusinessAnalysis.model_validate(stored.result).model_dump()
            if payload['estado']=='LISTO':
                schema=BusinessForecastResult if kind=='FORECAST' else BusinessSegmentResult
                payload['resultado']=schema.model_validate(payload['resultado']).model_dump()
            elif payload['resultado'] is not None:raise ValueError('Invalid insufficient result')
        except Exception:
            response.update(estado='REQUIERE_ACTUALIZAR',mensaje='El resultado guardado es incompatible; actualiza el análisis.')
            return response
        builder=build_weekly_sales if kind=='FORECAST' else build_customer_rfm
        current=builder(db,tenant,datetime.now(timezone.utc))
        response.update(payload,fecha_corte=stored.metadata['cutoff'],
            datos_cambiaron=current.fingerprint!=stored.metadata['dataset_hash'])
        if response['datos_cambiaron']:
            response['advertencias'].append('Cambió la fuente desde el corte; actualiza manualmente el análisis.')
        return response

    @classmethod
    def get_demand_forecast(cls,db,tenant_id):
        return cls._read(db,tenant_id,'FORECAST')

    @classmethod
    def get_customer_segments(cls,db,tenant_id):
        return cls._read(db,tenant_id,'RFM')
