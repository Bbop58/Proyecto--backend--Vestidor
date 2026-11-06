"""Explicit operator provisioning. Dry-run by default; no .env loading or automatic grant."""
import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, select, insert, update, delete, MetaData, func
from sqlalchemy.engine import make_url


class BootstrapError(ValueError):
    pass


def provision(connection, user_id, operator_slug='fashionstore-operadora', apply=False):
    # Reflect only the current DB; models/settings imports must not load a private .env.
    metadata=MetaData()
    metadata.reflect(bind=connection)
    for name in ['users','tenants','roles','role_permissions','platform_audit_logs']:
        if name not in metadata.tables:raise BootstrapError('Falta la migración SaaS S1')
    users,tenants,roles,links,audit=(metadata.tables[n] for n in
        ['users','tenants','roles','role_permissions','platform_audit_logs'])
    if 'es_operadora' not in tenants.c:raise BootstrapError('Falta la migración de empresa operadora')
    row=connection.execute(select(users).where(users.c.id==user_id).with_for_update()).mappings().first()
    if not row or not row['is_active']:raise BootstrapError('Selecciona un usuario existente y activo por UUID')
    operator=connection.execute(select(tenants).where(tenants.c.slug==operator_slug)).mappings().first()
    if operator and not operator['es_operadora']:raise BootstrapError('El slug pertenece a una empresa comercial; no se convierte')
    if connection.execute(select(tenants.c.id).where(tenants.c.es_operadora.is_(True),tenants.c.slug!=operator_slug)).first():
        raise BootstrapError('Ya existe otra empresa operadora')
    already=operator is not None and row['tenant_id']==operator['id']
    if not already:
        # Preserve every historical business reference, including wallets and login audit.
        for table in metadata.tables.values():
            for fk in table.foreign_keys:
                if fk.column.table.name=='users' and fk.column.name=='id':
                    count=connection.execute(select(func.count()).select_from(table).where(fk.parent==user_id)).scalar_one()
                    if count:raise BootstrapError('La cuenta tiene referencias históricas; aprovisiona una cuenta nueva sin operaciones')
        for name,columns in {'subscription_terms':('accepted_by','approved_by'),
                            'subscription_change_requests':('accepted_by','decided_by')}.items():
            table=metadata.tables.get(name)
            if table is not None:
                for column in columns:
                    if connection.execute(select(func.count()).select_from(table).where(table.c[column]==user_id)).scalar_one():
                        raise BootstrapError('La cuenta tiene historia contractual; no se reasigna a plataforma')
    report={'mode':'apply' if apply else 'dry-run','user_id':str(user_id),
            'operator_slug':operator_slug,'already_assigned':already,'server_uuid_grant_required':True}
    if not apply:return report
    now=datetime.now(timezone.utc)
    tenant_id=operator['id'] if operator else uuid.uuid4()
    if not operator:
        connection.execute(insert(tenants).values(id=tenant_id,nombre='FashionStore Operadora',
            razon_social='Administración de plataforma',nit_rut='OPERADORA',slug=operator_slug,
            activo=True,es_operadora=True,platform_revision=0,created_at=now,updated_at=now))
    elif not operator['activo']:raise BootstrapError('La empresa operadora debe estar activa')
    role=connection.execute(select(roles.c.id).where(roles.c.tenant_id==tenant_id,
                           roles.c.name=='superadmin_plataforma')).first()
    role_id=role.id if role else uuid.uuid4()
    if not role:
        connection.execute(insert(roles).values(id=role_id,tenant_id=tenant_id,name='superadmin_plataforma',
            description='Operador SaaS sin acceso comercial',created_at=now,updated_at=now))
    connection.execute(delete(links).where(links.c.role_id==role_id))
    connection.execute(update(users).where(users.c.id==user_id).values(
        tenant_id=tenant_id,role_id=role_id,updated_at=now))
    if not already or row['role_id']!=role_id:
        connection.execute(insert(audit).values(id=uuid.uuid4(),actor_id=user_id,
            action='OPERATOR_BOOTSTRAP',target_type='IDENTITY',target_id=user_id,
            reason='Aprovisionamiento explícito por UUID',before_data={},after_data={},created_at=now))
    report['operator_tenant_id']=str(tenant_id)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operator-user-id',type=uuid.UUID,required=True)
    parser.add_argument('--operator-tenant-slug',default='fashionstore-operadora')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true')
    mode.add_argument('--dry-run',action='store_true',help='Simulación explícita (modo predeterminado)')
    parser.add_argument('--confirm-user',type=uuid.UUID)
    parser.add_argument('--allow-nonlocal-database',action='store_true')
    args=parser.parse_args()
    url=os.environ.get('DATABASE_URL')
    if not url:raise BootstrapError('DATABASE_URL debe proporcionarse explícitamente; no se carga .env')
    parsed=make_url(url)
    if parsed.get_backend_name()!='postgresql' or parsed.query:raise BootstrapError('Se requiere PostgreSQL sin parámetros de conexión alternativos')
    local=parsed.host in {'127.0.0.1','localhost','::1'} and (parsed.database or '').startswith('fashionstore_test_')
    if not local and not args.allow_nonlocal_database:raise BootstrapError('Base no local: se requiere autorización de mantenimiento y --allow-nonlocal-database')
    if args.apply and args.confirm_user!=args.operator_user_id:raise BootstrapError('--apply exige --confirm-user con el mismo UUID')
    engine=create_engine(url)
    try:
        with engine.begin() as connection:
            report=provision(connection,args.operator_user_id,args.operator_tenant_slug,args.apply)
        print(json.dumps(report))
    finally:engine.dispose()


if __name__=='__main__':
    try:main()
    except BootstrapError as error:
        print(str(error))
        raise SystemExit(2)
    except Exception as error:
        print('No se completó el aprovisionamiento: '+type(error).__name__)
        raise SystemExit(2)
