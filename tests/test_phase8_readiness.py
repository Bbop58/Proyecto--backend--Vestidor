from app.api.health import schema_compatible
from sqlalchemy import text


def test_readiness_detects_the_actual_missing_role_column(db_session):
    connection=db_session.connection()
    assert schema_compatible(connection)
    connection.execute(text('ALTER TABLE roles DROP COLUMN tenant_id CASCADE'))
    assert not schema_compatible(connection)
    # Fixture rollback restores the DDL as well as data; no committed schema edits.


def test_database_failure_returns_503_without_exposing_connection_string(client,monkeypatch):
    from app.api import health
    class Unavailable:
        def connect(self):raise RuntimeError('private-db-url-must-not-leak')
    monkeypatch.setattr(health,'engine',Unavailable())
    response=client.get('/health/ready')
    assert response.status_code==503
    assert response.json()=={'status':'not_ready','reason':'database_unavailable'}
