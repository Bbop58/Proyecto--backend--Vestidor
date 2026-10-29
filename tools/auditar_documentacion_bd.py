"""Auditoría de catálogo PostgreSQL y modelos, sin leer datos comerciales ni ejecutar DDL.

Ejecutar desde backend con .venv/Scripts/python.exe tools/auditar_documentacion_bd.py.
La conexión hereda la configuración del proyecto, pero fuerza transacciones de solo lectura.
"""
from __future__ import annotations

import ast
import inspect as pyinspect
import json
import hashlib
import re
import sys
import sqlite3
from datetime import datetime
from enum import Enum as PyEnum
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CheckConstraint, UniqueConstraint
from app.config import settings
from app.database import Base
import app.models  # Solo registro de metadatos; nunca importar app.main.

DIALECT = postgresql.dialect()
OUT = ROOT / "documentacion de base de datos"


def sqltype(value):
    return str(value.compile(dialect=DIALECT))


def value_default(value):
    if value is None:
        return None
    arg = getattr(value, "arg", value)
    if isinstance(arg, PyEnum):
        return arg.value
    if callable(arg):
        return getattr(arg, "__name__", "función") + "()"
    return str(arg)


def orm_snapshot():
    result = {}
    mappers = {m.local_table.name: m for m in Base.registry.mappers}
    for name, table in sorted(Base.metadata.tables.items()):
        mapper = mappers.get(name)
        cls = mapper.class_ if mapper else None
        source = pyinspect.getsourcefile(cls) if cls else str(BACKEND / "app/models/role_permission.py")
        properties = []
        if cls:
            for key, val in vars(cls).items():
                if isinstance(val, property):
                    properties.append({"name": key, "source": pyinspect.getsource(val.fget).strip()})
        result[name] = {
            "class": cls.__name__ if cls else "role_permissions (Table)",
            "source": str(Path(source).relative_to(ROOT)).replace("\\", "/"),
            "columns": [dict(name=c.name, type=sqltype(c.type), nullable=c.nullable,
                             pk=c.primary_key, client_default=value_default(c.default),
                             server_default=value_default(c.server_default),
                             onupdate=value_default(c.onupdate),
                             enum_name=getattr(c.type, "name", None) if hasattr(c.type, "enums") else None,
                             enum_values=list(c.type.enums) if hasattr(c.type, "enums") else None)
                        for c in table.columns],
            "pk": {"name": table.primary_key.name, "constrained_columns": [c.name for c in table.primary_key]},
            "unique": [dict(name=c.name, column_names=[x.name for x in c.columns])
                       for c in table.constraints if isinstance(c, UniqueConstraint)],
            "checks": [dict(name=c.name, sqltext=str(c.sqltext))
                       for c in table.constraints if isinstance(c, CheckConstraint)],
            "indexes": [dict(name=i.name, unique=i.unique,
                             column_names=[getattr(c, "name", str(c)) for c in i.expressions])
                        for i in sorted(table.indexes, key=lambda i: i.name or "")],
            "fks": [dict(name=fk.name, constrained_columns=[e.parent.name for e in fk.elements],
                         referred_schema=fk.elements[0].column.table.schema or "public",
                         referred_table=fk.elements[0].column.table.name,
                         referred_columns=[e.column.name for e in fk.elements],
                         options={"ondelete": fk.ondelete or "NO ACTION", "onupdate": fk.onupdate or "NO ACTION"})
                    for fk in sorted(table.foreign_key_constraints,
                                     key=lambda f: tuple(e.parent.name for e in f.elements))],
            "relationships": [dict(name=r.key, target=r.mapper.class_.__name__, uselist=r.uselist,
                                   local_columns=sorted(c.name for c in r.local_columns),
                                   back_populates=r.back_populates, cascade=str(r.cascade))
                              for r in mapper.relationships] if mapper else [],
            "computed_properties": properties,
        }
    declared=set()
    for source in (BACKEND/"app/models").glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8-sig"))):
            if isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id=="__tablename__" for x in node.targets):
                declared.add(ast.literal_eval(node.value))
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=="Table" and node.args and isinstance(node.args[0],ast.Constant):
                declared.add(node.args[0].value)
    assert declared==set(result), "Un modelo declarado no fue registrado en Base.metadata."
    return result


def live_snapshot():
    engine = create_engine(settings.SQLALCHEMY_DATABASE_URI, hide_parameters=True,
                           connect_args={"connect_timeout": 8,
                                         "options": "-c default_transaction_read_only=on -c statement_timeout=20000"})
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            assert conn.execute(text("SHOW transaction_read_only")).scalar() == "on"
            ins = inspect(conn)
            schemas = [s for s in ins.get_schema_names() if s != "information_schema" and not s.startswith("pg_")]
            tables = {}
            views = []
            for schema in schemas:
                for name in ins.get_table_names(schema=schema):
                    columns = []
                    for c in ins.get_columns(name, schema=schema):
                        columns.append(dict(name=c["name"], type=sqltype(c["type"]), nullable=c["nullable"],
                                            server_default=c.get("default"), identity=c.get("identity"),
                                            computed=c.get("computed"), comment=c.get("comment"),
                                            enum_name=getattr(c["type"], "name", None) if hasattr(c["type"], "enums") else None,
                                            enum_values=list(c["type"].enums) if hasattr(c["type"], "enums") else None))
                    key = f"{schema}.{name}"
                    tables[key] = dict(schema=schema, name=name, columns=columns,
                                       pk=ins.get_pk_constraint(name, schema=schema),
                                       unique=ins.get_unique_constraints(name, schema=schema),
                                       checks=ins.get_check_constraints(name, schema=schema),
                                       fks=ins.get_foreign_keys(name, schema=schema),
                                       indexes=ins.get_indexes(name, schema=schema),
                                       comment=ins.get_table_comment(name, schema=schema).get("text"))
                for name in ins.get_view_names(schema=schema):
                    views.append(dict(schema=schema, name=name, kind="view", definition=ins.get_view_definition(name, schema=schema)))
                for name in ins.get_materialized_view_names(schema=schema):
                    views.append(dict(schema=schema, name=name, kind="materialized view", definition=ins.get_view_definition(name, schema=schema)))
            query = """
                SELECT n.nspname AS schema, c.relname AS table_name, k.conname AS name,
                       k.contype AS kind, k.convalidated AS validated, k.condeferrable AS deferrable,
                       k.condeferred AS initially_deferred, pg_get_constraintdef(k.oid, true) AS definition
                  FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                 ORDER BY n.nspname,c.relname,k.conname
            """
            constraints = [dict(r) for r in conn.execute(text(query)).mappings()]
            indexes = [dict(r) for r in conn.execute(text("""
                SELECT schemaname AS schema, tablename AS table_name, indexname AS name, indexdef AS definition
                FROM pg_indexes WHERE schemaname NOT LIKE 'pg_%' AND schemaname <> 'information_schema'
                ORDER BY schemaname,tablename,indexname
            """)).mappings()]
            triggers = [dict(r) for r in conn.execute(text("""
                SELECT n.nspname AS schema,c.relname AS table_name,t.tgname AS name,
                       pg_get_triggerdef(t.oid,true) AS definition
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE NOT t.tgisinternal AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                ORDER BY n.nspname,c.relname,t.tgname
            """)).mappings()]
            policies = [dict(r) for r in conn.execute(text("""
                SELECT schemaname AS schema,tablename AS table_name,policyname AS name,
                       permissive,roles,cmd,qual,with_check FROM pg_policies
                WHERE schemaname NOT LIKE 'pg_%' AND schemaname <> 'information_schema'
            """)).mappings()]
            rls = [dict(r) for r in conn.execute(text("""
                SELECT n.nspname AS schema,c.relname AS table_name,c.relrowsecurity AS enabled,
                       c.relforcerowsecurity AS forced FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                ORDER BY n.nspname,c.relname
            """)).mappings()]
            sequences = [dict(r) for r in conn.execute(text("""
                SELECT sequence_schema AS schema,sequence_name AS name,data_type,start_value,
                       minimum_value,maximum_value,increment,cycle_option
                FROM information_schema.sequences WHERE sequence_schema NOT LIKE 'pg_%'
            """)).mappings()]
            routines = [dict(r) for r in conn.execute(text("""
                SELECT n.nspname AS schema,p.proname AS name,
                       pg_get_function_identity_arguments(p.oid) AS arguments,
                       pg_get_function_result(p.oid) AS result,l.lanname AS language,
                       e.extname AS extension
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                JOIN pg_language l ON l.oid=p.prolang
                LEFT JOIN pg_depend d ON d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e'
                LEFT JOIN pg_extension e ON e.oid=d.refobjid
                WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                ORDER BY n.nspname,p.proname,arguments
            """)).mappings()]
            extensions = [dict(r) for r in conn.execute(text("SELECT extname AS name,extversion AS version FROM pg_extension ORDER BY extname")).mappings()]
            inventory = [dict(r) for r in conn.execute(text("""
                SELECT n.nspname AS schema,c.relname AS name,c.relkind AS kind
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                  AND c.relkind IN ('r','p','f','v','m','S')
                ORDER BY n.nspname,c.relname
            """)).mappings()]
            assert {f"{x['schema']}.{x['name']}" for x in inventory if x["kind"] in ("r","p")} == set(tables)
            column_catalog=[dict(r) for r in conn.execute(text("""
                SELECT n.nspname AS schema,c.relname AS table_name,a.attname AS name,a.attnum AS ordinal,
                       format_type(a.atttypid,a.atttypmod) AS physical_type,a.attnotnull AS not_null,
                       a.attidentity AS identity_kind,a.attgenerated AS generated_kind,
                       cn.nspname AS collation_schema,cl.collname AS collation,
                       pg_get_expr(ad.adbin,ad.adrelid) AS default_expression
                FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                LEFT JOIN pg_collation cl ON cl.oid=a.attcollation
                LEFT JOIN pg_namespace cn ON cn.oid=cl.collnamespace
                LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
                WHERE c.relkind IN ('r','p','f') AND a.attnum>0 AND NOT a.attisdropped
                  AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                ORDER BY n.nspname,c.relname,a.attnum
            """)).mappings()]
            assert {(c["schema"]+"."+c["table_name"],c["name"]) for c in column_catalog} == {(key,c["name"]) for key,t in tables.items() for c in t["columns"]}
            domains=[dict(r) for r in conn.execute(text("""
                SELECT n.nspname AS schema,t.typname AS name,format_type(t.typbasetype,t.typtypmod) AS base_type,
                       t.typnotnull AS not_null,t.typdefault AS default_expression
                FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                WHERE t.typtype='d' AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                ORDER BY n.nspname,t.typname
            """)).mappings()]
            revisions = [row[0] for row in conn.execute(text("SELECT version_num FROM public.alembic_version"))] if "public.alembic_version" in tables else []
            result = dict(status="verified_read_only", schemas=schemas, tables=tables,
                          enums=ins.get_enums(schema="*"), constraints=constraints, indexes=indexes,
                          views=views, triggers=triggers, policies=policies, row_security=rls,
                          sequences=sequences, routines=routines, extensions=extensions,
                          catalog_inventory=inventory, column_catalog=column_catalog, domains=domains, alembic_revisions=revisions,
                          server_version=conn.execute(text("SHOW server_version")).scalar())
            conn.rollback()
            return result
    finally:
        engine.dispose()


def unique_sets(table):
    sets = [set(table["pk"].get("constrained_columns") or [])]
    sets += [set(x["column_names"]) for x in table["unique"]]
    sets += [set(x["column_names"]) for x in table["indexes"]
             if x.get("unique") and all(x.get("column_names", []))
             and not x.get("dialect_options", {}).get("postgresql_where")]
    return [s for s in sets if s]


def cardinality(table, fk):
    fields = set(fk["constrained_columns"])
    nullable = any(c["nullable"] for c in table["columns"] if c["name"] in fields)
    return ("0..1" if nullable else "1", "0..1" if any(s <= fields for s in unique_sets(table)) else "0..*")


def fk_key(fk):
    return (tuple(fk["constrained_columns"]), fk.get("referred_schema") or "public",
            fk["referred_table"], tuple(fk["referred_columns"]))


def compare(orm, live):
    diffs = []
    def add(table, category, field, model, db):
        diffs.append(dict(table=table, category=category, field=field, orm=model, database=db))
    for name, m in orm.items():
        db = live["tables"].get("public." + name)
        if db is None:
            add(name, "tabla", "existencia", "declarada", "ausente"); continue
        mc = {c["name"]: c for c in m["columns"]}; dc = {c["name"]: c for c in db["columns"]}
        for field in sorted(mc.keys() | dc.keys()):
            if field not in mc or field not in dc:
                add(name, "columna", field, "presente" if field in mc else "ausente", "presente" if field in dc else "ausente"); continue
            for key in ["type", "nullable", "enum_name", "enum_values", "server_default"]:
                if mc[field].get(key) != dc[field].get(key):
                    add(name, key, field, mc[field].get(key), dc[field].get(key))
        if m["pk"]["constrained_columns"] != db["pk"]["constrained_columns"]:
            add(name, "PK", "columnas", m["pk"]["constrained_columns"], db["pk"]["constrained_columns"])
        mf={fk_key(f): f for f in m["fks"]}; df={fk_key(f): f for f in db["fks"]}
        for key in sorted(mf.keys() | df.keys()):
            if key not in mf or key not in df:
                add(name,"FK",str(key),"presente" if key in mf else "ausente","presente" if key in df else "ausente"); continue
            for action in ["ondelete", "onupdate"]:
                a=mf[key].get("options",{}).get(action) or "NO ACTION"; b=df[key].get("options",{}).get(action) or "NO ACTION"
                if a != b:add(name,action,", ".join(key[0]),a,b)
        if {tuple(sorted(s)) for s in unique_sets(m)} != {tuple(sorted(s)) for s in unique_sets(db)}:
            add(name,"unicidad","conjuntos de columnas",sorted(sorted(s) for s in unique_sets(m)),sorted(sorted(s) for s in unique_sets(db)))
        mi={x["name"]:x for x in m["indexes"]}; di={x["name"]:x for x in db["indexes"] if not x.get("duplicates_constraint")}
        for name_index in sorted(mi.keys() | di.keys()):
            a=mi.get(name_index); b=di.get(name_index)
            if a is None or b is None:add(name,"índice",name_index,"presente" if a else "ausente","presente" if b else "ausente")
            elif (a["unique"],a["column_names"]) != (b["unique"],b["column_names"]):
                add(name,"índice",name_index,a,b)
        if {(c.get("sqltext") or "").strip() for c in m["checks"]} != {(c.get("sqltext") or "").strip() for c in db["checks"]}:
            add(name,"CHECK","reglas SQL",m["checks"],db["checks"])
    return diffs


def migration_snapshot():
    created={}; added={}; files=[]
    for path in sorted((BACKEND/"alembic/versions").glob("*.py")):
        tree=ast.parse(path.read_text(encoding="utf-8-sig")); files.append(str(path.relative_to(ROOT)).replace("\\","/"))
        for fn in tree.body:
            if not isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)) or fn.name!="upgrade":continue
            for node in ast.walk(fn):
                if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute) or not isinstance(node.func.value,ast.Name) or node.func.value.id!="op":continue
                if node.func.attr=="create_table" and node.args:
                    name=ast.literal_eval(node.args[0]);cols=[]
                    for c in node.args[1:]:
                        if isinstance(c,ast.Call) and isinstance(c.func,ast.Attribute) and c.func.attr=="Column":cols.append(ast.literal_eval(c.args[0]))
                    created[name]=dict(columns=cols,source=str(path.relative_to(ROOT)).replace("\\","/"))
                if node.func.attr=="add_column" and len(node.args)>1:
                    name=ast.literal_eval(node.args[0]);col=ast.literal_eval(node.args[1].args[0]);added.setdefault(name,[]).append(col)
    return dict(files=files,created=created,added_columns=added)


def offline_snapshot():
    mobile=ROOT/"mobile/lib/core/database/database_helper.dart"; dart=mobile.read_text(encoding="utf-8")
    sql=[s.strip() for s in re.findall(r"db\.execute\('''(.*?)'''\)",dart,re.S)]
    mem=sqlite3.connect(":memory:")
    for statement in sql:mem.execute(statement)
    reconstructed={}
    for (name,) in mem.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        reconstructed[name]=dict(columns=[dict(zip(["cid","name","type","notnull","default","pk"],row)) for row in mem.execute(f'PRAGMA table_info("{name}")')],
                                 fks=mem.execute(f'PRAGMA foreign_key_list("{name}")').fetchall(),indexes=mem.execute(f'PRAGMA index_list("{name}")').fetchall())
    mem.close()
    web=ROOT/"web/src/app/core/services/offline-storage.service.ts"; ts=web.read_text(encoding="utf-8")
    stores=[dict(name=n,key_path=k) for n,k in re.findall(r"createObjectStore\('([^']+)',\s*\{\s*keyPath:\s*'([^']+)'",ts)]
    sync=ROOT/"web/src/app/core/models/sync.model.ts"
    return dict(sqlite=dict(source=str(mobile.relative_to(ROOT)).replace("\\","/"),name="fashion_store_offline.db",version=1,ddl=sql,reconstructed_tables=reconstructed,device_verified=False),
                indexeddb=dict(source=str(web.relative_to(ROOT)).replace("\\","/"),name="FashionStoreOfflineDB",version=1,stores=stores,
                               indexes=re.findall(r"createIndex\('([^']+)',\s*'([^']+)'",ts),
                               typescript_contract=sync.read_text(encoding="utf-8"),browser_verified=False))


def main():
    OUT.mkdir(exist_ok=True)
    orm=orm_snapshot()
    try: live=live_snapshot()
    except Exception as e:
        print(json.dumps({"error":"No se pudo completar lectura del catálogo; no se publica una auditoría completa.","type":type(e).__name__}));return 1
    snapshot=dict(verified_at=datetime.now(ZoneInfo("America/La_Paz")).isoformat(),
                  scope="Base PostgreSQL configurada en backend; catálogos de todos los esquemas no internos visibles para la conexión; sin datos comerciales ni credenciales.",
                  orm=orm, database=live, differences=compare(orm,live),migrations=migration_snapshot(),offline=offline_snapshot())
    sources=list((BACKEND/"app/models").glob("*.py"))+list((BACKEND/"alembic/versions").glob("*.py"))+[BACKEND/"app/main.py",ROOT/"mobile/lib/core/database/database_helper.dart",ROOT/"web/src/app/core/services/offline-storage.service.ts",ROOT/"web/src/app/core/models/sync.model.ts"]
    snapshot["source_sha256"]={str(p.relative_to(ROOT)).replace("\\","/"):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    path=OUT/"esquema_verificado.json";path.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"snapshot":str(path),"tablas_orm":len(orm),"tablas_db":len(live["tables"]),
                      "columnas_orm":sum(len(t["columns"]) for t in orm.values()),"columnas_db":sum(len(t["columns"]) for t in live["tables"].values()),
                      "fk_db":sum(len(t["fks"]) for t in live["tables"].values()),"diferencias":len(snapshot["differences"]),
                      "categorias_diferencias":{c:sum(d["category"]==c for d in snapshot["differences"]) for c in sorted({d["category"] for d in snapshot["differences"]})}},ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
