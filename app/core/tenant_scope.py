"""Request-owned ORM isolation. Background/seed sessions remain explicitly unscoped."""
from fastapi import HTTPException
from sqlalchemy import event, or_, and_, inspect
from sqlalchemy.orm import Session, with_loader_criteria
from app.database import Base


def bind_tenant(db, tenant_id, user_id):
    previous = db.info.get('tenant_scope')
    if previous and previous != tenant_id:
        raise HTTPException(403, 'No se permite cambiar de empresa durante una operación')
    db.info.update(tenant_scope=tenant_id, actor_id=user_id)


def predicates(tenant_id):
    from app import models as m
    result = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if hasattr(cls,'tenant_id'):
            result[cls] = cls.tenant_id == tenant_id
    result[m.Tenant] = m.Tenant.id == tenant_id
    result[m.Role] = or_(m.Role.tenant_id == tenant_id, and_(m.Role.tenant_id.is_(None), m.Role.name.in_(["admin","encargado","cajero","cliente"])))
    for cls, rel, parent in [
        (m.ProductVariant,'producto',m.Product), (m.SaleDetail,'venta',m.Sale),
        (m.ReservationDetail,'reserva',m.Reservation),
        (m.ProductExchangeDetail,'cambio',m.ProductExchange),
        (m.Notification,'user',m.User)]:
        result[cls] = getattr(cls,rel).has(parent.tenant_id == tenant_id)
    return result


@event.listens_for(Session,'do_orm_execute')
def isolate_queries(state):
    from app.models.platform_audit_log import PlatformAuditLog
    actor = state.session.info.get('platform_actor_id')
    mappers = {mapper.class_ for mapper in state.all_mappers}
    if actor:
        from app.models import User, Tenant, Role, Permission, TokenBlacklist, Subscription, SubscriptionPlan, SubscriptionPlanVersion
        allowed = {User,Tenant,Role,Permission,TokenBlacklist,Subscription,SubscriptionPlan,SubscriptionPlanVersion,PlatformAuditLog}
        from sqlalchemy.sql import visitors
        from sqlalchemy.sql.schema import Table
        tables = {node.name for node in visitors.iterate(state.statement) if isinstance(node,Table)}
        allowed_tables = {model.__tablename__ for model in allowed} | {'role_permissions'}
        if (not state.is_select or not state.is_orm_statement or not mappers <= allowed
                or not tables <= allowed_tables):
            raise HTTPException(403, 'El contexto de plataforma no permite consultas comerciales')
        state.statement = state.statement.options(with_loader_criteria(User,User.id==actor,include_aliases=True))
    elif PlatformAuditLog in mappers and state.session.info.get('tenant_scope'):
        raise HTTPException(403, 'La auditoría SaaS no pertenece al negocio')
    tenant = state.session.info.get('tenant_scope')
    if not tenant:
        return
    if state.is_select and not state.is_column_load and not state.is_relationship_load:
        for cls, condition in predicates(tenant).items():
            state.statement = state.statement.options(with_loader_criteria(cls,condition,include_aliases=True))
    elif state.is_update or state.is_delete:
        # The season service deactivates the other seasons of the same year.
        # Every bulk write involving ownership/FKs remains forbidden.
        from app.models.season import Season
        mapper = state.bind_mapper
        values = getattr(state.statement,'_values',{}) or {}
        keys = {getattr(key,'key',str(key)) for key in values}
        if state.is_update and mapper is not None and mapper.class_ is Season and keys <= {'activa'}:
            state.statement = state.statement.where(Season.tenant_id == tenant)
        else:
            raise HTTPException(403,'La operación masiva requiere un procedimiento explícito de empresa')


@event.listens_for(Session,'before_flush')
def isolate_writes(db, context, instances):
    from app.models.platform_audit_log import PlatformAuditLog
    from app.models.user import User
    actor = db.info.get('platform_actor_id')
    if actor:
        for obj in list(db.new)+list(db.dirty)+list(db.deleted):
            if isinstance(obj,PlatformAuditLog) and obj in db.new:
                continue
            if isinstance(obj,User) and obj.id==actor and obj not in db.new and obj not in db.deleted:
                changed={a.key for a in inspect(obj).attrs if a.history.has_changes()}
                if changed <= {'full_name','email','hashed_password','updated_at'}:
                    if 'email' in changed:
                        from sqlalchemy import select
                        duplicate=db.connection().execute(select(User.id).where(User.email==obj.email,User.id!=actor)).first()
                        if duplicate:
                            db.info['scope_violation']=True
                            raise HTTPException(400,'El correo electrónico ya está registrado')
                    continue
            raise HTTPException(403, 'La plataforma no puede escribir datos del negocio')
        return
    tenant = db.info.get('tenant_scope')
    if not tenant:
        return
    from app.models.role import Role
    scoped = predicates(tenant)
    table_models = {mapper.local_table.name:mapper.class_ for mapper in Base.registry.mappers}
    def reject(code,message):
        db.info['scope_violation'] = True
        raise HTTPException(code,message)
    with db.no_autoflush:
        for obj in list(db.new)+list(db.dirty)+list(db.deleted):
            cls = type(obj)
            from app.models.tenant import Tenant
            from app.core.access_policy import PLATFORM_ROLE
            if isinstance(obj,PlatformAuditLog):
                reject(403,'La auditoría SaaS requiere una identidad de plataforma')
            if cls.__name__ in {'SubscriptionTerm','SubscriptionChangeRequest','SubscriptionPayment','SubscriptionPaymentEvent'}:
                reject(403,'El contrato requiere un procedimiento explícito verificado')
            if isinstance(obj,Tenant) and (obj.es_operadora or inspect(obj).attrs.es_operadora.history.has_changes()):
                reject(403,'Una empresa no puede convertirse en operadora')
            if isinstance(obj,Role) and obj.name == PLATFORM_ROLE:
                reject(403,'El rol de plataforma no puede administrarse desde una empresa')
            if cls not in scoped:
                if cls.__name__ in {'Permission','SubscriptionPlan','SubscriptionPlanVersion'}:
                    if obj in db.new or obj in db.deleted or db.is_modified(obj,include_collections=False):
                        reject(403,'La configuración global requiere administración de plataforma')
                continue
            if isinstance(obj,Role) and obj not in db.new and obj.tenant_id is None:
                if db.is_modified(obj,include_collections=True) or obj in db.deleted:
                    reject(403,'Los roles base compartidos no se modifican desde una empresa')
                continue
            if cls.__name__ == 'User' and obj not in db.deleted:
                from sqlalchemy import select
                table = cls.__table__
                duplicate = db.execute(select(table.c.id).where(table.c.email == obj.email,table.c.id != obj.id)).first()
                if duplicate:
                    reject(400,'El correo electrónico ya está registrado')
            if hasattr(obj,'tenant_id'):
                if obj in db.new and obj.tenant_id is None:
                    obj.tenant_id = tenant
                if obj.tenant_id != tenant:
                    reject(404,'Recurso no disponible en esta empresa')
                if inspect(obj).attrs.tenant_id.history.deleted:
                    old = inspect(obj).attrs.tenant_id.history.deleted[0]
                    if old != tenant:
                        reject(403,'No se permite reasignar pertenencia entre empresas')
            if cls.__name__ == 'Tenant' and (obj in db.new or obj.id != tenant):
                reject(403,'Operación de empresa no permitida')
            # Assigning an ORM relationship can defer its FK value until after
            # before_flush. Validate those pending references as well as IDs.
            for relationship in inspect(cls).relationships:
                target = relationship.mapper.class_
                if target not in scoped:
                    continue
                for related in inspect(obj).attrs[relationship.key].history.added:
                    if related is None:
                        continue
                    if target.__name__ == 'Tenant':
                        if related.id != tenant:
                            reject(404,'Referencia no disponible en esta empresa')
                    elif hasattr(related,'tenant_id'):
                        shared_role = isinstance(related,Role) and related.tenant_id is None and related.name in {'admin','encargado','cajero','cliente'}
                        pending_owned = related in db.new and related.tenant_id is None
                        if related.tenant_id != tenant and not shared_role and not pending_owned:
                            reject(404,'Referencia no disponible en esta empresa')
            for column in inspect(cls).columns:
                value = getattr(obj,column.key)
                if value is None:
                    continue
                for fk in column.foreign_keys:
                    target = table_models.get(fk.column.table.name)
                    if target not in scoped:
                        continue
                    target_column = getattr(target,fk.column.key)
                    if not db.query(target_column).filter(target_column == value,scoped[target]).first():
                        # Related objects may be pending in this same atomic operation.
                        pending = next((x for x in db.new if isinstance(x,target) and getattr(x,fk.column.key)==value),None)
                        if pending is None or (hasattr(pending,'tenant_id') and pending.tenant_id not in (None,tenant)):
                            reject(404,'Referencia no disponible en esta empresa')
