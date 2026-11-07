"""Company RFM clustering; labels describe observed groups, not forced archetypes."""
import math
import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from threadpoolctl import threadpool_limits
from app.ml.training_result import outcome


def segment_label(recency,frequency):
    if recency>=90:return 'En riesgo'
    if recency<=30 and frequency>=4:return 'VIP'
    if frequency>=4:return 'Frecuentes'
    return 'Ocasionales'


RECOMMENDATIONS={'VIP':'Revisar preferencias y conservar una atención personalizada.',
    'En riesgo':'Revisar la relación y motivos de inactividad antes de contactar.',
    'Frecuentes':'Revisar disponibilidad de las prendas de compra habitual.',
    'Ocasionales':'Observar hábitos antes de ofrecer incentivos.'}


def train_segments(dataset):
    rows=sorted(dataset.rows,key=lambda row:row['customer_id'])
    x=np.array([[r['recency_days'],r['frequency'],float(r['monetary_bob'])] for r in rows],dtype=float).reshape(-1,3)
    if not np.isfinite(x).all() or (x<0).any() or (len(x) and (x[:,1]<1).any()):
        raise ValueError('Invalid RFM business data')
    if len({r['customer_id'] for r in rows})!=len(rows):raise ValueError('Duplicate buyer')
    if dataset.quality.get('excluded_invalid_sales',0):
        return outcome(dataset,'RFM',None,message='Primero concilia las ventas excluidas.',
            warnings=['Existen ventas incongruentes; no se publica una segmentación nueva.'])
    if len(rows)<12 or len(np.unique(x,axis=0))<4:
        return outcome(dataset,'RFM',None,message='Se requieren 12 compradores y 4 vectores RFM distintos.')
    model=Pipeline([('scaler',StandardScaler()),('kmeans',KMeans(n_clusters=4,n_init=10,random_state=42))])
    with threadpool_limits(limits=1):labels=model.fit_predict(x)
    if len(set(labels))!=4:
        return outcome(dataset,'RFM',None,message='No se pudieron separar cuatro grupos distintos.')
    scaled=model.named_steps['scaler'].transform(x)
    rng=np.random.RandomState(42)
    selected=[int(np.flatnonzero(labels==label)[0]) for label in range(4)]
    remaining=np.setdiff1d(np.arange(len(rows)),selected)
    if len(remaining):selected.extend(rng.choice(remaining,min(1000-len(selected),len(remaining)),replace=False).tolist())
    with threadpool_limits(limits=1):score=float(silhouette_score(scaled[selected],labels[selected]))
    if not math.isfinite(score):raise ValueError('Undefined silhouette')
    clusters=[]
    ordering=sorted(range(4),key=lambda label:tuple(x[labels==label].mean(axis=0)))
    remap={old:new for new,old in enumerate(ordering)};names={}
    for old in ordering:
        members=x[labels==old];r,f,m=members.mean(axis=0);name=segment_label(r,f);names[old]=name
        clusters.append({'cluster_id':remap[old],'nombre':name,'total_clientes':len(members),
            'porcentaje':round(100*len(members)/len(rows),2),'recencia_promedio_dias':round(float(r),2),
            'frecuencia_promedio':round(float(f),2),'monetario_promedio_bs':round(float(m),2),
            'recomendacion':RECOMMENDATIONS[name]})
    sample=sorted(rng.choice(len(rows),min(150,len(rows)),replace=False).tolist())
    points=[{'cliente_id':rows[i]['customer_id'],'nombre':rows[i]['name'],
        'recencia_dias':int(x[i,0]),'frecuencia':int(x[i,1]),'monetario_bs':float(x[i,2]),
        'cluster_id':remap[int(labels[i])],'segmento':names[int(labels[i])]} for i in sample]
    result={'clusters':clusters,'puntos_muestra':points,'muestra_grafico':len(points),
        'metricas':{'silhouette':round(score,6),'muestra_silhouette':len(selected),
            'total_clientes':len(rows),'n_clusters':4,'inercia':float(model.named_steps['kmeans'].inertia_)}}
    warnings=['Segmentos descriptivos de compras originales DEMO; no son propensión de compra ni acciones automáticas.']
    if score<0.25:warnings.append('Separación débil entre grupos; interpreta los segmentos con cautela.')
    return outcome(dataset,'RFM',model,result,message='RFM de compradores identificados en 365 días.',warnings=warnings)
