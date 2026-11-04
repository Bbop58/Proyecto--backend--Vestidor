"""Explicit reporting periods; do not silently turn a dated question into all-time totals."""
import re
from datetime import datetime, timezone
import sqlglot
from sqlglot import exp


def apply_report_period(sql, question):
    q=question.lower()
    now=datetime.now(timezone.utc)
    year,month=now.year,now.month
    months=['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
    selected=next((i+1 for i,name in enumerate(months) if name in q),None)
    if selected:
        month=selected
        requested_year=re.search(r'\b(20\d{2})\b',q)
        if requested_year: year=int(requested_year.group(1))
    elif 'mes pasado' in q or 'mes anterior' in q:
        month-=1
        if month==0:year-=1;month=12
    elif not any(term in q for term in ('este mes','del mes','mes actual')):
        return sql
    start=f'{year:04}-{month:02}-01'
    end=f'{year+1:04}-01-01' if month==12 else f'{year:04}-{month+1:02}-01'
    tree=sqlglot.parse_one(sql,read='postgres')
    for table in list(tree.find_all(exp.Table)):
        if table.name.lower()!='ventas':continue
        filtered=sqlglot.parse_one(f"SELECT * FROM ventas WHERE created_at >= TIMESTAMPTZ '{start} 00:00:00+00' AND created_at < TIMESTAMPTZ '{end} 00:00:00+00'",read='postgres').subquery()
        filtered.set('alias',table.args.get('alias').copy() if table.args.get('alias') else exp.TableAlias(this=table.this.copy()))
        table.replace(filtered)
    return tree.sql(dialect='postgres')
