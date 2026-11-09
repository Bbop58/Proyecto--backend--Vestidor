"""Two-company REST/WebSocket acceptance against explicitly disposable demo data."""
import asyncio,json,os,sys,uuid
from pathlib import Path
from datetime import datetime,timedelta,timezone
import httpx
import websockets

BASE=os.environ.get('QA_NOTIFICATION_API','http://127.0.0.1:58013/api/v1')
if BASE not in {'http://127.0.0.1:58013/api/v1',
 'https://proyecto-backend-vestidor-production.up.railway.app/api/v1'}:raise RuntimeError('Unexpected QA API')
OUTPUT=Path(os.environ.get('QA_NOTIFICATION_OUTPUT',os.environ['TEMP']))/'fashionstore-notifications-fixtures.json'

async def main():
    fixtures=[];sessions=[]
    async with httpx.AsyncClient(timeout=30) as client:
        async def call(method,path,auth=None,**kwargs):
            response=await client.request(method,BASE+path,headers={'Authorization':'Bearer '+auth['access_token']} if auth else {},**kwargs)
            response.raise_for_status();return response.json()
        try:
            tenants=await call('GET','/subscriptions/public/tenants')
            for slug in ['urbanstyle','modaexpress']:
                tenant=next(t for t in tenants if t['slug']==slug)
                email=f'pushqa-{uuid.uuid4().hex[:12]}@example.com'
                customer=await call('POST','/auth/register',json={'tenant_id':tenant['id'],
                    'email':email,'full_name':'QA Notificaciones','password':'OnlyTest123!'})
                admin=await call('POST','/auth/login',json={'tenant_id':tenant['id'],
                    'email':f'admin@{slug}.com','password':'Password123!'})
                sessions.extend([customer,admin])
                branch=(await call('GET','/sucursales',admin))[0]['id']
                available=await call('GET','/inventario/disponibilidad',admin,params={'sucursal_id':branch})
                row=next(r for r in available if r['stock_disponible']>3)
                variants=await call('GET',f"/productos/{row['producto_id']}/variantes",admin)
                variant=next(v['id'] for v in variants if v['sku']==row['sku'])
                ws_url=BASE.replace('https:','wss:').replace('http:','ws:')+'/notifications/ws'
                async with websockets.connect(ws_url,origin='https://proyecto-frontend-vestidor-ten.vercel.app') as socket:
                    await socket.send(json.dumps({'token':customer['access_token']}))
                    initial=json.loads(await asyncio.wait_for(socket.recv(),10))
                    assert initial['type']=='notifications.changed' and initial['unread_count']==0
                    reservation=await call('POST','/reservas',customer,json={'sucursal_id':branch,
                        'fecha_hora_esperada':(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat(),
                        'items':[{'variante_id':variant,'cantidad':1}]})
                    created=json.loads(await asyncio.wait_for(socket.recv(),10))
                    assert created['unread_count']==1
                    await call('PATCH',f"/reservas/{reservation['id']}/preparar",admin)
                    ready=json.loads(await asyncio.wait_for(socket.recv(),10))
                    assert ready['unread_count']==2 and ready['revision']!=created['revision']
                    inbox=await call('GET','/notifications',customer)
                    assert len(inbox)==2 and any(n['type']=='RESERVATION_READY' for n in inbox)
                    await call('POST','/notifications/mark-all-read',customer,json={})
                    read=json.loads(await asyncio.wait_for(socket.recv(),10));assert read['unread_count']==0
                fixture={'slug':slug,'tenant_id':tenant['id'],'email':email,'user_id':customer['user']['id'],
                    'branch_id':branch,'variant_id':variant,'reservation_id':reservation['id'],
                    'reservation_code':reservation['codigo'],'websocket':'PASS'}
                fixtures.append(fixture);print(json.dumps({'company':slug,'websocket':'PASS','created':1,'ready':2,'read':0}))
            OUTPUT.write_text(json.dumps(fixtures,indent=2),encoding='utf-8')
        finally:
            for session in sessions:
                try:await call('POST','/auth/logout',session,json={'refresh_token':session['refresh_token']})
                except Exception:pass

if __name__=='__main__':asyncio.run(main())
