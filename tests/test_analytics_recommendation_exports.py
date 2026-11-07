"""R2 full Excel export: same snapshot, no UI limit or executable catalogue values."""
from io import BytesIO
from openpyxl import load_workbook
from fastapi import HTTPException
import pytest
from tests.test_tenant_sales import shops
from tests.test_analytics_recommendations_branches import stock, history, new_variant
from tests.test_analytics_data import CUT
from app.services.analytics_branch_recommendations import read_recommendation_snapshot
from app.services.analytics_recommendation_export import export_recommendation_xlsx


def test_excel_keeps_all_rows_types_and_quality_without_formula_execution(db_session,shops):
    a=shops['a'];db=db_session
    for n in range(10):
        v=new_variant(db,a,n+100);stock(db,a[2][0],v,100);history(db,a,a[2][0],v,1,2)
    a[1].producto.nombre='=HYPERLINK("bad")';a[1].sku='+SKU';db.commit()
    data=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    content=export_recommendation_xlsx(data)
    wb=load_workbook(BytesIO(content))
    assert wb.sheetnames==['Resumen','Detalle completo','Calidad']
    sheet=wb['Detalle completo'];headers=[c.value for c in sheet[1]]
    assert sheet.max_row==len(data.casos)+1 and sheet.max_row>4
    assert sheet.freeze_panes=='A2' and sheet.auto_filter.ref
    values=list(sheet.iter_rows(min_row=2))
    assert {r[headers.index('SKU')].value for r in values}=={c.sku for c in data.casos}
    assert any(r[headers.index('Producto')].value.startswith('=') for r in values)
    assert all(c.data_type!='f' for row in sheet for c in row)
    assert all(r[headers.index('Ventas 4 semanas')].data_type=='n' for r in values)
    assert len(list(wb['Calidad'].iter_rows(min_row=2)))==sum(c.reponer is None for c in data.casos)


def test_excel_endpoint_requires_exact_fingerprint_and_ignores_foreign_tenant(client,shops):
    from app.main import app
    from app.api.deps import get_current_user
    app.dependency_overrides[get_current_user]=lambda:shops['a'][0]
    report=client.get('/api/v1/analytics/recommendations/report-data').json()
    url='/api/v1/analytics/recommendations/export.xlsx'
    response=client.get(url,params={'fingerprint':report['fingerprint'],'tenant_id':str(shops['b'][0].tenant_id)})
    assert response.status_code==200,response.text
    assert response.headers['content-type'].startswith('application/vnd.openxmlformats')
    assert response.headers['x-recommendation-fingerprint']==report['fingerprint']
    assert len(list(load_workbook(BytesIO(response.content))['Detalle completo'].iter_rows(min_row=2)))==len(report['casos'])
    assert client.get(url,params={'fingerprint':'0'*64}).status_code==409
    assert client.get(url).status_code==422


def test_export_never_truncates_large_snapshots(db_session,shops,monkeypatch):
    from app.services import analytics_recommendation_export as module
    data=read_recommendation_snapshot(db_session,shops['a'][0].tenant_id,CUT)
    monkeypatch.setattr(module,'MAX_EXPORT_ROWS',1)
    with pytest.raises(HTTPException) as e:export_recommendation_xlsx(data)
    assert e.value.status_code==413


@pytest.mark.parametrize('role_name,status',[('cliente',403),('encargado',200),('cajero',200)])
def test_excel_access_uses_commercial_report_permission(client,db_session,shops,role_name,status):
    from app.main import app
    from app.api.deps import get_current_user
    from app.models import Role
    a=shops['a'][0];a.role_id=db_session.query(Role).filter_by(name=role_name,tenant_id=None).one().id;db_session.commit()
    app.dependency_overrides[get_current_user]=lambda:a
    if status==200:fp=client.get('/api/v1/analytics/recommendations').json()['fingerprint']
    else:fp='0'*64
    assert client.get('/api/v1/analytics/recommendations/export.xlsx',params={'fingerprint':fp}).status_code==status
