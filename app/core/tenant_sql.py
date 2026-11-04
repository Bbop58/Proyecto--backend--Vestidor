"""Scope parsed, read-only report queries before executing model-generated SQL."""
import sqlglot
from sqlglot import exp
from fastapi import HTTPException
from sqlalchemy import select
from app.core.tenant_scope import predicates


def scope_report_sql(sql, db, tenant_id):
    if not tenant_id:
        raise HTTPException(403,'El reporte requiere una empresa explícita')
    try:
        tree = sqlglot.parse_one(sql,read='postgres')
    except sqlglot.errors.ParseError:
        raise HTTPException(400,'Consulta de reporte no válida')
    if not isinstance(tree,(exp.Select,exp.Union)) or tree.find(exp.CTE) or tree.find(exp.Into) or tree.find(exp.Lock):
        raise HTTPException(400,'Use una consulta SELECT de solo lectura para reportes')
    functions = {'COUNT','SUM','AVG','MIN','MAX','COALESCE','EXTRACT','TIMESTAMP_TRUNC','DATE_TRUNC',
                 'AND','OR','CASE','IF','TIME_TO_STR','ROUND','CAST','LOWER','UPPER','TO_CHAR','NULLIF','ABS','CURRENT_DATE','CURRENT_TIMESTAMP'}
    if any(column.name.lower() in {'hashed_password','paypal_client_secret','firma_balance'} for column in tree.find_all(exp.Column)):
        raise HTTPException(400,'Columna privada no autorizada para reportes')
    for function in tree.find_all(exp.Func):
        name = function.name.upper() if isinstance(function,exp.Anonymous) else function.sql_name()
        if name not in functions:
            raise HTTPException(400,'Función no autorizada para reportes')
    tables = {model.__tablename__:(model,condition) for model,condition in predicates(tenant_id).items()}
    # Snapshot original table nodes so generated predicates aren't recursively rewritten.
    for table in list(tree.find_all(exp.Table)):
        report_tables={'ventas','detalles_venta','productos','variantes_producto','categorias','sucursales','users','inventario','reservas','detalles_reserva','cambios_producto'}
        if table.db or table.catalog or table.name.lower() not in tables or table.name.lower() not in report_tables:
            raise HTTPException(400,'Tabla no autorizada para reportes')
        model,condition = tables[table.name.lower()]
        columns = [c for c in model.__table__.columns if c.name not in
                   {'hashed_password','paypal_client_secret','firma_balance'}]
        stmt = select(*columns).where(condition)
        safe_sql = str(stmt.compile(dialect=db.get_bind().dialect,compile_kwargs={'literal_binds':True}))
        safe_view = sqlglot.parse_one(safe_sql,read='postgres').subquery()
        safe_view.set('alias',table.args.get('alias').copy() if table.args.get('alias') else exp.TableAlias(this=table.this.copy()))
        table.replace(safe_view)
    return tree.sql(dialect='postgres')
