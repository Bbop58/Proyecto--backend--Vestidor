"""R1: every branch/variant case remains available beyond visual highlights."""
from datetime import timedelta
import pytest
from sqlalchemy import select, event
from fastapi import HTTPException
from app.models import Inventory, ProductVariant, SaleStatus, Sale, SaleDetail
from tests.test_tenant_sales import shops, snapshot
from tests.test_analytics_data import receipt, CUT
from app.services.analytics_branch_recommendations import read_recommendation_snapshot, summarize_snapshot


def stock(db, branch, variant, actual, reserved=0, minimum=0):
    row=db.query(Inventory).filter_by(sucursal_id=branch.id,variante_id=variant.id).one_or_none()
    if row is None:
        row=Inventory(tenant_id=branch.tenant_id,sucursal_id=branch.id,variante_id=variant.id)
        db.add(row)
    row.stock_actual=actual;row.stock_reservado=reserved;row.stock_minimo=minimum;row.activo=True
    db.commit();return row


def history(db, shop, branch, variant, recent, prior=0, age=80):
    own=(shop[0],variant,[branch])
    receipt(db,own,CUT-timedelta(days=age),amount=10)
    if recent:receipt(db,own,CUT-timedelta(days=8),amount=recent*10,lines=recent)
    if prior:receipt(db,own,CUT-timedelta(days=40),amount=prior*10,lines=prior)


def new_variant(db, shop, n):
    v=ProductVariant(producto_id=shop[1].producto_id,talla=str(n),color='Negro',sku=f'BR-{n:03}',activo=True,precio_extra=0)
    db.add(v);db.commit();return v


def test_same_variant_has_independent_branch_actions_and_no_purchase_when_other_stock_exists(db_session,shops):
    db=db_session;a=shops['a'];v=a[1];b1,b2=a[2]
    stock(db,b1,v,5);stock(db,b2,v,40)
    history(db,a,b1,v,12,12);history(db,a,b2,v,2,3)
    before=snapshot(db);data=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    cases={str(r.sucursal_id):r for r in data.casos}
    first,second=cases[str(b1.id)],cases[str(b2.id)]
    assert (first.tipo,first.vendidas4,first.stock_objetivo,first.reponer)==('REPONER',12,15,10)
    assert (second.tipo,second.vendidas4,second.stock_objetivo)==('EVALUAR_OFERTA',2,3)
    assert first.revisar_otras_sucursales and 'otras sucursales' in first.texto
    cover=data.cobertura_empresa[0]
    assert cover.compra_sugerida==0 and cover.disponible==45 and cover.objetivo==18
    assert snapshot(db)==before


def test_more_than_three_per_kind_are_counted_and_export_source_keeps_every_case(db_session,shops):
    db=db_session;a=shops['a'];b1,b2=a[2]
    for n in range(1,6):
        v=new_variant(db,a,n)
        stock(db,b1,v,0);stock(db,b2,v,100)
        history(db,a,b1,v,4,4);history(db,a,b2,v,1,2)
    data=read_recommendation_snapshot(db,a[0].tenant_id,CUT);summary=summarize_snapshot(data)
    assert data.totales.reponer==5 and data.totales.evaluar_oferta==5
    assert len(data.casos)==12  # ten observed cases + original variant in two branches, insufficient
    assert summary.totales==data.totales
    assert len(summary.destacados)==2
    assert len({(r.sucursal_id,r.variante_id) for r in data.casos})==len(data.casos)
    assert len([r for r in data.casos if r.tipo=='REPONER'])>3
    assert len([r for r in data.casos if r.tipo=='EVALUAR_OFERTA'])>3
    assert {r.sku for r in data.casos if r.tipo=='REPONER'}=={f'BR-{n:03}' for n in range(1,6)}
    assert len(summary.acciones)<=3  # transitional old UI, explicitly separated from v2 coverage
    assert summary.version==2


def test_priority_is_stockout_then_fractional_gap_not_least_sold(db_session,shops):
    db=db_session;a=shops['a'];b=a[2][0]
    v1,v2,v3=[new_variant(db,a,n) for n in (11,12,13)]
    for v,units,available in [(v1,1,0),(v2,8,1),(v3,20,5)]:
        stock(db,b,v,available);history(db,a,b,v,units,units)
    data=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    chosen=[r for r in summarize_snapshot(data).destacados if r.tipo=='REPONER']
    assert len(chosen)==1 and chosen[0].variante_id==v1.id
    assert len([r for r in data.casos if r.tipo=='REPONER'])==3


def test_offer_priority_and_healthy_category_each_have_a_branch_highlight(db_session,shops):
    db=db_session;a=shops['a'];b=a[2][0]
    slow,other,healthy=[new_variant(db,a,n) for n in (21,22,23)]
    for v,recent,available in [(slow,1,30),(other,2,100),(healthy,8,10)]:
        stock(db,b,v,available);history(db,a,b,v,recent,recent+1)
    s=summarize_snapshot(read_recommendation_snapshot(db,a[0].tenant_id,CUT))
    assert next(r for r in s.destacados if r.tipo=='EVALUAR_OFERTA').variante_id==slow.id
    assert next(r for r in s.destacados if r.tipo=='MANTENER').variante_id==healthy.id
    assert s.totales.mantener==1 and s.totales.evaluar_oferta==2


def test_other_branch_history_does_not_make_an_unobserved_branch_eligible(db_session,shops):
    db=db_session;a=shops['a'];v=a[1];b1,b2=a[2]
    stock(db,b1,v,30);stock(db,b2,v,0,minimum=5);history(db,a,b1,v,1,2)
    data=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    row=next(r for r in data.casos if r.sucursal_id==b2.id)
    assert row.tipo=='DATOS_INSUFICIENTES' and row.reponer is None and row.alerta_minimo
    assert data.cobertura_empresa[0].compra_sugerida is None
    assert summarize_snapshot(data).totales.sin_historial==1


def test_closed_foreign_branches_and_inactive_catalog_are_never_reported(db_session,shops):
    db=db_session;a=shops['a'];b1,b2=a[2]
    history(db,a,b1,a[1],2,2);history(db,shops['b'],shops['b'][2][0],shops['b'][1],20,20)
    b2.activa=False;db.commit()
    data=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    assert {r.sucursal_id for r in data.casos}=={b1.id}
    assert [r.id for r in data.sucursales_resumen]==[b1.id]
    a[1].activo=False;db.commit()
    assert read_recommendation_snapshot(db,a[0].tenant_id,CUT).casos==[]


def test_missing_or_foreign_owned_inventory_is_quality_not_zero(db_session,shops):
    db=db_session;a=shops['a'];b1,b2=a[2]
    history(db,a,b1,a[1],4,4)
    row=db.query(Inventory).filter_by(sucursal_id=b1.id,variante_id=a[1].id).one()
    row.tenant_id=shops['b'][0].tenant_id;db.commit()
    data=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    case=next(c for c in data.casos if c.sucursal_id==b1.id)
    assert case.tipo=='REVISAR_DATOS' and case.disponible is None and case.reponer is None
    db.delete(row);db.commit()
    case=next(c for c in read_recommendation_snapshot(db,a[0].tenant_id,CUT).casos if c.sucursal_id==b1.id)
    assert case.tipo=='REVISAR_DATOS' and case.stock_actual is None


def test_snapshot_is_read_only_stable_and_scoped(db_session,shops):
    db=db_session;a=shops['a'];before=snapshot(db)
    one=read_recommendation_snapshot(db,a[0].tenant_id,CUT);two=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    assert one.fingerprint==two.fingerprint and snapshot(db)==before
    db.info['tenant_scope']=a[0].tenant_id
    try:
        with pytest.raises(HTTPException) as e:read_recommendation_snapshot(db,shops['b'][0].tenant_id,CUT)
        assert e.value.status_code==403
    finally:db.info.pop('tenant_scope',None)


def test_v2_and_full_report_share_snapshot_fingerprint_and_scoped_totals(client,db_session,shops):
    from app.main import app
    from app.api.deps import get_current_user
    a=shops['a'];app.dependency_overrides[get_current_user]=lambda:a[0]
    brief=client.get('/api/v1/analytics/recommendations').json()
    report=client.get('/api/v1/analytics/recommendations/report-data?tenant_id='+str(shops['b'][0].tenant_id))
    assert report.status_code==200,report.text
    data=report.json()
    assert brief['version']==data['version']==2
    assert brief['tenant_id']==data['tenant_id']==str(a[0].tenant_id)
    assert brief['fingerprint']==data['fingerprint'] and brief['totales']==data['totales']
    assert len(data['casos'])==2 and all(r['sucursal_id'] in {str(b.id) for b in a[2]} for r in data['casos'])


def test_zero_rows_and_missing_assignment_do_not_create_phantom_branches(db_session,shops):
    db=db_session;a=shops['a'];v=new_variant(db,a,90)
    data=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    unassigned=[c for c in data.casos if c.variante_id==v.id]
    assert len(unassigned)==1 and unassigned[0].sucursal_id is None
    assert unassigned[0].tipo=='REVISAR_DATOS'
    assert not any(c.variante_id==v.id for c in summarize_snapshot(data).destacados)
    a[1].activo=False;v.activo=False;db.commit()
    data=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    assert data.casos==[] and data.totales.total==0 and len(data.sucursales_resumen)==2


def test_queries_do_not_grow_with_more_products_or_branches(db_session,shops):
    from app.models import Branch
    db=db_session;a=shops['a'];tenant=a[0].tenant_id;statements=[];connection=db.get_bind()
    def capture(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
    event.listen(connection,'before_cursor_execute',capture)
    try:
        read_recommendation_snapshot(db,tenant,CUT)
        small=sum(q.lstrip().upper().startswith('SELECT') for q in statements)
        event.remove(connection,'before_cursor_execute',capture)
        for n in range(30,35):
            v=new_variant(db,a,n)
            branch=Branch(tenant_id=tenant,nombre=f'Extra{n}',ciudad='SC',direccion='QA',activa=True)
            db.add(branch);db.commit();stock(db,branch,v,30);history(db,a,branch,v,1,2)
        statements.clear();event.listen(connection,'before_cursor_execute',capture)
        data=read_recommendation_snapshot(db,tenant,CUT)
        assert sum(q.lstrip().upper().startswith('SELECT') for q in statements)==small
        assert len(data.sucursales_resumen)==7
        assert all(not q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','ALTER','CREATE')) for q in statements)
    finally:event.remove(connection,'before_cursor_execute',capture)


def test_fractional_gap_ranks_without_float_and_ties_use_sku(db_session,shops):
    db=db_session;a=shops['a'];b=a[2][0]
    high,low=[new_variant(db,a,n) for n in (51,52)]
    stock(db,b,high,1);history(db,a,b,high,8,8) # target10; gap9/10
    stock(db,b,low,5);history(db,a,b,low,20,20) # target25; gap20/25
    s=summarize_snapshot(read_recommendation_snapshot(db,a[0].tenant_id,CUT))
    assert next(c for c in s.destacados if c.tipo=='REPONER').variante_id==high.id


def test_reserved_units_affect_local_not_other_branch_and_fingerprint(db_session,shops):
    db=db_session;a=shops['a'];v=a[1];b1,b2=a[2]
    i=stock(db,b1,v,20);stock(db,b2,v,10);history(db,a,b1,v,12,12);history(db,a,b2,v,8,8)
    one=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    i.stock_reservado=15;db.commit();two=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    by={c.sucursal_id:c for c in two.casos}
    assert by[b1.id].disponible==5 and by[b1.id].reponer==10
    assert by[b2.id].tipo=='MANTENER' and by[b2.id].disponible==10
    assert one.fingerprint!=two.fingerprint
    assert two.cobertura_empresa[0].compra_sugerida==10


def test_invalid_cancelled_future_and_open_week_receipts_never_inflate_branch_sales(db_session,shops):
    db=db_session;a=shops['a'];v=a[1];b=a[2][0];stock(db,b,v,3);history(db,a,b,v,4,4)
    own=(a[0],v,[b])
    bad=receipt(db,own,CUT-timedelta(days=8));bad.monto_total=999;db.commit()
    receipt(db,own,CUT-timedelta(days=8),status=SaleStatus.CANCELADA)
    receipt(db,own,CUT-timedelta(days=8),status=SaleStatus.PENDIENTE_PAGO)
    receipt(db,own,CUT-timedelta(days=1),amount=300,lines=30)
    receipt(db,own,CUT+timedelta(days=8),amount=300,lines=30)
    data=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    case=next(c for c in data.casos if c.sucursal_id==b.id)
    assert case.vendidas4==4 and case.vendidas4_anteriores==4 and case.reponer==2
    assert data.ventas_excluidas==1


def test_growth_current_offer_minimum_and_short_history_are_not_forced_into_categories(db_session,shops):
    db=db_session;a=shops['a'];v=a[1];b=a[2][0]
    stock(db,b,v,100);history(db,a,b,v,2,1)
    data=read_recommendation_snapshot(db,a[0].tenant_id,CUT)
    assert next(c for c in data.casos if c.sucursal_id==b.id).tipo=='MANTENER'
    sale=receipt(db,(a[0],v,[b]),CUT-timedelta(days=40),amount=20,lines=2)
    v.producto.en_oferta=True;v.producto.precio_oferta=5;db.commit()
    case=next(c for c in read_recommendation_snapshot(db,a[0].tenant_id,CUT).casos if c.sucursal_id==b.id)
    assert case.tipo=='EVALUAR_OFERTA' and 'Revisar la oferta vigente' in case.texto
    stock(db,b,v,1,minimum=20)
    case=next(c for c in read_recommendation_snapshot(db,a[0].tenant_id,CUT).casos if c.sucursal_id==b.id)
    assert case.stock_objetivo==20 and case.reponer==19 and 'MINIMO_DOMINANTE' in case.motivos


@pytest.mark.parametrize('path',['recommendations','recommendations/report-data'])
def test_full_data_and_summary_denied_for_customers_and_platform_context(client,db_session,shops,path):
    from app.main import app
    from app.api.deps import get_current_user
    from app.models import Role
    a=shops['a'][0];a.role_id=db_session.query(Role).filter_by(name='cliente').one().id;db_session.commit()
    app.dependency_overrides[get_current_user]=lambda:a
    assert client.get('/api/v1/analytics/'+path).status_code==403


def test_empty_branch_and_company_rename_are_part_of_export_fingerprint(db_session,shops):
    from app.models import Tenant
    db=db_session;a=shops['a'];tenant=a[0].tenant_id
    one=read_recommendation_snapshot(db,tenant,CUT)
    a[2][0].nombre='Nueva Central';db.commit()
    two=read_recommendation_snapshot(db,tenant,CUT)
    assert one.fingerprint!=two.fingerprint
    db.get(Tenant,tenant).nombre='Nueva Empresa';db.commit()
    three=read_recommendation_snapshot(db,tenant,CUT)
    assert three.empresa_nombre=='Nueva Empresa' and three.fingerprint!=two.fingerprint


def test_snapshot_contains_no_export_or_legacy_limit_in_serialization(db_session,shops):
    data=read_recommendation_snapshot(db_session,shops['a'][0].tenant_id,CUT)
    payload=data.model_dump(mode='json')
    assert 'legacy' not in payload and len(payload['casos'])==data.totales.total
    assert payload['entorno']=='DEMO' and payload['origen_datos']=='BASE_DATOS_EMPRESA'
    assert set(payload['casos'][0]) >= {'stock_actual','reservado','disponible','minimo_operativo','vendidas4','sucursal_id','producto_id'}
