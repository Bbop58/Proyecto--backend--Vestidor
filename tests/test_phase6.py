"""Phase 6: known data, explicit scope and failures, never live provider keys."""
import pytest
from fastapi import HTTPException
from tests.test_tenant_sales import shops
from app.services.chatbot_service import ChatbotService
from app.services.voice_report_service import VoiceReportService
from app.services.custom_report_service import CustomReportService
from app.services.recommendation_service import RecommendationService
from app.models.inventory import Inventory


@pytest.fixture(autouse=True)
def no_live_ai(monkeypatch):
    from app.config import settings
    for key in ('GEMINI_API_KEY','VOICE_GEMINI_API_KEY','CHATBOT_GEMINI_API_KEY'):
        monkeypatch.setenv(key,'')
        if hasattr(settings,key): monkeypatch.setattr(settings,key,'')


@pytest.mark.parametrize('slug',[None,'unknown','a-fragment'])
def test_chatbot_requires_exact_explicit_company(db_session,shops,slug):
    with pytest.raises(HTTPException) as error:
        ChatbotService.process_message(db_session,'hola',[],tenant_slug=slug)
    assert error.value.status_code in (400,404)


def test_unknown_voice_question_does_not_substitute_another_report():
    with pytest.raises(HTTPException) as error:
        VoiceReportService._generate_sql_with_gemini('¿Qué margen obtuve por proveedor?',100)
    assert error.value.status_code==503


@pytest.mark.parametrize('entity,columns,filters',[
    ('unknown',[],{}),('ventas',['unknown'],{}),
    ('ventas',[],{'fecha_desde':'bad-date'}),('ventas',[],{'sucursal_id':'bad-uuid'}),
    ('ventas',[],{'monto_min':'NaN'})])
def test_custom_report_invalid_input_is_not_silently_ignored(db_session,shops,entity,columns,filters):
    with pytest.raises(HTTPException) as error:
        CustomReportService.execute_custom_report(db_session,entity,columns,filters,tenant_id=shops['a'][0].tenant_id)
    assert error.value.status_code==422


def test_recommendation_available_stock_excludes_reserved_and_closed_branches(db_session,shops):
    user,variant,branches=shops['a']
    row=db_session.query(Inventory).filter_by(sucursal_id=branches[0].id).one()
    row.stock_reservado=7
    branches[1].activa=False
    db_session.commit()
    assert RecommendationService._get_product_stock(db_session,variant.producto_id,user.tenant_id)==3


@pytest.mark.parametrize('expired,expected',[(True,13),(False,8)])
def test_recommendation_quotes_current_offer_and_variant_extra(db_session,shops,expired,expected):
    from datetime import datetime,timedelta,timezone
    user,variant,_=shops['a']
    variant.precio_extra=3
    product=variant.producto
    product.en_oferta=True
    product.precio_oferta=5
    product.fecha_fin_oferta=datetime.now(timezone.utc)+timedelta(days=-1 if expired else 1)
    db_session.commit()
    item=RecommendationService.get_trending_products(db_session,user.tenant_id)[0]
    assert item.precio_base==10
    assert item.precio_desde==expected


def test_reservation_report_uses_persisted_domain_fields(db_session,shops):
    from datetime import datetime,timedelta,timezone
    from app.models.reservation import Reservation
    user,_,branches=shops['a']
    now=datetime.now(timezone.utc)
    db_session.add(Reservation(tenant_id=user.tenant_id,codigo='KNOWN-R',cliente_id=user.id,
        sucursal_id=branches[0].id,fecha_hora_esperada=now+timedelta(hours=1),fecha_expiracion=now+timedelta(hours=24),total_estimado=37))
    db_session.commit()
    result=CustomReportService.execute_custom_report(db_session,'reservas',['codigo_reserva','cliente_nombre','monto_total'],{},sort_by='fecha_creacion',tenant_id=user.tenant_id)
    assert result['rows'][0]['monto_total']==37


def test_chatbot_role_name_does_not_grant_financial_reports(db_session,shops,monkeypatch):
    from app.models.role import Role
    user=shops['a'][0]
    role=Role(name='administrador sin permisos',tenant_id=user.tenant_id)
    db_session.add(role);db_session.flush();user.role=role;db_session.commit()
    monkeypatch.setattr('app.services.chatbot_service.SaleService.get_summary_report',lambda *a,**kw:pytest.fail('No financial permission'))
    result=ChatbotService.process_message(db_session,'total de ventas',[],current_user=user)
    assert 'Facturación Total Acumulada' not in result.reply


def test_voice_summary_does_not_read_transaction_count_as_money():
    summary=VoiceReportService._build_speech_summary('ventas por sucursal','',
        ['sucursal','total_ventas','total_recaudado_bs'],
        [{'sucursal':'Central','total_ventas':2,'total_recaudado_bs':37}])
    assert '37' in summary


def test_excel_cells_from_data_remain_text_not_formulas():
    import io,openpyxl
    content=VoiceReportService.generate_excel_report('test','test',['cliente'],[{'cliente':'=1+2'}])
    cell=openpyxl.load_workbook(io.BytesIO(content)).active['A7']
    assert cell.data_type=='s'


def test_tryon_rejects_non_image_before_download_or_provider(db_session,shops,monkeypatch):
    from app.schemas.ai import VirtualTryOnRequest
    from app.services.ai_tryon_service import AITryOnService
    monkeypatch.setattr(AITryOnService,'_tryon_with_vton_fallback',lambda **kw:pytest.fail('Must reject before provider'))
    with pytest.raises(HTTPException) as error:
        AITryOnService.process_virtual_tryon(db_session,VirtualTryOnRequest(producto_id=shops['a'][1].producto_id,imagen_cliente_base64='bm90IGFuIGltYWdl'))
    assert error.value.status_code==400


@pytest.mark.parametrize('sql',["SELECT firma_balance FROM user_wallets", "SELECT * FROM payment_intents", "SELECT pg_sleep(10)","SELECT hashed_password FROM users"])
def test_voice_rejects_nonreport_tables_and_private_columns(db_session,shops,sql):
    from app.core.tenant_sql import scope_report_sql
    with pytest.raises(HTTPException):
        scope_report_sql(sql,db_session,shops['a'][0].tenant_id)


def test_voice_known_aggregates_scope_limit_and_counts(db_session,shops,monkeypatch):
    from app.core.tenant_scope import bind_tenant
    from app.services.sale_service import SaleService
    from tests.test_commercial_invariants import sale_data
    a,va,ba=shops['a'];b,vb,bb=shops['b']
    SaleService.create_presencial_sale(db_session,sale_data(ba[0],va),a.id)
    SaleService.create_presencial_sale(db_session,sale_data(bb[0],vb),b.id)
    bind_tenant(db_session,a.tenant_id,a.id)
    result=VoiceReportService.execute_voice_report(db_session,'Ventas totales agrupadas por sucursal',limit=1)
    assert len(result.filas)==1
    assert result.filas[0]['total_recaudado_bs']==10
    assert result.filas[0]['sucursal']=='a0'


@pytest.mark.parametrize('response', ['timeout','invalid','empty'])
def test_voice_provider_errors_never_replace_question(monkeypatch,response):
    from types import SimpleNamespace
    from app.config import settings
    monkeypatch.setattr(settings,'VOICE_GEMINI_API_KEY','test-only')
    def generate(**kwargs):
        if response=='timeout': raise TimeoutError('test provider')
        return SimpleNamespace(text='not JSON' if response=='invalid' else '')
    monkeypatch.setattr('app.services.voice_report_service.genai.Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    with pytest.raises(HTTPException) as error:
        VoiceReportService._generate_sql_with_gemini('Margen por proveedor')
    assert error.value.status_code in (502,503)


def test_tryon_reference_fallback_is_explicit_and_temp_files_removed(db_session,shops,monkeypatch,tmp_path):
    import base64,io
    from PIL import Image
    from app.services.ai_tryon_service import AITryOnService
    from app.schemas.ai import VirtualTryOnRequest
    user,variant,_=shops['a']
    image=io.BytesIO();Image.new('RGB',(20,30),'blue').save(image,format='PNG')
    raw=image.getvalue()
    monkeypatch.setattr('app.services.ai_tryon_service.httpx.get',lambda *a,**kw: type('Response',(),{'status_code':200,'content':raw})())
    variant.producto.imagen_url='https://public.example/garment.png';db_session.commit()
    monkeypatch.setattr(AITryOnService,'_tryon_with_vton_fallback',lambda **kw:(_ for _ in ()).throw(TimeoutError('fake provider')))
    result=AITryOnService.process_virtual_tryon(db_session,VirtualTryOnRequest(producto_id=variant.producto_id,imagen_cliente_base64=base64.b64encode(raw).decode()))
    assert result.es_referencial and result.motor=='referencial'
    assert 'referencial' in result.mensaje
    Image.open(io.BytesIO(base64.b64decode(result.imagen_resultado_base64.split(',')[1]))).verify()


def test_tryon_vton_timeout_cancels_job_and_removes_input_files(monkeypatch):
    from app.services.ai_tryon_service import AITryOnService
    from types import SimpleNamespace
    from pathlib import Path
    import tempfile
    seen=[];files=[]
    original=tempfile.NamedTemporaryFile
    def temp(**kwargs):
        f=original(**kwargs);files.append(f.name);return f
    def timeout(**kw):
        assert kw['timeout']==25
        raise TimeoutError('fake provider')
    monkeypatch.setattr('app.services.ai_tryon_service.tempfile.NamedTemporaryFile',temp)
    monkeypatch.setattr('app.services.ai_tryon_service.Client',lambda *a,**kw:SimpleNamespace(submit=lambda **kwargs:SimpleNamespace(result=timeout,cancel=lambda:seen.append('cancelled'))))
    monkeypatch.setattr('app.services.ai_tryon_service.handle_file',lambda x:x)
    with pytest.raises(TimeoutError):
        AITryOnService._tryon_with_vton_fallback(b'a',b'b','garment')
    assert seen==['cancelled']
    assert all(not Path(f).exists() for f in files)


def test_custom_report_date_until_includes_day_and_reservation_filter(db_session,shops):
    from datetime import datetime,timedelta,timezone
    from app.models.reservation import Reservation
    user,_,branches=shops['a'];now=datetime(2026,10,12,18,tzinfo=timezone.utc)
    db_session.add(Reservation(tenant_id=user.tenant_id,codigo='DATE-KNOWN',cliente_id=user.id,
        sucursal_id=branches[0].id,fecha_hora_esperada=now+timedelta(hours=1),fecha_expiracion=now+timedelta(hours=24),created_at=now))
    db_session.commit()
    result=CustomReportService.execute_custom_report(db_session,'reservas',['codigo_reserva'],{'fecha_desde':'2026-10-12','fecha_hasta':'2026-10-12'},sort_by='fecha_creacion',tenant_id=user.tenant_id)
    assert result['total_rows']==1
    result=CustomReportService.execute_custom_report(db_session,'reservas',['codigo_reserva'],{'fecha_desde':'2026-10-13'},sort_by='fecha_creacion',tenant_id=user.tenant_id)
    assert result['total_rows']==0


def test_voice_canonical_period_is_not_all_time(db_session,shops):
    from app.core.tenant_scope import bind_tenant
    from app.services.sale_service import SaleService
    from tests.test_commercial_invariants import sale_data
    from datetime import datetime,timezone
    from app.models.sale import Sale
    user,variant,branches=shops['a']
    result=SaleService.create_presencial_sale(db_session,sale_data(branches[0],variant),user.id)
    sale=db_session.get(Sale,result['id']);sale.created_at=datetime(2024,1,5,tzinfo=timezone.utc);db_session.commit()
    bind_tenant(db_session,user.tenant_id,user.id)
    report=VoiceReportService.execute_voice_report(db_session,'Ventas por sucursal de este mes')
    assert sum(float(row['total_recaudado_bs']) for row in report.filas)==0


@pytest.mark.parametrize('action',['apply','dismiss','auto-apply-all'])
def test_report_read_permission_cannot_change_prices(client,db_session,shops,action):
    from app.models.role import Role
    from app.models.permission import Permission
    from app.main import app
    from app.api.deps import get_current_user
    user,variant,_=shops['a']
    role=Role(name='report-reader',tenant_id=user.tenant_id,permissions=[db_session.query(Permission).filter_by(code='ventas.reports').one()])
    db_session.add(role);user.role=role;db_session.commit()
    app.dependency_overrides[get_current_user]=lambda:user
    path='/api/v1/analytics/markdown-recommendations/'+(action if action=='auto-apply-all' else f'{variant.producto_id}/{action}')
    response=client.post(path,json={'descuento_pct':20})
    assert response.status_code==403,response.text
    db_session.refresh(variant.producto);assert not variant.producto.en_oferta


def test_anonymous_tryon_cannot_select_private_product(client,shops):
    response=client.post('/api/v1/ai/virtual-tryon',json={'producto_id':str(shops['b'][1].producto_id),'imagen_cliente_base64':'bm90aW1hZ2U='})
    assert response.status_code in (401,403)


def test_retired_auto_markdown_cannot_change_prices(client,db_session,shops):
    from app.main import app
    from app.api.deps import get_current_user
    user,variant,_=shops['a'];app.dependency_overrides[get_current_user]=lambda:user
    original=(variant.producto.precio_base,variant.producto.precio_oferta,variant.producto.en_oferta)
    response=client.post('/api/v1/analytics/markdown-recommendations/auto-apply-all')
    assert response.status_code==410
    db_session.refresh(variant.producto)
    assert (variant.producto.precio_base,variant.producto.precio_oferta,variant.producto.en_oferta)==original
