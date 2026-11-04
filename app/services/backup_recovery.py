"""Complete, versioned company snapshots; no global restore through a tenant API."""
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Date, DateTime, Enum, Numeric, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

ALIASES = {'variantes_producto':'variantes','user_wallets':'billeteras'}
VERSION = '4.0.0'


def serialize(value):
    if isinstance(value,(datetime,date)): return value.isoformat()
    if isinstance(value,UUID): return str(value)
    if isinstance(value,Decimal): return float(value)
    raise TypeError('Unsupported backup value')


def digest(data):
    return hashlib.sha256(json.dumps(data,default=serialize,sort_keys=True).encode()).hexdigest()


def models_for(tenant):
    from app.core.tenant_scope import predicates
    return {ALIASES.get(model.__tablename__,model.__tablename__):(model,condition)
            for model,condition in predicates(tenant).items()}


def lock_tables(connection,models,mode):
    # Identifiers are exclusively local ORM metadata, never backup input.
    names={model.__tablename__ for model,_ in models.values()}|{'role_permissions','permissions','subscription_plans'}
    quote=connection.dialect.identifier_preparer.quote
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '30s'"))
    connection.execute(text('LOCK TABLE '+','.join(quote(n) for n in sorted(names))+' IN '+mode+' MODE'))


def export_snapshot(db):
    from app.models.role_permission import role_permissions
    from app.models.permission import Permission
    from app.models.subscription_plan import SubscriptionPlan
    tenant=db.info.get('tenant_scope')
    if not tenant: raise HTTPException(403,'El respaldo requiere una sesión de empresa')
    models=models_for(tenant); connection=db.connection();lock_tables(connection,models,'SHARE')
    data={key:[dict(row) for row in connection.execute(select(model.__table__).where(condition)).mappings()]
          for key,(model,condition) in models.items()}
    role_ids=[row['id'] for row in data['roles']]
    data['role_permissions']=[dict(row) for row in connection.execute(select(role_permissions).where(role_permissions.c.role_id.in_(role_ids))).mappings()]
    # Shared dependencies are references only; restore can never change them.
    data['reference_permissions']=[dict(row) for row in connection.execute(select(Permission.__table__)).mappings()]
    plan_ids={row['plan_id'] for row in data.get('subscriptions',[])}
    data['reference_plans']=[dict(row) for row in connection.execute(select(SubscriptionPlan.__table__).where(SubscriptionPlan.id.in_(plan_ids))).mappings()]
    return {'system':'FashionStore','version':VERSION,'scope':'tenant','tenant_id':str(tenant),
            'created_at':datetime.now(timezone.utc).isoformat(),'data':data,
            'checksum_sha256':digest(data),'total_records':sum(len(rows) for rows in data.values())}


def convert_row(table,row):
    if not isinstance(row,dict) or set(row)!=set(table.columns.keys()):
        raise HTTPException(400,'El respaldo no coincide con las columnas del esquema actual')
    result={}
    try:
        for column in table.columns:
            value=row[column.name]
            if value is not None:
                kind=column.type
                if isinstance(kind,PG_UUID): value=UUID(str(value))
                elif isinstance(kind,DateTime): value=datetime.fromisoformat(str(value))
                elif isinstance(kind,Date): value=date.fromisoformat(str(value))
                elif isinstance(kind,Numeric):
                    value=Decimal(str(value))
                    if not value.is_finite(): raise ValueError('Nonfinite amount')
                elif isinstance(kind,Enum) and value not in kind.enums: raise ValueError('Invalid enum')
            elif not column.nullable and not column.primary_key: raise ValueError('Required value')
            result[column.name]=value
    except (ValueError,TypeError): raise HTTPException(400,'Un valor del respaldo no es válido')
    return result


def ordered_rows(table,rows):
    """Insert predecessors before successors for self-referencing sync history."""
    links=[(c.name,fk.column.name) for c in table.columns for fk in c.foreign_keys if fk.column.table is table]
    if not links: return rows
    pending=list(rows);ordered=[]
    while pending:
        ready=[row for row in pending if all(row[c] is None or any(parent[target]==row[c] for parent in ordered) for c,target in links)]
        if not ready: raise HTTPException(400,'El respaldo contiene referencias circulares no válidas')
        for row in ready:ordered.append(row);pending.remove(row)
    return ordered


def restore_snapshot(db,backup,user):
    from app.database import Base
    from app.models.role import Role
    from app.models.role_permission import role_permissions
    from app.models.permission import Permission
    from app.models.subscription_plan import SubscriptionPlan
    from app.models.user_wallet import UserWallet
    from app.services.wallet_service import WalletService
    from app.services.audit_service import AuditService
    tenant=db.info.get('tenant_scope')
    if not user or not user.is_platform_admin:
        raise HTTPException(403,'La restauración requiere administración de plataforma')
    if not tenant or user.tenant_id!=tenant or not isinstance(backup,dict) or backup.get('tenant_id')!=str(tenant):
        raise HTTPException(403,'El respaldo no pertenece a la empresa de esta sesión')
    if backup.get('version')!=VERSION or backup.get('scope')!='tenant':
        raise HTTPException(409,'Formato incompatible: genera un respaldo nuevo con esta versión')
    data=backup.get('data')
    if not isinstance(data,dict) or not backup.get('checksum_sha256') or digest(data)!=backup['checksum_sha256']:
        raise HTTPException(400,'El checksum del respaldo es obligatorio y debe coincidir')
    models=models_for(tenant)
    if set(data)!=set(models)|{'role_permissions','reference_permissions','reference_plans'}:
        raise HTTPException(400,'El respaldo está incompleto o contiene tablas no permitidas')
    if any(not isinstance(rows,list) for rows in data.values()) or sum(len(rows) for rows in data.values())>50000:
        raise HTTPException(400,'El respaldo excede los límites o contiene tablas no válidas')
    tables={model.__tablename__:model.__table__ for model,_ in models.values()}
    rows={model.__tablename__:[convert_row(model.__table__,row) for row in data[key]] for key,(model,_) in models.items()}
    rows['role_permissions']=[convert_row(role_permissions,row) for row in data['role_permissions']]
    connection=db.connection()
    try:
        lock_tables(connection,models,'EXCLUSIVE')
        for key,model in [('reference_permissions',Permission),('reference_plans',SubscriptionPlan)]:
            supplied=[convert_row(model.__table__,row) for row in data[key]]
            for row in supplied:
                existing=connection.execute(select(model.__table__).where(model.id==row['id'])).mappings().first()
                if not existing or digest(dict(existing))!=digest(row):
                    raise HTTPException(409,'Las referencias compartidas cambiaron; no se restauraron datos')
            rows[model.__tablename__]=supplied
        for name,items in rows.items():
            table=Base.metadata.tables[name]
            keys=[tuple(row[c.name] for c in table.primary_key) for row in items]
            if len(keys)!=len(set(keys)):raise HTTPException(400,'Hay identificadores duplicados en el respaldo')
            for row in items:
                if name=='tenants' and row['id']!=tenant:raise HTTPException(403,'Empresa ajena en el respaldo')
                shared=name=='roles' and row['tenant_id'] is None
                if 'tenant_id' in row and row['tenant_id']!=tenant and not shared:
                    raise HTTPException(403,'Hay registros de otra empresa en el respaldo')
                if shared:
                    existing=connection.execute(select(Role.__table__).where(Role.id==row['id'],Role.tenant_id.is_(None))).mappings().first()
                    if not existing or digest(dict(existing))!=digest(row):
                        raise HTTPException(409,'Los roles compartidos no pueden modificarse desde un respaldo')
                # Every scoped relationship must be inside this complete snapshot.
                for column in table.columns:
                    for fk in column.foreign_keys:
                        if row[column.name] is None:continue
                        target_rows=rows.get(fk.column.table.name,[])
                        if not any(parent[fk.column.name]==row[column.name] for parent in target_rows):
                            raise HTTPException(400,'Una referencia del respaldo no pertenece a su grafo de empresa')
        if len(rows['tenants'])!=1 or not any(r['id']==user.id and r['is_active'] for r in rows['users']):
            raise HTTPException(400,'El respaldo debe conservar la empresa y su operador activo')
        # Shared-role permission links must remain exactly as they exist now.
        shared_ids={r['id'] for r in rows['roles'] if r['tenant_id'] is None}
        current_links=[dict(r) for r in connection.execute(select(role_permissions).where(role_permissions.c.role_id.in_(shared_ids))).mappings()]
        saved_links=[r for r in rows['role_permissions'] if r['role_id'] in shared_ids]
        if {tuple(sorted(r.items())) for r in current_links}!={tuple(sorted(r.items())) for r in saved_links}:
            raise HTTPException(409,'No se pueden cambiar permisos compartidos al restaurar')
        # Prevent ID collisions with other tenants before deleting any own data.
        for name,table in tables.items():
            model,condition=next(pair for pair in models.values() if pair[0].__tablename__==name)
            if len(table.primary_key.columns)!=1:continue
            pk=list(table.primary_key.columns)[0];ids=[r[pk.name] for r in rows[name]]
            if ids and connection.execute(select(pk).where(pk.in_(ids),~condition)).first():
                raise HTTPException(403,'Un identificador pertenece a otra empresa')
        owned_roles=select(Role.id).where(Role.tenant_id==tenant)
        connection.execute(role_permissions.delete().where(role_permissions.c.role_id.in_(owned_roles)))
        for table in reversed(Base.metadata.sorted_tables):
            if table.name not in tables or table.name=='tenants':continue
            model,condition=next(pair for pair in models.values() if pair[0].__tablename__==table.name)
            if model is Role:condition=Role.tenant_id==tenant
            connection.execute(table.delete().where(condition))
        tenant_row=rows['tenants'][0]
        connection.execute(tables['tenants'].update().where(tables['tenants'].c.id==tenant).values(**{k:v for k,v in tenant_row.items() if k!='id'}))
        for table in Base.metadata.sorted_tables:
            if table.name not in tables or table.name=='tenants':continue
            items=rows[table.name]
            if table.name=='roles':items=[r for r in items if r['tenant_id']==tenant]
            for row in ordered_rows(table,items):connection.execute(table.insert().values(**row))
        for row in rows['role_permissions']:
            if row['role_id'] not in shared_ids:connection.execute(role_permissions.insert().values(**row))
        db.expire_all()
        for wallet in db.query(UserWallet).all():
            if not WalletService.verify_wallet_integrity(wallet):raise HTTPException(409,'La clave de billetera no valida las firmas del respaldo')
        AuditService.record(db,'Respaldo de empresa restaurado','Backups',user)
        db.commit()
        return {'success':True,'message':'Respaldo de la empresa restaurado; las demás empresas se conservaron.',
                'restored_entities':{key:len(data[key]) for key in models},'restored_at':datetime.now(timezone.utc).isoformat()}
    except HTTPException:
        db.rollback();raise
    except Exception:
        db.rollback()
        raise HTTPException(409,'No se pudo restaurar el respaldo; todos los cambios fueron revertidos')
