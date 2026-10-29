"""Genera el diccionario y el informe a partir del catálogo capturado, sin conexión DB."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(Path(__file__).parent))
from auditar_documentacion_bd import cardinality, unique_sets

FOLDER = ROOT / "documentacion de base de datos"


def cell(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", "")


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |",
                      "| " + " | ".join("---" for _ in headers) + " |"] +
                     ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]) + "\n"


def code(value):
    return "`" + str(value) + "`"


def mermaid(tables):
    lines = ["erDiagram"]
    for key, t in tables.items():
        alias = key.replace(".", "_")
        lines.append(f"    {alias} {{")
        pk = set(t["pk"].get("constrained_columns") or [])
        fk = {c for f in t["fks"] for c in f["constrained_columns"]}
        singles = {next(iter(s)) for s in unique_sets(t) if len(s) == 1}
        for c in t["columns"]:
            datatype = re.sub(r"[^a-zA-Z0-9_]", "_", c["type"].lower()).strip("_")
            tags = []
            if c["name"] in pk: tags.append("PK")
            if c["name"] in fk: tags.append("FK")
            if c["name"] in singles and c["name"] not in pk: tags.append("UK")
            tag = " " + ",".join(tags) if tags else ""
            note = "NULL" if c["nullable"] else "NOT NULL"
            lines.append(f'        {datatype} {c["name"]}{tag} "{note}"')
        lines.append("    }")
    for key, t in tables.items():
        for f in t["fks"]:
            parent, child = cardinality(t, f)
            left = "|o" if parent == "0..1" else "||"
            right = "o|" if child == "0..1" else "o{"
            identifying = set(f["constrained_columns"]) <= set(t["pk"].get("constrained_columns") or [])
            connector = "--" if identifying else ".."
            target = (f.get("referred_schema") or t["schema"]) + "." + f["referred_table"]
            label = ",".join(f["constrained_columns"])
            lines.append(f'    {target.replace(".", "_")} {left}{connector}{right} {key.replace(".", "_")} : "{label}"')
    return "\n".join(lines) + "\n"


def dbml(snapshot):
    db = snapshot["database"]
    lines = ["// Esquema físico verificado; sin datos ni credenciales.",
             "// Cardinalidad opcional determinada por NULL; no se impone mínimo de hijos.",
             "Project FashionStore {", "  database_type: 'PostgreSQL'", "}", ""]
    for e in db["enums"]:
        lines += [f'Enum {e["schema"]}.{e["name"]} {{']
        lines += ["  " + v for v in e["labels"]]
        lines += ["}", ""]
    for key, t in db["tables"].items():
        lines.append(f"Table {key} {{")
        pk = t["pk"]["constrained_columns"]
        for c in t["columns"]:
            typ = (t["schema"] + "." + c["enum_name"]) if c.get("enum_name") else '"' + c["type"].replace('"', '\\"') + '"'
            tags = ["null" if c["nullable"] else "not null"]
            if len(pk) == 1 and c["name"] in pk: tags.append("pk")
            if c.get("server_default") is not None: tags.append("default: `" + c["server_default"].replace("`", "") + "`")
            lines.append(f'  {c["name"]} {typ} [{", ".join(tags)}]')
        lines.append("  indexes {")
        if len(pk) > 1: lines.append("    (" + ", ".join(pk) + ") [pk]")
        for i in t["indexes"]:
            cols = i.get("column_names") or []
            if not cols or not all(cols): continue
            expr = cols[0] if len(cols) == 1 else "(" + ", ".join(cols) + ")"
            tags = ["name: '" + i["name"].replace("'", "\\'") + "'"]
            if i.get("unique"): tags.append("unique")
            lines.append(f'    {expr} [{", ".join(tags)}]')
        lines += ["  }", "}", ""]
    count = 0
    for key, t in db["tables"].items():
        for f in t["fks"]:
            count += 1
            cols = f["constrained_columns"]; remote = f["referred_columns"]
            local = cols[0] if len(cols) == 1 else "(" + ", ".join(cols) + ")"
            dest = remote[0] if len(remote) == 1 else "(" + ", ".join(remote) + ")"
            target = (f.get("referred_schema") or t["schema"]) + "." + f["referred_table"]
            opts = f.get("options") or {}
            lines.append("// " + (f.get("name") or "FK sin nombre"))
            lines.append(f'Ref fk_{count:03d}: {key}.{local} > {target}.{dest} [delete: {(opts.get("ondelete") or "NO ACTION").lower()}, update: {(opts.get("onupdate") or "NO ACTION").lower()}]')
    return "\n".join(lines) + "\n"


def generate(snapshot):
    db = snapshot["database"]; orm = snapshot["orm"]; tables = db["tables"]
    main_tables = [t for t in tables.values() if t["schema"] == "public" and t["name"] in orm]
    ncols = sum(len(t["columns"]) for t in main_tables)
    nfks = sum(len(t["fks"]) for t in tables.values())
    differences = snapshot["differences"]
    when = snapshot["verified_at"]
    lines = ["# Diccionario verificado de la base de datos FashionStore", "",
             f"Verificación: **{when}**, zona America/La_Paz. PostgreSQL **{db['server_version']}**.", "",
             "Este archivo sustituye la especificación anterior de 25 tablas. Describe el esquema físico de la base PostgreSQL configurada en el backend, consultado mediante una transacción de solo lectura y consistente. No se ejecutaron migraciones, cambios de estructura ni consultas de datos comerciales. Las diferencias con el código se conservan explícitamente; el diccionario no presupone que modelos, migraciones y base ya sean idénticos.", "",
             "## 1. Cobertura y fuente de verdad", "",
             table(["Elemento", "Cantidad verificada"], [
                 ["Tablas del sistema", len(main_tables)], ["Columnas de las tablas del sistema", ncols],
                 ["Tabla técnica adicional", "public.alembic_version (1 columna)"],
                 ["Tablas físicas totales", len(tables)], ["Columnas físicas totales", sum(len(t["columns"]) for t in tables.values())],
                 ["Claves foráneas", nfks], ["Restricciones de tabla (PK, FK, UNIQUE, CHECK)", len(db["constraints"])],
                 ["Índices físicos, incluidos los de PK y UNIQUE", len(db["indexes"])], ["Tipos ENUM nativos", len(db["enums"])],
                 ["Esquemas no internos inventariados", ", ".join(db["schemas"])],
                 ["Diferencias de definición ORM / DB", len(differences)]]), "",
             "La totalidad de las tablas, columnas y FK inventariadas se incluye debajo. La tabla técnica se documenta por separado en el catálogo; no se convierte en una entidad de negocio. La verificación es una fotografía de esta conexión y fecha, no una certificación de otros entornos ni de esquemas futuros.", "",
             "Fuentes: [captura de catálogo y modelos](documentacion%20de%20base%20de%20datos/esquema_verificado.json), [informe de diferencias](documentacion%20de%20base%20de%20datos/AUDITORIA_BASE_DE_DATOS.md), [auditor reproducible](backend/tools/auditar_documentacion_bd.py), [modelos](backend/app/models) y [migraciones](backend/alembic/versions).", "",
             "### 1.1. Reglas de interpretación", "",
             "- En las tablas de atributos, **permite NULL** y **DEFAULT PostgreSQL** proceden de la base física. Un default Python/ORM no constituye un DEFAULT de PostgreSQL.",
             "- Una FK no nula exige **1** padre para cada hijo; una FK nullable permite **0..1**. La cantidad de hijos de un padre es **0..***, salvo unicidad de la FK, que limita a **0..1**. Ninguna FK por sí sola exige que el padre tenga al menos un hijo.",
             "- Los roles de una asociación se distinguen por la columna: cliente y cajero son dos FK a users; prenda devuelta y entregada son dos FK a variantes_producto.",
             "- ON DELETE y cascadas ORM son mecanismos diferentes. CASCADE no basta para afirmar composición UML.",
             "- No se convierten campos de texto, JSON ni identificadores genéricos en FK inexistentes. Las propiedades calculadas del ORM se separan de los atributos persistidos.", "",
             "## 2. Catálogo de tablas y clases de modelo", "",
             table(["Tabla física", "Clase de modelo", "Columnas", "FK salientes", "Archivo"], [
                 [key, orm.get(t["name"], {}).get("class", "Técnica; sin clase ORM"), len(t["columns"]), len(t["fks"]),
                  orm.get(t["name"], {}).get("source", "Administrada por Alembic")]
                 for key, t in tables.items()]), "",
             "## 3. Diccionario completo de atributos", ""]
    for number, (key, t) in enumerate(tables.items(), 1):
        model = orm.get(t["name"], {}) if t["schema"] == "public" else {}
        mc = {c["name"]: c for c in model.get("columns", [])}
        pk = set(t["pk"].get("constrained_columns") or [])
        singles = {next(iter(s)) for s in unique_sets(t) if len(s) == 1}
        fks = {c: f for f in t["fks"] for c in f["constrained_columns"]}
        lines += [f"### 3.{number}. `{key}`", "",
                  "Modelo: **" + model.get("class", "Tabla técnica de Alembic, sin entidad de negocio") + "**.", ""]
        if model: lines += [f'Fuente: [{model["source"]}]({model["source"]}).', ""]
        rows = []
        catalog={c["name"]:c for c in db["column_catalog"] if c["schema"]==t["schema"] and c["table_name"]==t["name"]}
        for c in t["columns"]:
            name = c["name"]; tags = []
            if name in pk: tags.append("PK")
            if name in fks: tags.append("FK")
            if name in singles and name not in pk: tags.append("UNIQUE")
            fk = fks.get(name)
            target = ((fk.get("referred_schema") or t["schema"]) + "." + fk["referred_table"] + "(" + ", ".join(fk["referred_columns"]) + ")") if fk else None
            cat=catalog[name];collation=(cat["collation_schema"]+"."+cat["collation"]) if cat["collation"] else None
            rows.append([code(name), code(cat["physical_type"]), c["nullable"], ", ".join(tags) or "—", c.get("server_default"), mc.get(name, {}).get("client_default"), target,collation])
        lines += [table(["Atributo", "Tipo físico exacto", "Permite NULL", "Claves", "DEFAULT PostgreSQL", "Default Python/ORM", "Referencia FK","Collation"], rows), ""]
        constraints = [c for c in db["constraints"] if c["schema"] == t["schema"] and c["table_name"] == t["name"]]
        lines += ["**Restricciones físicas completas**", ""]
        for c in constraints:
            lines.append(f'- `{c["name"]}`: `{c["definition"]}`. Validada: {cell(c["validated"])}; diferible: {cell(c["deferrable"])}; inicialmente diferida: {cell(c["initially_deferred"])}.')
        lines += ["", "**Índices físicos completos**", ""]
        indexes = [i for i in db["indexes"] if i["schema"] == t["schema"] and i["table_name"] == t["name"]]
        for i in indexes: lines.append(f'- `{i["definition"]}`')
        if not indexes: lines.append("Sin índices físicos.")
        for c in t["columns"]:
            if c.get("identity") or c.get("computed") or c.get("comment"):
                lines += ["", f'Detalle de `{c["name"]}`: identidad={cell(c.get("identity"))}; cálculo SQL={cell(c.get("computed"))}; comentario={cell(c.get("comment"))}.']
        updates = [(c["name"], c["onupdate"]) for c in model.get("columns", []) if c.get("onupdate")]
        if updates: lines += ["", "Actualización automática del lado ORM: " + "; ".join(f"`{n}` → `{v}`" for n, v in updates) + ". No implica un trigger en la DB."]
        props = model.get("computed_properties", [])
        if props:
            lines += ["", "**Propiedades calculadas del modelo, no columnas SQL**", ""]
            for prop in props: lines += [f'`{prop["name"]}`:', "", "```python", prop["source"], "```", ""]
        own_diffs = [d for d in differences if d["table"] == t["name"]]
        if own_diffs: lines += ["", "**Diferencias con el ORM**", "", table(["Aspecto", "Campo u objeto", "Declaración ORM", "DB física"], [[d["category"], d["field"], d["orm"], d["database"]] for d in own_diffs])]
        lines += [""]
    lines += ["## 4. Matriz completa de relaciones y cardinalidades", "",
              "Lectura: **padres por hijo** describe el extremo de la tabla referenciada; **hijos por padre** describe el extremo de la tabla que contiene la FK. Se incluyen las 50 FK físicas, sin sustituirlas por relaciones inferidas.", ""]
    rel_rows = []
    for key, t in tables.items():
        for f in t["fks"]:
            parent_card, child_card = cardinality(t, f)
            target = (f.get("referred_schema") or t["schema"]) + "." + f["referred_table"]
            opts = f.get("options") or {}
            rel_rows.append([f["name"], key + "(" + ", ".join(f["constrained_columns"]) + ")",
                             target + "(" + ", ".join(f["referred_columns"]) + ")", parent_card, child_card,
                             opts.get("ondelete") or "NO ACTION", opts.get("onupdate") or "NO ACTION"])
    lines += [table(["FK física", "Tabla hija y atributos", "Tabla padre y atributos", "Padres por hijo", "Hijos por padre", "ON DELETE", "ON UPDATE"], rel_rows), "",
              "### 4.1. Asociaciones que no deben inventarse", "",
              "- `productos.temporada` y `productos.proveedor` son texto; no son FK hacia temporadas o proveedores.",
              "- `sucursales.ciudad` es texto. No existe una tabla ciudades en esta base.",
              "- `sync_operations.entity_id` es UUID sin FK declarada hacia ventas ni reservas.",
              "- `notifications.reference_id`, `movimientos_inventario.referencia` y `ventas.referencia_pago` son referencias genéricas de texto, no FK.",
              "- `token_blacklist` no contiene user_id ni una FK a users.",
              "- `inventario.stock_disponible` y `inventario.alerta` son propiedades calculadas en Python, no columnas.",
              "- `role_permissions` es una tabla asociativa con PK compuesta, no una clase ORM declarada. Relaciona roles y permissions mediante dos FK.",
              "- Los datasets `sales_df` y `customers_df` del análisis ML y los servicios externos no son tablas de esta DB.", "",
              "## 5. Tipos ENUM físicos y otros objetos", "",
              table(["Tipo", "Valores admitidos, en orden"], [[e["schema"] + "." + e["name"], e["labels"]] for e in db["enums"]]), "",
              table(["Objeto", "Cantidad"], [[k, len(db[k])] for k in ["views", "triggers", "policies", "sequences", "routines", "domains", "extensions"]]), "",
              "Las vistas, vistas materializadas, triggers de usuario, secuencias, políticas RLS y rutinas de esquemas no internos se consultaron explícitamente. Su ausencia no se infirió de los modelos. Los triggers internos de integridad referencial pertenecen a PostgreSQL; las FK que los originan están inventariadas.", "",
              "Estado RLS por tabla: " + "; ".join(f'{r["schema"]}.{r["table_name"]}: enabled={str(r["enabled"]).lower()}, forced={str(r["forced"]).lower()}' for r in db["row_security"]) + ".", "",
              "Extensiones: " + ", ".join(f'{e["name"]} {e["version"]}' for e in db["extensions"]) + ".", "",
              "Revisiones registradas en `alembic_version`: " + cell(db.get("alembic_revisions")) + ". Se leyó exclusivamente este identificador técnico de migración, sin registros comerciales.", "",
              "## 6. Relaciones y propiedades del ORM", "",
              "Estas relaciones describen la navegación en Python; no sustituyen las FK y cardinalidades físicas de la sección 4. Se incluyen backrefs configurados y cascadas del ORM.", ""]
    nav_rows = []
    for name, m in orm.items():
        for rel in m["relationships"]:
            nav_rows.append([m["class"], rel["name"], rel["target"], "Colección" if rel["uselist"] else "Objeto", rel["local_columns"], rel["back_populates"], rel["cascade"]])
    lines += [table(["Clase", "Propiedad", "Destino", "Navegación", "Columnas locales", "Back populates", "Cascade ORM"], nav_rows), "",
              "## 7. Persistencia local offline", "",
              "La DB central PostgreSQL y las bases locales son estructuras distintas. A continuación se documenta todo lo declarado en el código local; no se inspeccionó el almacenamiento de un teléfono ni de una sesión de navegador.", "",
              "### 7.1. SQLite de Flutter", ""]
    mobile = snapshot["offline"]["sqlite"]
    lines += [f'Archivo lógico: `{mobile["name"]}`, versión {mobile["version"]}. Fuente: [{mobile["source"]}]({mobile["source"]}).', "",
              "Las tres sentencias CREATE TABLE se reconstruyeron en una SQLite temporal en memoria para verificar columnas y restricciones. Ninguna de estas tablas declara FK. Los ítems y variantes se guardan como JSON de texto; no se representan como tablas hijas inexistentes.", ""]
    for ddl in mobile["ddl"]: lines += ["```sql", ddl, "```", ""]
    lines += ["En estas sentencias, TEXT PRIMARY KEY no se acompaña de NOT NULL ni de WITHOUT ROWID. No se debe atribuir automáticamente la misma nulabilidad que a una PK de PostgreSQL; el resultado exacto de PRAGMA está en la captura JSON.", "",
              "### 7.2. IndexedDB de Angular", ""]
    web = snapshot["offline"]["indexeddb"]
    lines += [f'Base `{web["name"]}`, versión {web["version"]}. Fuente: [{web["source"]}]({web["source"]}).', "",
              table(["Object store", "Key path", "Tipo de valor"], [[s["name"], s["key_path"], "OfflineLocalOperation" if s["name"] == "offline_operations" else "any: objeto de caché sin esquema de campos rígido"] for s in web["stores"]]), "",
              "Índice declarado: `offline_operations.by_local_status` sobre `local_status`. IndexedDB no declara FK ni restricciones relacionales entre estos stores.", "",
              "Contrato TypeScript de operaciones y resultados de sincronización (contrato de aplicación, no columnas de PostgreSQL):", "", "```typescript", web["typescript_contract"].strip(), "```", "",
              "## 8. Estado de migraciones y diferencias con el código", "",
              "Las migraciones versionadas no describen por sí solas todo el esquema actual. Hay tablas creadas por los metadatos ORM al arrancar y columnas añadidas por SQL en el inicio. No se ejecutó ese inicio durante la auditoría.", "",
              "Tablas del sistema sin `op.create_table` en las migraciones versionadas: " + ", ".join(code(x) for x in sorted(set(orm) - set(snapshot["migrations"]["created"]))) + ".", "",
              "Las 55 diferencias se detallan en la sección de cada tabla y en el [informe de auditoría](documentacion%20de%20base%20de%20datos/AUDITORIA_BASE_DE_DATOS.md). Un DEFAULT del servidor adicional no es, por sí solo, un fallo funcional: se distingue de las discrepancias de nulabilidad, tipos, unicidad y borrado.", "",
              "Para los próximos diagramas de clases, los atributos persistidos y las asociaciones se tomarán de este catálogo físico; los controladores y las interfaces conservarán sus responsabilidades y métodos del código. No se agregan tablas ni FK para hacer coincidir un dibujo.", "",
              "## 9. Diagrama ER físico completo", "",
              "Se incluyen las 28 tablas (27 de negocio y una técnica), los 263 atributos y las 50 relaciones. Líneas sólidas: FK que forma parte de la PK hija; líneas punteadas: asociación no identificadora. Las cardinalidades se calculan a partir de NULL y unicidad, no del nombre de la clase ni de ON DELETE.", "",
              "```mermaid", mermaid(tables).strip(), "```", "",
              "## 10. Esquema DBML físico completo", "",
              "Incluye columnas, defaults del servidor, enums, claves, índices y acciones referenciales. Los detalles PostgreSQL de las restricciones se conservan literalmente en la sección 3 y en el JSON de auditoría.", "",
              "```dbml", dbml(snapshot).strip(), "```", "",
              "## 11. Reproducción de la verificación", "",
              "Desde `backend`, con el entorno del proyecto y su configuración de conexión:", "",
              "```powershell", ".venv/Scripts/python.exe tools/auditar_documentacion_bd.py", ".venv/Scripts/python.exe tools/generar_diccionario_bd.py", "```", "",
              "El primer comando consulta catálogos y escribe la captura JSON. El segundo regenera este diccionario, el informe y los formatos ER/DBML. Ninguno ejecuta migraciones, create_all ni modificaciones en la base.", ""]
    (ROOT / "PROMPT_CODEX_DIAGRAMA_BASE_DE_DATOS.md").write_text("\n".join(lines), encoding="utf-8")
    (FOLDER / "esquema_fisico.mmd").write_text(mermaid(tables), encoding="utf-8")
    (FOLDER / "esquema_fisico.dbml").write_text(dbml(snapshot), encoding="utf-8")
    write_report(snapshot)
    print(json.dumps({"tablas_documentadas": len(tables), "columnas_documentadas": sum(len(t["columns"]) for t in tables.values()),
                      "fk_documentadas": len(rel_rows), "indices_documentados": len(db["indexes"]),
                      "restricciones_documentadas": len(db["constraints"]), "diferencias_documentadas": len(differences)}, ensure_ascii=False))


def write_report(snapshot):
    db=snapshot["database"]; orm=snapshot["orm"]; differences=snapshot["differences"]
    groups=Counter(d["category"] for d in differences)
    lines=["# Auditoría de documentación y esquema de base de datos", "", f'Verificación: {snapshot["verified_at"]}.', "",
           "## Resultado", "",
           "Se inspeccionó la DB PostgreSQL configurada mediante catálogos en transacción READ ONLY y REPEATABLE READ. Están presentes las 27 tablas ORM y sus 262 columnas; no hay columnas de negocio faltantes ni columnas físicas de negocio sin documentar. Se incluyó también alembic_version, con una columna técnica, para un total de 28 tablas y 263 columnas. Las 50 FK físicas coinciden en sus extremos con las declaradas, pero tres difieren en ON DELETE.", "",
           "La documentación quedó alineada con el esquema físico consultado. Esto no significa que el ORM y las migraciones hayan sido corregidos: sus diferencias se listan abajo. No se ejecutó DDL ni se modificaron datos o modelos de la aplicación.", "",
           "## Diferencias DB frente al ORM", "",
           table(["Categoría", "Cantidad de diferencias de definición"], sorted(groups.items())), "",
           table(["Tabla", "Aspecto", "Campo u objeto", "ORM", "DB real"], [[d["table"],d["category"],d["field"],d["orm"],d["database"]] for d in differences]), "",
           "Las cantidades son diferencias de propiedades, no 55 errores independientes ni 55 columnas faltantes. Un índice único puede aparecer tanto en unicidad como en índices; los 24 defaults adicionales del servidor se distinguen de los defaults Python y no implican por sí mismos un fallo.", "",
           "## Hallazgos que afectan el diseño", "",
           "- ventas.impuesto_iva y ventas.monto_neto permiten NULL físicamente; el ORM declara nullable=False.",
           "- inventario.sucursal_id, inventario.variante_id y movimientos_inventario.inventario_id usan CASCADE en la DB y RESTRICT en los modelos.",
           "- users.created_at, users.updated_at, token_blacklist.revoked_at y token_blacklist.expires_at son TIMESTAMP WITHOUT TIME ZONE en DB; los modelos declaran zona horaria.",
           "- categorias.nombre, proveedores.nombre, sucursales.nombre y temporadas.nombre son únicos en la DB; sus índices ORM no son únicos. Esa unicidad física es global, no una clave compuesta por tenant.",
           "- Los índices ausentes y los defaults se enumeran exactamente en la tabla anterior; no se han creado ni alterado.", "",
           "## Cobertura de migraciones", "",
           "Se revisaron únicamente las funciones upgrade mediante AST, sin ejecutarlas. La evidencia distingue create_table y add_column; no equivale a ejecutar la historia de migraciones en una DB nueva.", "",
           table(["Tabla ORM", "Migración create_table", "Columnas ORM sin create_table/add_column versionado"], [
               [name, snapshot["migrations"]["created"].get(name,{}).get("source", "No existe"),
                sorted({c["name"] for c in model["columns"]} - set(snapshot["migrations"]["created"].get(name,{}).get("columns",[])) - set(snapshot["migrations"]["added_columns"].get(name,[])))]
               for name,model in orm.items()]), "",
           "El arranque en backend/app/main.py ejecuta Base.metadata.create_all y ALTER TABLE ADD COLUMN IF NOT EXISTS. El script backend/migrate_tax_columns.py añade columnas tributarias. No se ejecutó ninguno de esos caminos en esta auditoría. create_all no armoniza las restricciones ni tipos de tablas ya existentes; ADD COLUMN IF NOT EXISTS tampoco corrige una columna que ya existe con otra definición.", "",
           "## Correcciones respecto del diccionario anterior", "",
           "- Se amplió de 25 a 27 tablas del sistema e incluyó la tabla técnica de Alembic.",
           "- Se agregaron favoritos y tarifas_delivery con todos sus campos, FK e índices.",
           "- Se regeneraron los 263 atributos y las 50 FK desde el catálogo físico, incluidas nulabilidad, PK compuestas, restricciones UNIQUE, defaults y acciones ON DELETE/ON UPDATE.",
           "- Se corrigió el mínimo de hijos: una FK no obliga a que el padre tenga al menos un detalle o variante.",
           "- Se distinguieron columnas persistidas, propiedades calculadas, navegación ORM y relaciones que solo existen como referencia lógica.",
           "- Se actualizaron los tipos ENUM, los diagramas ER y el DBML desde la misma captura para evitar versiones contradictorias.",
           "- Se inventariaron las tres tablas SQLite y los tres stores IndexedDB declarados en el código, separados de PostgreSQL.", "",
           "## Objetos físicos adicionales", "",
           table(["Objeto", "Cantidad"], [[key,len(db[key])] for key in ["constraints","indexes","enums","views","triggers","policies","sequences","routines","extensions"]]), "",
           "Las 28 tablas tienen RLS deshabilitado en esta captura. El aislamiento por empresa implementado en la aplicación no debe confundirse con políticas RLS de PostgreSQL.", "",
           "## Archivos y alcance", "",
           "- [Diccionario completo](../PROMPT_CODEX_DIAGRAMA_BASE_DE_DATOS.md).",
           "- [Captura estructurada](esquema_verificado.json).",
           "- [ER Mermaid](esquema_fisico.mmd).",
           "- [DBML](esquema_fisico.dbml).", "",
           "La captura no contiene URL de conexión, usuarios, contraseñas ni registros comerciales. No se consultaron otras instalaciones o dispositivos. La documentación Word y los diagramas de casos de uso no se modificaron en este paso; sus modelos posteriores deberán usar este esquema verificado.", ""]
    (FOLDER/"AUDITORIA_BASE_DE_DATOS.md").write_text("\n".join(lines),encoding="utf-8")


if __name__=="__main__":
    generate(json.loads((FOLDER/"esquema_verificado.json").read_text(encoding="utf-8")))
