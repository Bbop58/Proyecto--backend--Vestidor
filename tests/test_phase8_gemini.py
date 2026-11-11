"""Provider regressions without live keys, quota or nondeterministic latency."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.config import settings
from app.core.gemini_models import candidates, http_options
from app.services.voice_report_service import VoiceReportService
from app.services.report_intent_service import ReportIntentService
from app.services.chatbot_service import ChatbotService
from tests.test_tenant_sales import shops


def test_model_list_is_explicit_unique_and_bounded():
    assert candidates('gemini-3.5-flash-lite, gemini-3.5-flash-lite,gemini-3.8-flash,gemini-extra') == ['gemini-3.5-flash-lite','gemini-3.8-flash']
    assert candidates('https://attacker.invalid, bad model') == []
    options=http_options()
    assert options.timeout==10000
    assert options.retry_options.attempts==1


def test_defaults_try_the_live_validated_text_model_first(monkeypatch):
    from app.config import Settings
    calls=[]
    monkeypatch.setattr(settings,'VOICE_GEMINI_API_KEY','unit-test-only')
    for name in ('GEMINI_VOICE_MODELS','GEMINI_CHAT_MODELS'):
        configured=Settings.model_fields[name].default
        assert candidates(configured)==['gemini-3.6-flash','gemini-3.5-flash-lite']
        monkeypatch.setattr(settings,name,configured)
    def generate(**kwargs):
        calls.append(kwargs['model'])
        assert kwargs['config'].response_mime_type == 'application/json'
        assert kwargs['config'].response_schema.__name__ == 'ReportIntent'
        assert 'No escribas SQL' in kwargs['config'].system_instruction
        assert kwargs['contents'] == 'Cantidad de recibos emitidos'
        return SimpleNamespace(text='{"status":"ready","domain":"sales","metric":"sales_count","dimensions":[],"filters":[],"period":{"kind":"all_time"},"ranking":null,"detail":"product","clarification":null}')
    monkeypatch.setattr('app.services.report_intent_service.genai.Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    sql,_=VoiceReportService._generate_sql_with_gemini('Cantidad de recibos emitidos')
    assert 'COUNT(DISTINCT v.id)' in sql
    assert 'SELECT COUNT(*) FROM ventas' not in sql
    assert calls==['gemini-3.6-flash']


@pytest.mark.parametrize('expression,alias,expected,forbidden',[
    ('SUM(v.monto_total)','total_ventas','37.50 Bolivianos','unidades'),
    ('COUNT(v.id)','total_ventas','2 registros','Bolivianos'),
    ('SUM(d.cantidad)','cantidad','3 unidades','Bolivianos'),
    ('COUNT(v.id)','monto_total','2 registros','Bolivianos'),
])
def test_speech_uses_sql_metric_not_the_alias(expression,alias,expected,forbidden):
    amount=37.5 if expression.startswith('SUM(v.monto') else 3 if 'cantidad' in expression else 2
    summary=VoiceReportService._build_speech_summary('consulta','',
        ['metodo_pago',alias],[{'metodo_pago':'EFECTIVO',alias:amount}],
        sql=f'SELECT v.metodo_pago, {expression} AS {alias} FROM ventas v GROUP BY v.metodo_pago')
    assert expected in summary
    assert forbidden not in summary


def test_speech_handles_unquoted_sql_alias_folded_by_postgres():
    summary=VoiceReportService._build_speech_summary('consulta','',
        ['metodo_pago','total_ventas'],[{'metodo_pago':'EFECTIVO','total_ventas':37.5}],
        sql='SELECT v.metodo_pago, SUM(v.monto_total) AS TOTAL_VENTAS FROM ventas v GROUP BY v.metodo_pago')
    assert '37.50 Bolivianos' in summary
    assert 'unidades' not in summary


def test_voice_tries_available_configured_backup_after_overload(monkeypatch):
    monkeypatch.setattr(settings,'VOICE_GEMINI_API_KEY','unit-test-only')
    monkeypatch.setattr(settings,'GEMINI_VOICE_MODELS','gemini-3.5-flash-lite,gemini-3.8-flash')
    calls=[]
    def generate(**kw):
        calls.append(kw['model'])
        assert kw['config'].thinking_config.thinking_level.value.lower()=='low'
        if len(calls)==1:raise RuntimeError('503 simulated')
        return SimpleNamespace(text='{"status":"ready","domain":"sales","metric":"sales_count","dimensions":[],"filters":[],"period":{"kind":"all_time"},"ranking":null,"detail":"product","clarification":null}')
    monkeypatch.setattr('app.services.report_intent_service.genai.Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    sql,_=VoiceReportService._generate_sql_with_gemini('Cantidad de recibos emitidos')
    assert 'COUNT(DISTINCT v.id)' in sql
    assert calls==['gemini-3.5-flash-lite','gemini-3.8-flash']


def test_voice_provider_failure_remains_failure_not_another_report(monkeypatch):
    monkeypatch.setattr(settings,'VOICE_GEMINI_API_KEY','unit-test-only')
    def generate(**kw):raise TimeoutError('simulated private request')
    monkeypatch.setattr('app.services.report_intent_service.genai.Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    with pytest.raises(HTTPException) as error:
        VoiceReportService._generate_sql_with_gemini('Cantidad de recibos emitidos')
    assert error.value.status_code==503
    assert 'private' not in error.value.detail


@pytest.mark.parametrize('payload', [
    'not JSON',
    '{"status":"ready","domain":"sales","metric":"sales_count","sql":"SELECT * FROM users"}',
    '{"status":"clarify","domain":"none","metric":"none"}',
])
def test_report_intent_rejects_invalid_or_executable_model_output(monkeypatch,payload):
    monkeypatch.setattr(settings,'VOICE_GEMINI_API_KEY','unit-test-only')
    monkeypatch.setattr('app.services.report_intent_service.genai.Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_:SimpleNamespace(text=payload))))
    with pytest.raises(HTTPException) as error:
        ReportIntentService.interpret('cuánto vendimos')
    assert error.value.status_code==422


def test_report_intent_returns_clarification_without_sql(monkeypatch):
    monkeypatch.setattr(settings,'VOICE_GEMINI_API_KEY','unit-test-only')
    payload='{"status":"clarify","domain":"none","metric":"none","dimensions":[],"filters":[],"period":{"kind":"all_time"},"ranking":null,"detail":"product","clarification":"¿Quieres medir unidades o ingresos?"}'
    monkeypatch.setattr('app.services.report_intent_service.genai.Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_:SimpleNamespace(text=payload))))
    intent=ReportIntentService.interpret('muéstrame lo más importante')
    assert intent.status=='clarify'
    assert intent.clarification=='¿Quieres medir unidades o ingresos?'


def test_report_query_builder_binds_filters_and_ranks_inside_each_branch():
    from app.schemas.report_intent import ReportIntent
    from app.services.report_query_builder import ReportQueryBuilder
    intent=ReportIntent.model_validate({
        'status':'ready','domain':'sales','metric':'units_sold','dimensions':['branch','product'],
        'filters':[{'field':'branch','value':'Central; DROP TABLE users','match':'exact'}],
        'period':{'kind':'last_months','amount':3},
        'ranking':{'top_n':10,'direction':'desc','within':'branch'},'detail':'product'
    })
    sql,params,_=ReportQueryBuilder.build(intent)
    assert 'ROW_NUMBER() OVER (PARTITION BY grouped.sucursal' in sql
    assert 'posicion_ranking <= :top_n' in sql
    assert params['filter_0']=='Central; DROP TABLE users'
    assert params['top_n']==10
    assert 'DROP TABLE users' not in sql


def test_report_query_builder_rejects_revenue_join_that_would_duplicate_sale_totals():
    from app.schemas.report_intent import ReportIntent
    from app.services.report_query_builder import ReportQueryBuilder
    intent=ReportIntent.model_validate({
        'status':'ready','domain':'sales','metric':'gross_revenue','dimensions':['product'],
        'filters':[],'period':{'kind':'all_time'},'ranking':None,'detail':'product'
    })
    with pytest.raises(HTTPException) as error:
        ReportQueryBuilder.build(intent)
    assert error.value.status_code==422


@pytest.mark.parametrize('available',[True,False])
def test_chat_response_distinguishes_gemini_and_local(db_session,shops,monkeypatch,available):
    monkeypatch.setattr(ChatbotService,'get_api_key',classmethod(lambda cls:'unit-test-only'))
    def generate(**kw):
        if not available:raise TimeoutError('simulated')
        return SimpleNamespace(text='Respuesta de prueba')
    monkeypatch.setattr('app.services.chatbot_service.genai.Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    result=ChatbotService.process_message(db_session,'hola',[],current_user=shops['a'][0])
    assert result.origen==('gemini' if available else 'local')
    assert bool(result.modelo)==available
    if not available:assert result.reply.startswith('Respuesta local (IA no disponible')
