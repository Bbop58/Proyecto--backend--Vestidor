"""Four-week company merchandise forecast; causal temporal evaluation only."""
from datetime import timedelta
import math
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from app.ml.training_result import outcome

METHODS=('ULTIMA_SEMANA','MEDIA_PREVIA','RANDOM_FOREST')  # Simpler wins ties.
LABELS={'ULTIMA_SEMANA':'Método de referencia: última semana',
        'MEDIA_PREVIA':'Método de referencia: media previa', 'RANDOM_FOREST':'Random Forest'}


def feature_row(day, history):
    if len(history)<3:raise ValueError('Three previous weeks required')
    angle=2*math.pi*day.isocalendar().week/53
    return np.array([history[-3],history[-2],history[-1],sum(history[-3:])/3,
                     math.sin(angle),math.cos(angle)],dtype=float)


def fit_forest(values, dates):
    x=np.array([feature_row(dates[i],values[:i]) for i in range(3,len(values))])
    model=Pipeline([('forest',RandomForestRegressor(n_estimators=80,max_depth=6,
        min_samples_leaf=2,random_state=42,n_jobs=1))])
    model.fit(x,np.array(values[3:]))
    return model


def recursive_predict(model, history, start, weeks=4):
    previous=list(history);predictions=[]
    for i in range(weeks):
        value=float(model.predict(np.array([feature_row(start+timedelta(weeks=i),previous)]))[0])
        if not math.isfinite(value):raise ValueError('Nonfinite prediction')
        value=max(0.,value);predictions.append(value);previous.append(value)
    return predictions


def predict_method(method, model, history, start):
    if method=='RANDOM_FOREST':return recursive_predict(model,history,start)
    if method=='ULTIMA_SEMANA':return [float(history[-1])]*4
    return [float(sum(history[-3:])/3)]*4


def metrics(actual, predictions):
    actual=np.array(actual,dtype=float);predictions=np.array(predictions,dtype=float)
    error=actual-predictions;denominator=float(np.sum((actual-actual.mean())**2))
    return {'mae':round(float(np.mean(np.abs(error))),4),
            'rmse':round(float(np.sqrt(np.mean(error**2))),4),
            'r2':round(1-float(np.sum(error**2))/denominator,6) if denominator>0 else None,
            'muestras_prueba':len(actual)}


def train_forecast(dataset):
    dates=[r['week_start'] for r in dataset.rows]
    values=[float(r['sales_bob']) for r in dataset.rows]
    if (any(not math.isfinite(v) or v<0 for v in values)
        or any(day.weekday()!=0 for day in dates)
        or any(b-a!=timedelta(weeks=1) for a,b in zip(dates,dates[1:]))
        or (dates and dates[-1]+timedelta(weeks=1)!=dataset.period_end)):
        raise ValueError('Invalid weekly business data')
    if dataset.quality.get('excluded_invalid_sales',0):
        return outcome(dataset,'FORECAST',None,message='Primero concilia las ventas excluidas.',
            warnings=['Existen ventas incongruentes; no se publica un pronóstico nuevo.'])
    if len(values)<27 or sum(v>0 for v in values)<8:
        return outcome(dataset,'FORECAST',None,message='Se requieren 27 semanas observadas y 8 con ventas.')
    predictions={key:[] for key in METHODS};actual=[];evaluations=[]
    for origin in (len(values)-12,len(values)-8,len(values)-4):
        prefix=values[:origin];model=fit_forest(prefix,dates[:origin])
        observed=values[origin:origin+4];actual.extend(observed)
        evaluation={'inicio_prueba':dates[origin].isoformat(),'fin_entrenamiento':dates[origin-1].isoformat(),
                    'filas_entrenamiento':origin-3,'semanas_prueba':4,'metodos':{}}
        for method in METHODS:
            predicted=predict_method(method,model,prefix,dates[origin]);predictions[method].extend(predicted)
            evaluation['metodos'][method]=metrics(observed,predicted)
        evaluations.append(evaluation)
    comparison=[{'metodo':method,**metrics(actual,predictions[method])} for method in METHODS]
    method=min(METHODS,key=lambda m:float(np.mean(np.abs(np.array(actual)-np.array(predictions[m])))))
    forest=fit_forest(values,dates)
    # Publish each method's actual four-week output, reusing the one final
    # forest. References remain references; selecting one never hides the ML.
    series=[]
    for candidate in METHODS:
        future=predict_method(candidate,forest,values,dataset.period_end)
        projection=[{'fecha':(dataset.period_end+timedelta(weeks=i)).isoformat(),
                     'ventas_bs':round(v,2)} for i,v in enumerate(future)]
        series.append({'metodo':candidate,'etiqueta_metodo':LABELS[candidate],
            'proyeccion':projection,'total_proyectado_bs':round(sum(p['ventas_bs'] for p in projection),2)})
    winner=next(row for row in series if row['metodo']==method)
    result={'metodo':method,'etiqueta_metodo':LABELS[method],
        'historico':[{'fecha':day.isoformat(),'ventas_bs':value} for day,value in zip(dates,values)],
        'proyeccion':winner['proyeccion'],
        'total_proyectado_bs':winner['total_proyectado_bs'],
        'proyecciones_metodos':series,
        'metricas':next({k:v for k,v in row.items() if k!='metodo'} for row in comparison if row['metodo']==method),
        'comparacion':comparison,'evaluaciones':evaluations,'filas_entrenamiento_final':len(values)-3}
    warnings=['Evaluación sobre datos DEMO; no demuestra precisión comercial.']
    if result['metricas']['r2'] is not None and result['metricas']['r2']<0:
        warnings.append('R² negativo en la evaluación; usa esta estimación con cautela, no como garantía de ventas.')
    return outcome(dataset,'FORECAST',{'forest':forest,'selected_method':method},result,
        message='Estimación de mercadería original completada, sin envío; no es utilidad.',
        warnings=warnings)
