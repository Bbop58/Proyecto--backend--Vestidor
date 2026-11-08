"""Explicit mobile session maintenance. No app settings, seed or startup hooks."""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, inspect, text, String, DateTime, Uuid
from scripts.migrate_saas import validate_target


def migrate_connection(connection):
    if not connection.in_transaction():
        raise ValueError('Explicit transaction required')
    schema = connection.scalar(text('SELECT current_schema()'))
    inspector = inspect(connection)
    if not all(inspector.has_table(name, schema=schema) for name in ('tenants', 'users')):
        raise ValueError('Existing company/user schema required')
    connection.execute(text("SET LOCAL lock_timeout='10s'"))
    connection.execute(text('SELECT pg_advisory_xact_lock(610134,9)'))
    quote = connection.dialect.identifier_preparer.quote
    prefix = quote(schema) + '.'
    connection.execute(text(f'''CREATE TABLE IF NOT EXISTS {prefix}mobile_sessions (
        id uuid PRIMARY KEY,
        tenant_id uuid NOT NULL REFERENCES {prefix}tenants(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES {prefix}users(id) ON DELETE CASCADE,
        credential_hash varchar(64) NOT NULL UNIQUE,
        created_at timestamptz NOT NULL,
        last_used_at timestamptz NOT NULL,
        revoked_at timestamptz,
        CONSTRAINT ck_mobile_session_hash CHECK (credential_hash ~ '^[a-f0-9]{{64}}$')
    )'''))
    found = inspect(connection)
    columns = {c['name']: c for c in found.get_columns('mobile_sessions', schema=schema)}
    if set(columns) != {'id','tenant_id','user_id','credential_hash','created_at','last_used_at','revoked_at'}:
        raise ValueError('Unexpected existing mobile session schema')
    if any(c['nullable'] != (name == 'revoked_at') for name, c in columns.items()):
        raise ValueError('Unexpected mobile session nullability')
    if (not all(isinstance(columns[name]['type'], Uuid) for name in ('id','tenant_id','user_id'))
            or not isinstance(columns['credential_hash']['type'], String)
            or columns['credential_hash']['type'].length != 64
            or not all(isinstance(columns[name]['type'], DateTime) and columns[name]['type'].timezone
                       for name in ('created_at','last_used_at','revoked_at'))
            or found.get_pk_constraint('mobile_sessions', schema=schema)['constrained_columns'] != ['id']):
        raise ValueError('Unexpected mobile session types or primary key')
    foreign = found.get_foreign_keys('mobile_sessions', schema=schema)
    if any(f.get('referred_schema') not in (None, schema) for f in foreign):
        raise ValueError('Mobile ownership must reference the current schema')
    if {(tuple(f['constrained_columns']), f['referred_table'], f.get('options', {}).get('ondelete')) for f in foreign} != {
            (('tenant_id',),'tenants','CASCADE'), (('user_id',),'users','CASCADE')}:
        raise ValueError('Unexpected mobile session ownership constraints')
    if not any(c['column_names'] == ['credential_hash'] for c in found.get_unique_constraints('mobile_sessions', schema=schema)):
        raise ValueError('Mobile credential uniqueness required')
    if not any(c['name'] == 'ck_mobile_session_hash' for c in found.get_check_constraints('mobile_sessions', schema=schema)):
        raise ValueError('Mobile credential hash constraint required')
    for field in ('tenant_id', 'user_id'):
        connection.execute(text(f'CREATE INDEX IF NOT EXISTS ix_mobile_sessions_{field} ON {prefix}mobile_sessions ({field})'))
    return {'tables': ['mobile_sessions'], 'seed_executed': False}


def revoke_restored_sessions(connection):
    """Full-database recovery only: revoke BEFORE exposing a restored API."""
    if not connection.in_transaction():
        raise ValueError('Explicit transaction required')
    return connection.execute(text('UPDATE mobile_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE revoked_at IS NULL')).rowcount


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--allow-nonlocal-database', action='store_true')
    parser.add_argument('--revoke-restored-sessions', action='store_true',
                        help='After full pg_dump recovery only; revokes ALL restored mobile sessions')
    args = parser.parse_args(argv)
    if not args.apply:
        parser.error('--apply required after review and backup')
    engine = None
    try:
        url = os.environ.get('DATABASE_URL', '')
        validate_target(url, args.allow_nonlocal_database)
        engine = create_engine(url, connect_args={'connect_timeout': 15})
        with engine.begin() as connection:
            result = migrate_connection(connection)
            if args.revoke_restored_sessions:
                result['revoked_restored_sessions'] = revoke_restored_sessions(connection)
        print(json.dumps(result))
        return 0
    except Exception as error:
        print('Mobile session maintenance failed: ' + type(error).__name__ + '; transaction rolled back', file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    sys.exit(main())
