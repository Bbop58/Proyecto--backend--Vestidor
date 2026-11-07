"""R2: export real files from an existing, explicitly local disposable QA DB."""
import os,sys,json,hashlib,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 from sqlalchemy.engine import make_url
 url=make_url(os.environ.get('DATABASE_URL',''))
 if url.get_backend_name()!='postgresql' or url.host not in {'127.0.0.1','localhost','::1'} or not (url.database or '').startswith('fashionstore_test_') or url.query:p.error('Only a disposable local QA database is allowed')
 from sqlalchemy import create_engine,select
 from app.models import Tenant
 from app.database import Base
 from app.main import app
 from scripts.reset_demo_database import _snapshot
 from fastapi.testclient import TestClient
 from openpyxl import load_workbook
 from io import BytesIO
 engine=create_engine(url);args.output.mkdir(parents=True,exist_ok=True);results=[]
 with engine.connect() as conn:companies=conn.execute(select(Tenant.id,Tenant.slug).where(Tenant.es_operadora.is_(False))).all()
 with TestClient(app) as client:
  for tenant,slug in companies:
   login=client.post('/api/v1/auth/login',json={'email':'admin@'+slug+'.com','password':'Password123!','tenant_id':str(tenant)})
   assert login.status_code==200;account=login.json();headers={'Authorization':'Bearer '+account['access_token']}
   try:
    with engine.connect() as conn:before=_snapshot(conn,Base.metadata)
    summary=client.get('/api/v1/analytics/recommendations',headers=headers);full=client.get('/api/v1/analytics/recommendations/report-data',headers=headers)
    assert summary.status_code==full.status_code==200
    data=full.json();xlsx=client.get('/api/v1/analytics/recommendations/export.xlsx',params={'fingerprint':data['fingerprint']},headers=headers)
    assert xlsx.status_code==200
    (args.output/(slug+'-summary.json')).write_text(json.dumps(summary.json(),ensure_ascii=False),encoding='utf-8')
    (args.output/(slug+'-snapshot.json')).write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    (args.output/(slug+'.xlsx')).write_bytes(xlsx.content)
    book=load_workbook(BytesIO(xlsx.content));sheet=book['Detalle completo'];keys={(str(r[20].value),r[21].value) for r in list(sheet)[1:]}
    assert keys=={(str(c['sucursal_id']) if c['sucursal_id'] else 'None',c['variante_id']) for c in data['casos']}
    assert sheet.max_row-1==len(data['casos'])>3
    assert all(c.data_type!='f' for ws in book for row in ws for c in row)
    with engine.connect() as conn:assert before==_snapshot(conn,Base.metadata)
    results.append(dict(slug=slug,cases=len(data['casos']),xlsx_rows=sheet.max_row-1,quality_rows=book['Calidad'].max_row-1,all_ids_match=True,no_get_writes=True,xlsx_sha256=hashlib.sha256(xlsx.content).hexdigest()))
   finally:
    assert client.post('/api/v1/auth/logout',headers=headers,json={'refresh_token':account['refresh_token']}).status_code==200
    assert client.get('/api/v1/users/me',headers=headers).status_code==401
 (args.output/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8');engine.dispose()
 print('PASS: complete real XLSX, all branch IDs, numeric/literal cells and zero GET changes for both companies')
if __name__=='__main__':main()
