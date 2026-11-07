"""D1 integration rehearsal. Only a disposable local fashionstore_test_ database."""
import argparse
import json
import os
from pathlib import Path
import sys
import socket
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-disposable-demo', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from sqlalchemy.engine import make_url
    url = make_url(os.environ.get('DATABASE_URL', ''))
    if (url.get_backend_name() != 'postgresql' or url.host not in {'127.0.0.1','localhost','::1'}
        or not (url.database or '').startswith('fashionstore_test_') or url.query):
        parser.error('Only explicit disposable local fashionstore_test_ PostgreSQL is allowed')
    from sqlalchemy import create_engine, select, text, event
    from sqlalchemy.orm import Session
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models import Tenant
    from app.database import Base, engine as api_engine
    from app.seed_audit import reconcile
    from scripts.reset_demo_database import reset_demo_database, _snapshot
    engine = create_engine(url)
    seed = reset_demo_database(engine)
    assert not any(seed['checks'].values()) and not seed['tables_changed_on_repeat']
    with engine.connect() as connection:
        companies = connection.execute(select(Tenant.id, Tenant.slug).where(Tenant.es_operadora.is_(False))).all()
        before = _snapshot(connection, Base.metadata)
    result = {'seed_checks': len(seed['checks']), 'seed_repeated_unchanged': True,
              'sales': seed['counts']['ventas'], 'companies': []}
    network_attempts = []
    real_connect = socket.socket.connect
    def local_only(sock, address):
        if address[0] not in {'127.0.0.1','localhost','::1'}:
            network_attempts.append(address[0])
            raise RuntimeError('External network forbidden in D1 rehearsal')
        return real_connect(sock, address)
    read_transactions = []
    def observe_read(conn, cursor, statement, parameters, context, executemany):
        if statement == 'SET TRANSACTION READ ONLY':
            assert conn.get_isolation_level() == 'REPEATABLE READ'
            read_transactions.append(True)
    event.listen(api_engine, 'before_cursor_execute', observe_read)
    with patch('socket.socket.connect', local_only), TestClient(app) as client:
        for identifier, slug in companies:
            login = client.post('/api/v1/auth/login', json={'email':'admin@'+slug+'.com',
                'password':'Password123!', 'tenant_id':str(identifier)})
            assert login.status_code == 200, login.status_code
            headers = {'Authorization':'Bearer '+login.json()['access_token']}
            with engine.connect() as connection:
                before_reads = _snapshot(connection, Base.metadata)
            response = client.get('/api/v1/analytics/recommendations', headers=headers)
            assert response.status_code == 200, response.text
            data = response.json()
            repeat = client.get('/api/v1/analytics/recommendations', headers=headers)
            assert repeat.json()['fingerprint'] == data['fingerprint']
            assert data['tenant_id'] == str(identifier)
            with Session(engine) as db:
                # Independent SQL: no service calculation/receipt reader reused.
                for action in data['acciones']:
                    sold = db.scalar(text('''SELECT coalesce(sum(d.cantidad),0)
                        FROM detalles_venta d JOIN ventas v ON v.id=d.venta_id
                        WHERE v.tenant_id=:tenant AND v.estado='COMPLETADA'
                        AND d.variante_id=:variant AND v.created_at>=:start
                        AND v.created_at<:end'''), dict(tenant=identifier, variant=action['variante_id'],
                            start=data['ventana_desde']+'T00:00:00-04:00', end=data['ventana_hasta']+'T00:00:00-04:00'))
                    stock = db.execute(text('''SELECT sum(i.stock_actual-i.stock_reservado),sum(i.stock_minimo)
                        FROM inventario i JOIN sucursales b ON b.id=i.sucursal_id
                        WHERE i.tenant_id=:tenant AND b.tenant_id=:tenant AND b.activa
                        AND i.activo AND i.variante_id=:variant'''),dict(tenant=identifier,variant=action['variante_id'])).one()
                    target = max((int(sold)*5+3)//4, stock[1])
                    assert action['vendidas4'] == sold and action['disponible'] == stock[0]
                    assert action['stock_objetivo'] == target
                    assert action['reponer'] == max(0,target-stock[0])
                assert not any(reconcile(db.connection()).values())
            with engine.connect() as connection:
                after_reads = _snapshot(connection, Base.metadata)
            assert before_reads == after_reads, 'A read changed database rows'
            result['companies'].append(dict(slug=slug, fingerprint=data['fingerprint'],
                quality=data['calidad'], actions=data['acciones'], interpretation=data['interpretacion'],
                repeat_identical=True, independent_sql=True))
    with engine.connect() as connection:
        after = _snapshot(connection, Base.metadata)
    changed = [name for name in before if before[name] != after[name]]
    # Authentic login is audited; each pair of GETs above must change NO table.
    assert set(changed) <= {'audit_logs'}, changed
    assert not network_attempts and len(read_transactions) == 4
    result.update(all_get_tables_unchanged=True, login_changed_tables=changed,
                  external_network_attempts=len(network_attempts),
                  repeatable_read_only_transactions=len(read_transactions))
    event.remove(api_engine, 'before_cursor_execute', observe_read)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    engine.dispose()
    print('PASS: double seed, both company APIs, independent SQL, repeat fingerprint, all tables unchanged')


if __name__ == '__main__':
    main()
