"""R1 read-only rehearsal against an ALREADY SEEDED disposable local QA database."""
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
import socket
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from sqlalchemy.engine import make_url
    url=make_url(os.environ.get('DATABASE_URL',''))
    if (url.get_backend_name()!='postgresql' or url.host not in {'127.0.0.1','localhost','::1'}
        or not (url.database or '').startswith('fashionstore_test_') or url.query):
        parser.error('Only explicit disposable local fashionstore_test_ PostgreSQL is allowed')
    from sqlalchemy import create_engine,select,text,event
    from sqlalchemy.orm import Session
    from fastapi.testclient import TestClient
    from app.database import Base,engine as api_engine
    from app.main import app
    from app.models import Tenant
    from app.seed_audit import reconcile
    from scripts.reset_demo_database import _snapshot
    engine=create_engine(url)
    with engine.connect() as conn:
        companies=conn.execute(select(Tenant.id,Tenant.slug).where(Tenant.es_operadora.is_(False))).all()
        checks=reconcile(conn)
        assert not any(checks.values())
    assert len(companies)==2
    transactions=[];external=[]
    def observe(conn,cursor,statement,parameters,context,executemany):
        if statement=='SET TRANSACTION READ ONLY':
            assert conn.get_isolation_level()=='REPEATABLE READ';transactions.append(True)
    connect=socket.socket.connect
    def local_only(sock,address):
        if address[0] not in {'127.0.0.1','localhost','::1'}:
            external.append(address[0]);raise RuntimeError('No external calls in R1 rehearsal')
        return connect(sock,address)
    event.listen(api_engine,'before_cursor_execute',observe)
    report={'source':'Already seeded local PostgreSQL; no fresh or new purchases',
        'seed_checks':len(checks),'companies':[]}
    with patch('socket.socket.connect',local_only),TestClient(app) as client:
        for tenant,slug in companies:
            login=client.post('/api/v1/auth/login',json={'email':'admin@'+slug+'.com','password':'Password123!','tenant_id':str(tenant)})
            assert login.status_code==200;account=login.json();headers={'Authorization':'Bearer '+account['access_token']}
            try:
                with engine.connect() as conn:before=_snapshot(conn,Base.metadata)
                brief=client.get('/api/v1/analytics/recommendations',headers=headers)
                full=client.get('/api/v1/analytics/recommendations/report-data',headers=headers)
                repeated=client.get('/api/v1/analytics/recommendations/report-data',headers=headers)
                assert brief.status_code==full.status_code==repeated.status_code==200
                summary,data=brief.json(),full.json()
                assert summary['version']==data['version']==2 and data['tenant_id']==str(tenant)
                assert data['fingerprint']==summary['fingerprint']==repeated.json()['fingerprint']
                assert 'legacy' not in data and summary['totales']==data['totales']
                assert len(data['casos'])==data['totales']['total']
                assert len(data['casos'])==len({(c['sucursal_id'],c['variante_id']) for c in data['casos']})
                assert len(summary['destacados'])==len({(c['sucursal_id'],c['tipo']) for c in summary['destacados']})
                start=data['ventana_desde']+'T00:00:00-04:00';end=data['ventana_hasta']+'T00:00:00-04:00'
                with engine.connect() as conn:
                    history=conn.execute(text("""SELECT v.sucursal_id,d.variante_id,
                        sum(CASE WHEN v.created_at>=:start THEN d.cantidad ELSE 0 END) recent,
                        sum(CASE WHEN v.created_at>=CAST(:start AS timestamptz)-interval '28 days' AND v.created_at<:start THEN d.cantidad ELSE 0 END) prior,
                        min(v.created_at) first_sale
                        FROM ventas v JOIN detalles_venta d ON d.venta_id=v.id
                        WHERE v.tenant_id=:tenant AND v.estado='COMPLETADA' AND v.created_at<:end
                        GROUP BY v.sucursal_id,d.variante_id"""),dict(tenant=tenant,start=start,end=end)).mappings().all()
                    history={(str(r['sucursal_id']),str(r['variante_id'])):r for r in history}
                    stocks=conn.execute(text("""SELECT i.sucursal_id,i.variante_id,i.stock_actual,i.stock_reservado,i.stock_minimo
                        FROM inventario i JOIN sucursales b ON b.id=i.sucursal_id
                        JOIN variantes_producto v ON v.id=i.variante_id JOIN productos p ON p.id=v.producto_id
                        WHERE i.tenant_id=:tenant AND b.tenant_id=:tenant AND p.tenant_id=:tenant
                        AND i.activo AND b.activa AND p.activo AND v.activo"""),dict(tenant=tenant)).mappings().all()
                    stocks={(str(r['sucursal_id']),str(r['variante_id'])):r for r in stocks}
                    assert set(stocks)<={(c['sucursal_id'],c['variante_id']) for c in data['casos']}
                    branch_ids={str(i) for i in conn.scalars(text('SELECT id FROM sucursales WHERE tenant_id=:tenant AND activa'),dict(tenant=tenant))}
                    verified=0
                    for case in data['casos']:
                        key=(case['sucursal_id'],case['variante_id']);stock=stocks.get(key);hist=history.get(key)
                        assert case['sucursal_id'] is None or case['sucursal_id'] in branch_ids
                        assert case['vendidas4']==(int(hist['recent']) if hist else 0)
                        assert case['vendidas4_anteriores']==(int(hist['prior']) if hist else 0)
                        if stock:
                            assert case['stock_actual']==stock['stock_actual']
                            assert case['disponible']==stock['stock_actual']-stock['stock_reservado']
                            assert case['minimo_operativo']==stock['stock_minimo']
                        if case['stock_objetivo'] is not None:
                            expected=max((case['vendidas4']*5+3)//4,stock['stock_minimo'])
                            assert case['stock_objetivo']==expected and case['reponer']==max(0,expected-case['disponible'])
                        verified+=1
                    after=_snapshot(conn,Base.metadata)
                    assert before==after
                report['companies'].append(dict(slug=slug,totals=data['totales'],branches=data['sucursales_resumen'],
                    highlights=len(summary['destacados']),verified_cases=verified,independent_sql=True,
                    all_tables_unchanged_by_get=True,interpretation=data['interpretacion'],summary=data['resumen_operativo'],fingerprint=data['fingerprint']))
            finally:
                assert client.post('/api/v1/auth/logout',headers=headers,json={'refresh_token':account['refresh_token']}).status_code==200
                assert client.get('/api/v1/users/me',headers=headers).status_code==401
    event.remove(api_engine,'before_cursor_execute',observe)
    assert len(transactions)==6 and not external
    report.update(repeatable_read_only_transactions=len(transactions),external_attempts=0,qa_sessions_revoked=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    engine.dispose();api_engine.dispose()
    print('PASS: both companies, ALL branch cases checked with independent SQL; no writes by GET, no external calls')


if __name__=='__main__':main()
