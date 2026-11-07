import uuid
import pytest
from sqlalchemy import text,inspect
from scripts.migrate_analytics_models import migrate_connection


def test_migration_is_atomic_repeatable_and_grants_only_commercial_admin(db_session):
    engine=db_session.get_bind().engine
    schema='ml_migration_'+uuid.uuid4().hex
    ids=[str(uuid.uuid4()) for _ in range(6)]
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE SCHEMA '+schema)
        conn.exec_driver_sql('SET LOCAL search_path TO '+schema)
        conn.exec_driver_sql('CREATE TABLE tenants(id uuid PRIMARY KEY,es_operadora boolean NOT NULL)')
        conn.exec_driver_sql('CREATE TABLE roles(id uuid PRIMARY KEY,name text,tenant_id uuid REFERENCES tenants(id))')
        conn.exec_driver_sql('CREATE TABLE permissions(id uuid PRIMARY KEY,code text UNIQUE,module text,method text,path text,description text,created_at timestamptz)')
        conn.exec_driver_sql('CREATE TABLE role_permissions(role_id uuid REFERENCES roles(id),permission_id uuid REFERENCES permissions(id),PRIMARY KEY(role_id,permission_id))')
        conn.execute(text('INSERT INTO tenants VALUES (:a,false),(:b,true)'),{'a':ids[0],'b':ids[1]})
        for identifier,name,company in [(ids[2],'admin',ids[0]),(ids[3],'admin',ids[1]),(ids[4],'cajero',ids[0]),(ids[5],'superadmin_plataforma',ids[1])]:
            conn.execute(text('INSERT INTO roles VALUES (:id,:name,:tenant)'),{'id':identifier,'name':name,'tenant':company})
    try:
        with pytest.raises(RuntimeError):
            with engine.begin() as conn:
                conn.exec_driver_sql('SET LOCAL search_path TO '+schema)
                migrate_connection(conn);raise RuntimeError('rollback')
        with engine.connect() as conn:
            assert not inspect(conn).has_table('analytics_models',schema=schema)
            assert conn.scalar(text(f'SELECT count(*) FROM {schema}.permissions'))==0
        with engine.begin() as conn:
            conn.exec_driver_sql('SET LOCAL search_path TO '+schema)
            migrate_connection(conn);migrate_connection(conn)
            assert conn.scalar(text('SELECT count(*) FROM permissions'))==1
            assert str(conn.scalar(text('SELECT role_id FROM role_permissions')))==ids[2]
            assert conn.scalar(text('SELECT count(*) FROM analytics_models'))==0
    finally:
        with engine.begin() as conn:conn.exec_driver_sql('DROP SCHEMA '+schema+' CASCADE')
