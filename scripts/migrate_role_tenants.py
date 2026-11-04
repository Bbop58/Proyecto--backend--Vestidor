"""Apply the explicit roles migration before deploying an existing Railway DB.

No application import, automatic seed, credentials, or table recreation.
Run from the backend image: python scripts/migrate_role_tenants.py
"""
import os
from pathlib import Path
import sys

import psycopg2


MIGRATION = Path(__file__).resolve().parents[1] / 'migrations' / 'phase1_role_tenants.sql'


def migrate_connection(connection):
    # The SQL file is also runnable with psql. Here the connection context owns
    # BEGIN/COMMIT so its lock, changes and verification are one transaction.
    script = '\n'.join(
        line for line in MIGRATION.read_text(encoding='utf-8').splitlines()
        if line.strip().upper() not in {'BEGIN;', 'COMMIT;'}
    )
    if connection.autocommit:
        raise ValueError('Migration requires a transactional connection')
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '60s'")
            cursor.execute("SELECT pg_advisory_xact_lock(610134, 1)")
            cursor.execute("SELECT to_regclass('roles'), to_regclass('tenants'), to_regclass('users')")
            if not all(cursor.fetchone()):
                raise RuntimeError('Expected existing roles, tenants and users tables; no seed was run')
            cursor.execute(script)
            # This is the exact field missing from the login's joined query.
            cursor.execute('SELECT tenant_id FROM roles LIMIT 1')
            cursor.fetchall()


def main():
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if not database_url:
        print('[roles migration] DATABASE_URL is required; nothing was changed.', file=sys.stderr)
        return 1
    connection = None
    try:
        connection = psycopg2.connect(database_url, connect_timeout=15)
        migrate_connection(connection)
    except Exception as error:
        # Never print the connection URL, SQL parameters or provider secrets.
        code = getattr(error, 'pgcode', None) or 'configuration/connection'
        print(f'[roles migration] FAILED ({type(error).__name__}, {code}); '
              'transaction rolled back; deployment must stop.', file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
    print('[roles migration] OK: roles.tenant_id and role indexes verified; accounts preserved.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
