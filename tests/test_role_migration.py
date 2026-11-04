"""Legacy-schema regression for Railway's missing roles.tenant_id; isolated schema."""
import os
import uuid

import psycopg2
from psycopg2 import sql
import pytest


@pytest.fixture
def legacy_roles():
    connection = psycopg2.connect(os.environ['TEST_DATABASE_URL'])
    schema = 'login_migration_' + uuid.uuid4().hex
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            cursor.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            cursor.execute('''
                CREATE TABLE tenants (id UUID PRIMARY KEY);
                CREATE TABLE roles (id UUID PRIMARY KEY, name TEXT NOT NULL UNIQUE);
                CREATE INDEX ix_roles_name ON roles(name);
                CREATE TABLE users (id UUID PRIMARY KEY, role_id UUID REFERENCES roles(id),
                    tenant_id UUID REFERENCES tenants(id), email TEXT, hashed_password TEXT);
            ''')
            company, admin, custom, user = [str(uuid.uuid4()) for _ in range(4)]
            cursor.execute('INSERT INTO tenants VALUES (%s)', (company,))
            cursor.execute('INSERT INTO roles VALUES (%s,%s),(%s,%s)', (admin,'admin',custom,'designer'))
            cursor.execute('INSERT INTO users VALUES (%s,%s,%s,%s,%s)',
                           (user,custom,company,'qa@example.com','unchanged-hash'))
    try:
        yield connection, company, admin, custom
    finally:
        connection.rollback()
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        connection.close()


def test_legacy_role_lookup_fails_before_migration(legacy_roles):
    connection, *_ = legacy_roles
    with pytest.raises(psycopg2.errors.UndefinedColumn):
        with connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT tenant_id FROM roles')


def test_migration_is_repeatable_and_preserves_accounts(legacy_roles):
    from scripts.migrate_role_tenants import migrate_connection
    connection, company, admin, custom = legacy_roles
    with connection.cursor() as cursor:
        cursor.execute('SELECT * FROM users ORDER BY id')
        before = cursor.fetchall()
    connection.commit()
    for _ in range(2):
        migrate_connection(connection)
    with connection.cursor() as cursor:
        cursor.execute('SELECT * FROM users ORDER BY id')
        assert cursor.fetchall() == before
        cursor.execute('SELECT tenant_id FROM roles WHERE id=%s', (custom,))
        assert str(cursor.fetchone()[0]) == company
        cursor.execute('SELECT tenant_id FROM roles WHERE id=%s', (admin,))
        assert cursor.fetchone()[0] is None
        # The same role name is now allowed in another company, not globally duplicated.
        cursor.execute('INSERT INTO roles(id,name,tenant_id) VALUES (%s,%s,%s)',
                       (str(uuid.uuid4()),'admin',company))


def test_failed_migration_rolls_back_schema_changes(legacy_roles):
    from scripts.migrate_role_tenants import migrate_connection
    connection, *_ = legacy_roles
    with connection:
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE roles DROP CONSTRAINT roles_name_key')
            cursor.execute('INSERT INTO roles VALUES (%s,%s)', (str(uuid.uuid4()),'admin'))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        migrate_connection(connection)
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='roles' AND column_name='tenant_id'")
        assert cursor.fetchone()[0] == 0
        cursor.execute('SELECT count(*) FROM roles')
        assert cursor.fetchone()[0] == 3


def test_cli_without_database_url_does_not_connect(monkeypatch,capsys):
    from scripts import migrate_role_tenants
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setattr(migrate_role_tenants.psycopg2,'connect',lambda *a,**kw:pytest.fail('No URL must not connect'))
    assert migrate_role_tenants.main() == 1
    assert 'DATABASE_URL is required' in capsys.readouterr().err


def test_cli_connection_failure_does_not_print_secrets(monkeypatch,capsys):
    from scripts import migrate_role_tenants
    private_url='postgresql://user:fake-private-password@invalid/db'
    monkeypatch.setenv('DATABASE_URL',private_url)
    def fail(*args,**kwargs):
        raise psycopg2.OperationalError(private_url)
    monkeypatch.setattr(migrate_role_tenants.psycopg2,'connect',fail)
    assert migrate_role_tenants.main() == 1
    output=capsys.readouterr().err
    assert 'fake-private-password' not in output
    assert 'deployment must stop' in output
