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
            if cls not in scoped:
                if cls.__name__ in {'Permission','SubscriptionPlan'}:
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
