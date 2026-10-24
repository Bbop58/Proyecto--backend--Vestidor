import urllib.request, json, urllib.parse

url_login = 'http://127.0.0.1:8000/api/v1/auth/login'
req_data = urllib.parse.urlencode({'username': 'admin@ficttstore.com', 'password': 'Password123!'}).encode('utf-8')
headers = {'Content-Type': 'application/x-www-form-urlencoded'}

req = urllib.request.Request(url_login, data=req_data, headers=headers)
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
    token = res['access_token']

auth_header = {'Authorization': f'Bearer {token}'}

# 1. Forecast endpoint
req_fc = urllib.request.Request('http://127.0.0.1:8000/api/v1/analytics/forecast?weeks_ahead=4', headers=auth_header)
with urllib.request.urlopen(req_fc) as resp:
    fc_res = json.loads(resp.read().decode('utf-8'))
    print('=== 1. FORECAST RESULT ===')
    print('HTTP STATUS:', resp.status)
    print('R2 SCORE:', fc_res['metricas']['r2_score'])
    print('RMSE:', fc_res['metricas']['rmse'])
    print('MAE:', fc_res['metricas']['mae'])
    print('TOTAL PROYECTADO MES BS:', fc_res['total_proyectado_mes_bs'])
    print('PUNTOS SERIE TEMPORAL:', len(fc_res['serie_temporal']))

# 2. Segments endpoint
req_seg = urllib.request.Request('http://127.0.0.1:8000/api/v1/analytics/segments', headers=auth_header)
with urllib.request.urlopen(req_seg) as resp:
    seg_res = json.loads(resp.read().decode('utf-8'))
    print('\n=== 2. SEGMENTATION RESULT ===')
    print('HTTP STATUS:', resp.status)
    print('SILHOUETTE SCORE:', seg_res['metricas']['silhouette_score'])
    print('INERTIA WSS:', seg_res['metricas']['inercia_wss'])
    for c in seg_res['clusters']:
        print(f"  - Cluster {c['cluster_id']}: {c['nombre_segmento']} ({c['total_clientes']} clientes, {c['porcentaje_poblacion']}%, Gasto Prom: Bs {c['gasto_promedio_bs']})")

# 3. Predict custom endpoint
req_pred = urllib.request.Request(
    'http://127.0.0.1:8000/api/v1/analytics/forecast/predict',
    data=json.dumps({
        'semana_del_ano': 48,
        'mes': 11,
        'temporada': 'Primavera',
        'categoria_nombre': 'Chaquetas y Abrigos',
        'sucursal_nombre': 'Sucursal Central',
        'ventas_semana_anterior_bs': 15000.0
    }).encode('utf-8'),
    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
)
with urllib.request.urlopen(req_pred) as resp:
    pred_res = json.loads(resp.read().decode('utf-8'))
    print('\n=== 3. PREDICT CUSTOM SIMULATION ===')
    print('HTTP STATUS:', resp.status)
    print('DEMANDA PROYECTADA BS:', pred_res['demanda_proyectada_bs'])
    print('UNIDADES ESTIMADAS:', pred_res['unidades_estimadas'])
    print('RANGO:', f"Bs {pred_res['rango_confianza_inferior_bs']} - Bs {pred_res['rango_confianza_superior_bs']}")
