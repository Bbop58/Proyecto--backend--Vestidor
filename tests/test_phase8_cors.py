from fastapi.testclient import TestClient
from app.main import app

ORIGIN='https://proyecto-frontend-vestidor-ten.vercel.app'


def test_unhandled_failure_keeps_cors_and_sanitizes_response(monkeypatch):
    # Patch an existing public route, without adding audit-only routes to the API.
    route=next(r for r in app.routes if getattr(r,'path',None)=='/health')
    original=route.app
    async def broken(scope,receive,send):raise RuntimeError('private-db-url')
    monkeypatch.setattr(route,'app',broken)
    with TestClient(app,raise_server_exceptions=False) as client:
        response=client.get('/health',headers={'Origin':ORIGIN})
    assert response.status_code==500
    assert response.headers.get('access-control-allow-origin')==ORIGIN
    assert response.json()=={'detail':'Error interno del servidor'}


def test_untrusted_origin_is_not_given_browser_access():
    with TestClient(app) as client:
        denied=client.options('/api/v1/auth/login',headers={
            'Origin':'https://unrelated.invalid','Access-Control-Request-Method':'POST'})
        allowed=client.options('/api/v1/auth/login',headers={
            'Origin':ORIGIN,'Access-Control-Request-Method':'POST',
            'Access-Control-Request-Headers':'authorization,content-type'})
    assert denied.status_code==400
    assert 'access-control-allow-origin' not in denied.headers
    assert allowed.status_code==200
    assert allowed.headers['access-control-allow-origin']==ORIGIN


def test_default_cors_includes_the_actual_deployed_frontend():
    from app.config import Settings
    assert ORIGIN in Settings.model_fields['BACKEND_CORS_ORIGINS'].default
