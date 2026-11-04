"""Provider regressions without live keys, quota or nondeterministic latency."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.config import settings
from app.core.gemini_models import candidates, http_options
from app.services.voice_report_service import VoiceReportService
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
        return SimpleNamespace(text='{"sql":"SELECT COUNT(*) FROM ventas","interpretation":"Conteo"}')
    monkeypatch.setattr('app.services.voice_report_service.genai.Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    sql,_=VoiceReportService._generate_sql_with_gemini('Cantidad de recibos emitidos')
    assert sql=='SELECT COUNT(*) FROM ventas'
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
        return SimpleNamespace(text='{"sql":"SELECT COUNT(*) FROM ventas","interpretation":"Conteo"}')
    monkeypatch.setattr('app.services.voice_report_service.genai.Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    sql,_=VoiceReportService._generate_sql_with_gemini('Cantidad de recibos emitidos')
    assert sql=='SELECT COUNT(*) FROM ventas'
    assert calls==['gemini-3.5-flash-lite','gemini-3.8-flash']


def test_voice_provider_failure_remains_failure_not_another_report(monkeypatch):
    monkeypatch.setattr(settings,'VOICE_GEMINI_API_KEY','unit-test-only')
    def generate(**kw):raise TimeoutError('simulated private request')
    monkeypatch.setattr('app.services.voice_report_service.genai.Client',lambda **kw:SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    with pytest.raises(HTTPException) as error:
        VoiceReportService._generate_sql_with_gemini('Cantidad de recibos emitidos')
    assert error.value.status_code==503
    assert 'private' not in error.value.detail


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
