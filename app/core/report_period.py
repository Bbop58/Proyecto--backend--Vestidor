"""Explicit reporting periods; do not silently turn a dated question into all-time totals."""
import re
import calendar
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import sqlglot
from sqlglot import exp
from fastapi import HTTPException

_NUMBER_WORDS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10, "once": 11, "doce": 12,
}
_MONTH_NAMES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def ensure_supported_report_period(question):
    """Reject explicit periods outside the current contract instead of using all history."""
    q = _normalized_question(question)
    has_explicit_date = bool(re.search(
        r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/](?:20)?\d{2})\b", q
    ))
    month_named = any(term in q for term in _MONTH_NAMES)
    named_month_count = sum(term in q for term in _MONTH_NAMES)
    month_range = (
        bool(re.search(r"\b(?:desde|entre|hasta)\b", q))
        or named_month_count > 1
        or bool(re.search(r"\bde(?:l)?\b.*\b(?:hasta|al|a|y)\b", q))
    )
    explicit_year = bool(re.search(r"\b20\d{2}\b", q))
    has_date_range = has_explicit_date or (month_named and month_range)
    has_unsupported_period = any(term in q for term in (
        "trimestre", "trimestral", "semestre", "semestral", "periodo fiscal",
    )) or bool(re.search(r"\b(?:ano|anos)\s+(?:pasado|anterior|fiscal)\b", q))
    # The current intent contract has no arbitrary-year or rolling-year field.
    # Do not let the classifier quietly turn these into all-time queries.
    has_unsupported_year = explicit_year or bool(re.search(
        r"\bultim(?:o|a)s?\s+(?:(?:un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|\d+)\s+)?anos?\b",
        q,
    ))
    if has_date_range or has_unsupported_period or has_unsupported_year:
        raise HTTPException(
            status_code=422,
            detail="Ese periodo aún no está disponible. Usa histórico, últimos días/semanas/meses, mes actual/anterior o año actual.",
        )


def parse_relative_period(question):
    """Return (count, unit) for common spoken Spanish periods, if present."""
    q = _normalized_question(question)
    match = re.search(
        r"\bultim(?:o|a)s?\s+(\d{1,3}|un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)\s+(dia(?:s)?|semana(?:s)?|mes(?:es)?)\b",
        q,
    )
    if not match:
        return None
    raw_count, raw_unit = match.groups()
    count = int(raw_count) if raw_count.isdigit() else _NUMBER_WORDS[raw_count]
    unit = "day" if raw_unit.startswith("dia") else "week" if raw_unit.startswith("semana") else "month"
    return count, unit


def _normalized_question(question):
    return "".join(
        character for character in unicodedata.normalize("NFKD", question.lower())
        if not unicodedata.combining(character)
    )


def _subtract_months(value, months):
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _apply_sales_range(sql, start, end):
    start_literal = start.strftime("%Y-%m-%d %H:%M:%S.%f%z")
    end_literal = end.strftime("%Y-%m-%d %H:%M:%S.%f%z")
    tree = sqlglot.parse_one(sql, read="postgres")
    for table in list(tree.find_all(exp.Table)):
        if table.name.lower() != "ventas":
            continue
        filtered = sqlglot.parse_one(
            "SELECT * FROM ventas "
            f"WHERE created_at >= TIMESTAMPTZ '{start_literal}' "
            f"AND created_at < TIMESTAMPTZ '{end_literal}'",
            read="postgres",
        ).subquery()
        filtered.set(
            "alias",
            table.args.get("alias").copy()
            if table.args.get("alias")
            else exp.TableAlias(this=table.this.copy()),
        )
        table.replace(filtered)
    return tree.sql(dialect="postgres")


def apply_report_period(sql, question):
    q = _normalized_question(question)
    now=datetime.now(ZoneInfo("America/La_Paz"))

    relative = parse_relative_period(q)
    if relative:
        amount, unit = relative
        maximum = {"day": 366, "week": 52, "month": 36}[unit]
        if amount < 1 or amount > maximum:
            raise HTTPException(
                status_code=422,
                detail=f"El periodo máximo para reportes es de {maximum} {unit}(s). Indica un intervalo menor.",
            )
        if unit == "month":
            start = _subtract_months(now, amount)
        else:
            from datetime import timedelta
            start = now - timedelta(days=amount * (7 if unit == "week" else 1))
        return _apply_sales_range(sql, start, now)

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
        filtered=sqlglot.parse_one(f"SELECT * FROM ventas WHERE (created_at AT TIME ZONE 'America/La_Paz') >= TIMESTAMP '{start} 00:00:00' AND (created_at AT TIME ZONE 'America/La_Paz') < TIMESTAMP '{end} 00:00:00'",read='postgres').subquery()
        filtered.set('alias',table.args.get('alias').copy() if table.args.get('alias') else exp.TableAlias(this=table.this.copy()))
        table.replace(filtered)
    return tree.sql(dialect='postgres')
