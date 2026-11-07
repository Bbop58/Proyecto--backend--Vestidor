"""D1: company facts, deterministic suggestions and read-only HTTP."""
from datetime import timedelta
import pytest
from app.services.analytics_recommendation_service import build_actions, render_action, AnalyticsRecommendationService
from tests.test_tenant_sales import shops, snapshot
from tests.test_analytics_data import receipt, CUT
from app.models import Inventory, SaleStatus, SaleDetail, Role


def fact(**changes):
    row = dict(variante_id='00000000-0000-0000-0000-000000000001', producto='Chaqueta', sku='QA', talla='M', color='Negro',
        vendidas4=12, vendidas4_anteriores=10, disponible=5, reservado=0, minimo_operativo=0,
        observados_dias=90, inventario_valido=True, oferta_vigente=False, sucursales=[])
    row.update(changes)
    return row


def test_replenishment_and_template_agree():
    action = build_actions([fact()])[0]
    assert (action.stock_objetivo, action.reponer, action.tipo) == (15,10,'REPONER')
    assert 'Reponer 10 unidades' in action.texto
    assert render_action(action) == action.texto


@pytest.mark.parametrize('changes,kind,target,quantity', [
    ({'minimo_operativo':20}, 'REPONER',20,15),
    ({'vendidas4':8,'disponible':10}, 'MANTENER',10,0),
    ({'vendidas4':2,'vendidas4_anteriores':3,'disponible':30},'EVALUAR_OFERTA',3,0),
    ({'vendidas4':0,'disponible':30},'EVALUAR_OFERTA',0,0),
    ({'observados_dias':3},'DATOS_INSUFICIENTES',None,None),
    ({'inventario_valido':False},'REVISAR_DATOS',None,None),
])
def test_policy_edges(changes,kind,target,quantity):
    from app.services.analytics_recommendation_service import make_action
    action=make_action(fact(**changes))
    assert (action.tipo,action.stock_objetivo,action.reponer)==(kind,target,quantity)


def test_unknown_and_current_offer_templates():
    from app.services.analytics_recommendation_service import make_action
    a=make_action(fact(vendidas4=2,disponible=30,oferta_vigente=True,talla=None,color=None,producto='Camisa ñ'))
    assert 'Revisar la oferta vigente' in a.texto and 'None' not in a.texto
    assert 'Camisa ñ' in a.texto
    assert 'mínimo operativo' in make_action(fact(minimo_operativo=20)).texto


def test_top_three_preserves_excess_and_is_deterministic():
    rows=[fact(sku=str(n),variante_id=f'00000000-0000-0000-0000-{n:012}',disponible=n) for n in range(1,5)]
    rows.append(fact(sku='EXCESS',vendidas4=2,disponible=30))
    result=build_actions(rows)
    assert len(result)==3 and result[-1].tipo=='EVALUAR_OFERTA'
    assert [a.sku for a in result[:2]]==['1','2']
    assert build_actions(list(reversed(rows)))==result
    assert build_actions([fact(observados_dias=0)])==[]


def inventory(db,shop,available,minimum=0):
    rows=db.query(Inventory).filter_by(variante_id=shop[1].id).order_by(Inventory.id).all()
    for i,row in enumerate(rows):
        row.stock_actual=available if i==0 else 0; row.stock_reservado=0; row.stock_minimo=minimum if i==0 else 0
    db.commit()
    return rows


def test_reader_uses_closed_weeks_reconciled_details_and_current_own_stock(db_session,shops):
    db=db_session; a=shops['a']; inventory(db,a,5)
    receipt(db,a,CUT-timedelta(days=80))
    sale=receipt(db,a,CUT-timedelta(days=8),amount=120,lines=12)
    receipt(db,a,CUT-timedelta(days=1),lines=20)
    receipt(db,a,CUT+timedelta(days=8),lines=20)
    receipt(db,a,CUT-timedelta(days=8),status=SaleStatus.CANCELADA)
    receipt(db,shops['b'],CUT-timedelta(days=8),lines=30)
    bad=receipt(db,a,CUT-timedelta(days=8));bad.monto_total=999;db.commit()
    before=snapshot(db)
    result=AnalyticsRecommendationService.read(db,a[0].tenant_id,CUT)
    action=result.acciones[0]
    assert (action.vendidas4,action.disponible,action.reponer)==(12,5,10)
    assert result.ventana_hasta.isoformat()=='2026-01-12'
    assert result.calidad.ventas_excluidas==1
    assert result.interpretacion.estado=='SIN_ANALISIS'
    assert snapshot(db)==before
    assert AnalyticsRecommendationService.read(db,a[0].tenant_id,CUT).fingerprint==result.fingerprint


def test_missing_inventory_inactive_branch_and_reserved_fingerprint(db_session,shops):
    db=db_session;a=shops['a']; rows=inventory(db,a,20)
    receipt(db,a,CUT-timedelta(days=80));receipt(db,a,CUT-timedelta(days=8),amount=120,lines=12)
    before=AnalyticsRecommendationService.read(db,a[0].tenant_id,CUT)
    rows[0].stock_reservado=15;db.commit()
    after=AnalyticsRecommendationService.read(db,a[0].tenant_id,CUT)
    assert before.fingerprint!=after.fingerprint and after.acciones[0].reponer==10
    for branch in a[2]:branch.activa=False
    db.commit()
    missing=AnalyticsRecommendationService.read(db,a[0].tenant_id,CUT)
    assert missing.acciones==[] and missing.calidad.inventario_inconsistente==1


def test_http_scope_no_training_no_effects(client,db_session,shops,monkeypatch):
    from app.main import app
    from app.api.deps import get_current_user
    from app.ml.ml_service import MLAnalyticsService
    monkeypatch.setattr(MLAnalyticsService,'train_all',lambda *args:pytest.fail('GET cannot train'))
    app.dependency_overrides[get_current_user]=lambda:shops['a'][0]
    before=snapshot(db_session)
    response=client.get('/api/v1/analytics/recommendations?tenant_id='+str(shops['b'][0].tenant_id))
    assert response.status_code==200,response.text
    assert response.json()['tenant_id']==str(shops['a'][0].tenant_id)
    assert snapshot(db_session)==before
    shops['a'][0].role_id=db_session.query(Role).filter_by(name='cliente').one().id;db_session.commit()
    assert client.get('/api/v1/analytics/recommendations').status_code==403


@pytest.mark.parametrize('method,label', [('RANDOM_FOREST','Random Forest'),('ULTIMA_SEMANA','método de referencia última semana'),('MEDIA_PREVIA','método de referencia media de tres semanas')])
def test_interpretation_uses_real_amount_and_method(method,label):
    from app.services.analytics_recommendation_service import build_interpretation
    value=build_interpretation(dict(estado='LISTO',fecha_corte=CUT.isoformat(),datos_cambiaron=False,
        resultado=dict(metodo=method,total_proyectado_bs=9326.92)))
    assert value.estado=='VIGENTE' and 'Bs 9.326,92' in value.texto and label in value.texto
    assert len(value.texto.split())<=45
    if method!='RANDOM_FOREST':assert 'Random Forest' not in value.texto


def test_stale_interpretation_never_announces_old_total():
    from app.services.analytics_recommendation_service import build_interpretation
    value=build_interpretation(dict(estado='LISTO',fecha_corte=CUT.isoformat(),datos_cambiaron=True,
        resultado=dict(metodo='RANDOM_FOREST',total_proyectado_bs=9326.92)))
    assert value.estado=='DESACTUALIZADO' and '9.326' not in value.texto and '2026-01-14' in value.texto


def test_growth_new_history_and_minimum_do_not_force_clearance():
    from app.services.analytics_recommendation_service import make_action
    assert make_action(fact(vendidas4=2,vendidas4_anteriores=1,disponible=30)).tipo=='MANTENER'
    assert make_action(fact(vendidas4=2,disponible=30,observados_dias=40)).tipo=='MANTENER'
    assert make_action(fact(observados_dias=2,minimo_operativo=20)).reponer is None
    action=make_action(fact(vendidas4=1,disponible=1,talla=None,color=None))
    assert action.reponer==1 and 'Reponer 1 unidad de' in action.texto and 'None' not in action.texto


def test_cross_scope_and_operator_are_rejected(db_session,shops):
    from fastapi import HTTPException
    db=db_session
    db.info['tenant_scope']=shops['a'][0].tenant_id
    try:
        with pytest.raises(HTTPException) as error:AnalyticsRecommendationService.read(db,shops['b'][0].tenant_id,CUT)
        assert error.value.status_code==403
    finally:db.info.pop('tenant_scope',None)
    db.info['platform_actor_id']=shops['a'][0].id
    try:
        with pytest.raises(HTTPException) as error:AnalyticsRecommendationService.read(db,shops['a'][0].tenant_id,CUT)
        assert error.value.status_code==403
    finally:db.info.pop('platform_actor_id',None)


def test_read_only_query_has_fixed_count_no_n_plus_one(db_session,shops):
    from sqlalchemy import event
    db=db_session; statements=[]
    engine=db.get_bind()
    def capture(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
    event.listen(engine,'before_cursor_execute',capture)
    try:
        AnalyticsRecommendationService.read(db,shops['a'][0].tenant_id,CUT)
        assert len(statements)<=13  # R1 adds one fixed branch-directory query; no per-row reads
        assert all(not s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','ALTER','CREATE')) for s in statements)
    finally:event.remove(engine,'before_cursor_execute',capture)


def test_invalid_forecast_does_not_hide_actions(db_session,shops,monkeypatch):
    from app.ml.ml_service import MLAnalyticsService
    monkeypatch.setattr(MLAnalyticsService,'get_demand_forecast',lambda *args:dict(estado='LISTO',resultado=dict(metodo='RANDOM_FOREST',total_proyectado_bs=float('nan'))))
    a=shops['a'];inventory(db_session,a,5)
    receipt(db_session,a,CUT-timedelta(days=80));receipt(db_session,a,CUT-timedelta(days=8),amount=120,lines=12)
    result=AnalyticsRecommendationService.read(db_session,a[0].tenant_id,CUT)
    assert result.acciones[0].reponer==10 and result.interpretacion.estado=='SIN_ANALISIS'


def test_other_tenant_inventory_cannot_replenish_own_stock(db_session,shops):
    db=db_session;a=shops['a'];rows=inventory(db,a,5)
    rows[0].tenant_id=shops['b'][0].tenant_id;db.commit()
    receipt(db,a,CUT-timedelta(days=80));receipt(db,a,CUT-timedelta(days=8),amount=120,lines=12)
    result=AnalyticsRecommendationService.read(db,a[0].tenant_id,CUT)
    assert result.acciones==[] and result.calidad.inventario_inconsistente==1
    assert result.calidad.detalles[0].disponible is None


@pytest.mark.parametrize('active_field',['variant','product'])
def test_inactive_catalog_excluded(db_session,shops,active_field):
    a=shops['a'];obj=a[1] if active_field=='variant' else a[1].producto
    obj.activo=False;db_session.commit()
    result=AnalyticsRecommendationService.read(db_session,a[0].tenant_id,CUT)
    assert result.acciones==[] and result.calidad.variantes_evaluadas==0


@pytest.mark.parametrize('role_name,expected',[('admin',200),('encargado',200),('cajero',200),('cliente',403)])
def test_http_reader_roles(client,db_session,shops,role_name,expected):
    from app.main import app
    from app.api.deps import get_current_user
    a=shops['a'][0];a.role_id=db_session.query(Role).filter_by(name=role_name,tenant_id=None).one().id
    db_session.commit();app.dependency_overrides[get_current_user]=lambda:a
    assert client.get('/api/v1/analytics/recommendations').status_code==expected


def test_closed_week_boundary_is_bolivian_not_utc(db_session,shops):
    from datetime import datetime,timezone
    db=db_session;a=shops['a'];inventory(db,a,5)
    receipt(db,a,CUT-timedelta(days=80));receipt(db,a,CUT-timedelta(days=8),amount=120,lines=12)
    before=AnalyticsRecommendationService.read(db,a[0].tenant_id,datetime(2026,1,12,3,59,tzinfo=timezone.utc))
    after=AnalyticsRecommendationService.read(db,a[0].tenant_id,datetime(2026,1,12,4,0,tzinfo=timezone.utc))
    assert before.ventana_hasta.isoformat()=='2026-01-05'
    assert after.ventana_hasta.isoformat()=='2026-01-12'
    assert before.acciones[0].vendidas4==0 and after.acciones[0].vendidas4==12


def test_native_forecast_feature_denies_before_reader(client,shops,monkeypatch):
    from app.main import app
    from app.api.deps import get_current_user
    from app.services.entitlement_service import EntitlementService
    from fastapi import HTTPException
    app.dependency_overrides[get_current_user]=lambda:shops['a'][0]
    def deny(*args):raise HTTPException(403,'Feature unavailable')
    monkeypatch.setattr(EntitlementService,'check',deny)
    monkeypatch.setattr(AnalyticsRecommendationService,'read',lambda *args:pytest.fail('Denied feature must not read'))
    assert client.get('/api/v1/analytics/recommendations').status_code==403
