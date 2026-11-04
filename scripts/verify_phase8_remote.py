"""Bounded checks against this project's explicitly disposable hosted demo.

Uses only public demo seed credentials. No tokens, exports, photos or secrets
are written to evidence. Does not capture a payment or restore a remote DB.
"""
import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE='https://proyecto-backend-vestidor-production.up.railway.app/api/v1'
ORIGIN='https://proyecto-frontend-vestidor-ten.vercel.app'


def call(method,path,token=None,data=None,timeout=70):
    headers={'Origin':ORIGIN,'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    request=urllib.request.Request(BASE+path,method=method,headers=headers,
        data=json.dumps(data).encode() if data is not None else None)
    start=time.monotonic()
    try:response=urllib.request.urlopen(request,timeout=timeout)
    except urllib.error.HTTPError as error:response=error
    raw=response.read();body=json.loads(raw) if raw else None
    return response.status,body,response.headers,round(time.monotonic()-start,2)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--providers',action='store_true')
    parser.add_argument('--output',required=True);args=parser.parse_args()
    results=[];sessions={}
    _,tenants,_,_=call('GET','/subscriptions/public/tenants')
    try:
        for slug in ['urbanstyle','modaexpress']:
            company=next(t for t in tenants if t['slug']==slug)
            status,login,headers,seconds=call('POST','/auth/login',data={
                'email':'admin@'+slug+'.com','password':'Password123!','tenant_id':company['id']})
            results.append({'name':slug+' login','http':status,'seconds':seconds,
                            'cors':headers.get('Access-Control-Allow-Origin')==ORIGIN})
            if status!=200:continue
            token=login['access_token'];sessions[slug]=(token,login['refresh_token'],company)
            for path in ['/users/me','/productos','/backups/stats','/wallet/me']:
                status,body,_,seconds=call('GET',path,token)
                entry={'name':slug+path,'http':status,'seconds':seconds}
                if path=='/users/me' and status==200:entry['own_brand']=body.get('tenant_name')==company['nombre']
                if path=='/productos' and status==200:
                    entry['product_ids']=[p['id'] for p in body] # Public IDs, no customer data.
                results.append(entry)
        if len(sessions)==2:
            a=next(r['product_ids'] for r in results if r['name']=='urbanstyle/productos')
            b=next(r['product_ids'] for r in results if r['name']=='modaexpress/productos')
            results.append({'name':'catalogs disjoint','pass':not(set(a)&set(b))})
            for slug,foreign in [('urbanstyle',b[0]),('modaexpress',a[0])]:
                status,_,_,_=call('GET','/productos/'+foreign,sessions[slug][0])
                results.append({'name':slug+' foreign product rejected','http':status,'pass':status==404})
        if args.providers and 'urbanstyle' in sessions:
            token,_,company=sessions['urbanstyle']
            for question,kind in [('Ventas por sucursal','canonical'),
                ('Calcula la suma de monto_total agrupada por metodo_pago de las ventas completadas','gemini_required')]:
                status,body,_,seconds=call('POST','/reportes-voz/query',token,{'pregunta':question,'limite':5})
                results.append({'name':'voice '+kind,'http':status,'seconds':seconds,
                    'rows':body.get('total_filas') if status==200 else None,
                    'error':body.get('detail') if status>=400 else None})
            status,body,_,seconds=call('POST','/chatbot/message',token,{
                'message':'Responde exactamente PRUEBA IA OK sin listar información de clientes.',
                'history':[],'tenant_slug':'urbanstyle'})
            results.append({'name':'chatbot live','http':status,'seconds':seconds,
                'origen':body.get('origen'), 'modelo':body.get('modelo'),
                'exact_test_reply':status==200 and 'PRUEBA IA OK' in body.get('reply',''),
                'note':'HTTP 200 alone does not prove a remote model; existing chatbot may use local fallback.'})
            status,config,_,_=call('GET','/payments/paypal/config?tenant_id='+company['id'])
            mode=config.get('mode') if isinstance(config,dict) else None
            results.append({'name':'paypal public config','http':status,'mode':mode})
            # Configuration can advertise sandbox even with invalid provider credentials.
            if status==200 and mode=='sandbox':
                _,products,_,_=call('GET','/productos',token)
                p=next(p for p in products if p.get('activo') and p.get('variantes'))
                v=next(v for v in p['variantes'] if v.get('activo'))
                query=urllib.parse.urlencode({'producto_id':p['id'],'talla':v['talla'],'color':v['color']})
                _,stock,_,_=call('GET','/inventario/disponibilidad?'+query,token)
                available=next((s for s in stock if s['stock_disponible']>0),None)
                if available:
                    # Availability already contains the server's effective variant price.
                    amount=float(available['precio'])
                    status,order,_,seconds=call('POST','/payments/paypal/create-order',token,{
                        'monto_bob':amount,'sucursal_id':available['sucursal_id'],
                        'detalles':[{'variante_id':v['id'],'cantidad':1}],
                        'request_id':str(uuid.uuid4()),'descripcion':'Prueba sandbox fase 8, sin captura',
                    })
                    results.append({'name':'paypal sandbox create only','http':status,'seconds':seconds,
                        'approval_link':bool(order.get('approve_url')) if status==201 else False,
                        'error':order.get('detail') if status>=400 else None,
                        'captured':False})
    finally:
        for token,refresh,_ in sessions.values():
            try:call('POST','/auth/logout',token,{'refresh_token':refresh})
            except Exception:pass
        Path(args.output).write_text(json.dumps({'results':results},indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps(results,ensure_ascii=True))


if __name__=='__main__':main()
