import re
import io
import time
import logging
import unicodedata
from decimal import Decimal
from uuid import UUID
from datetime import datetime, date
from typing import Dict, Any, List, Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import HTTPException, status
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.schemas.voice_report import VoiceReportQueryResponse
from app.schemas.report_intent import ReportIntent
from app.services.report_intent_service import ReportIntentService
from app.services.report_query_builder import ReportQueryBuilder

logger = logging.getLogger("voice_report_service")

# Palabras clave y sentencias destructivas prohibidas
FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE|EXEC|EXECUTE|COPY|LOCK|VACUUM|REINDEX|PG_|INFORMATION_SCHEMA)\b",
    re.IGNORECASE
)


class VoiceReportService:

    @classmethod
    def _match_canonical_exam_query(cls, question: str) -> Optional[Tuple[str, str]]:
        """
        Reconoce las consultas canónicas explícitamente requeridas en el Examen Parcial 2 (Sección 8.2).
        Garantiza respuesta determinista y perfecta incluso si la API de Gemini no tuviese cuota o red.
        """
        q = "".join(
            character for character in unicodedata.normalize("NFKD", question.lower().strip())
            if not unicodedata.combining(character)
        )

        asks_top_products = cls._is_top_products_request(q)
        asks_per_branch = "sucursal" in q and any(term in q for term in (
            "por cada", "por sucursal", "cada sucursal", "agrupad",
        ))

        # A per-branch top is a different report from the global top with a
        # branch column. Rank each branch independently; never aggregate branch
        # names into one row for this request.
        if asks_top_products and asks_per_branch:
            top_n = cls._requested_top_n(q, default=10)
            variant_level = any(term in q for term in ("sku", "talla", "color", "variante"))
            product_columns = "p.nombre AS producto, "
            variant_columns = "vp.sku, vp.talla, vp.color, " if variant_level else ""
            group_columns = "p.id, p.nombre"
            if variant_level:
                group_columns += ", vp.id, vp.sku, vp.talla, vp.color"
            group_columns += ", s.id, s.nombre"
            variant_tie_break = ", vp.sku ASC" if variant_level else ""
            ranking_metric = "unidades_vendidas"
            ranking_expression = "SUM(dv.cantidad)"
            if any(term in q for term in ("por ingresos", "mayor ingreso", "mas ingresos", "mayores ingresos")):
                ranking_metric = "ingresos_generados_bs"
                ranking_expression = "SUM(dv.subtotal)"
            sql = (
                "SELECT ranked.* FROM ("
                "SELECT s.nombre AS sucursal, " + product_columns + variant_columns +
                "COALESCE(SUM(dv.cantidad), 0) AS unidades_vendidas, "
                "COALESCE(SUM(dv.subtotal), 0) AS ingresos_generados_bs, "
                "ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY " + ranking_expression +
                " DESC, p.nombre ASC, p.id ASC" + variant_tie_break + ") AS posicion_sucursal "
                "FROM detalles_venta dv "
                "JOIN variantes_producto vp ON dv.variante_id = vp.id "
                "JOIN productos p ON vp.producto_id = p.id "
                "JOIN ventas v ON dv.venta_id = v.id "
                "JOIN sucursales s ON v.sucursal_id = s.id "
                "WHERE v.estado = 'COMPLETADA' "
                "GROUP BY " + group_columns +
                ") AS ranked "
                f"WHERE posicion_sucursal <= {top_n} "
                "ORDER BY sucursal ASC, posicion_sucursal ASC"
            )
            grain = "variantes (SKU, talla y color)" if variant_level else "productos (tallas y colores sumados)"
            metric_label = "ingresos generados" if ranking_metric == "ingresos_generados_bs" else "unidades vendidas"
            interp = f"Top {top_n} de {grain} por {metric_label}, calculado por separado para cada sucursal."
            return sql, interp

        # R1: Ventas por sucursal. Do not swallow time-series requests before R3.
        asks_time_series = any(term in q for term in (
            "por semana", "semanal", "por mes", "mensual", "por dia", "diario",
            "grafico", "evolucion", "tendencia",
        ))
        if not asks_time_series and "sucursal" in q and any(w in q for w in ["ventas", "agrupadas", "septiembre", "total"]):
            sql = (
                "SELECT s.nombre AS sucursal, "
                "COUNT(v.id) AS total_ventas, "
                "COALESCE(SUM(v.monto_total), 0) AS total_recaudado_bs, "
                "COALESCE(SUM(v.impuesto_iva), 0) AS total_iva_bs, "
                "COALESCE(SUM(v.monto_neto), 0) AS total_neto_bs "
                "FROM sucursales s "
                "LEFT JOIN ventas v ON v.sucursal_id = s.id AND v.estado = 'COMPLETADA' "
                "GROUP BY s.nombre "
                "ORDER BY total_recaudado_bs DESC"
            )
            interp = "Reporte canónico: Ventas totales e ingresos netos desglosados y agrupados por sucursal."
            return sql, interp

        # R2: Top productos más vendidos
        if cls._is_top_products_request(q):
            top_n = cls._requested_top_n(q, default=10)
            income_ranking = any(term in q for term in ("por ingresos", "mayor ingreso", "mas ingresos", "mayores ingresos"))
            ranking_expression = "SUM(dv.subtotal)" if income_ranking else "SUM(dv.cantidad)"
            variant_level = any(term in q for term in ("sku", "talla", "color", "variante"))
            detail_columns = "vp.sku, vp.talla, vp.color, " if variant_level else ""
            detail_group = ", vp.id, vp.sku, vp.talla, vp.color" if variant_level else ""
            rank_tie = ", vp.sku ASC" if variant_level else ""
            sql = (
                "SELECT p.nombre AS producto, " + detail_columns +
                "STRING_AGG(DISTINCT s.nombre, ', ' ORDER BY s.nombre) AS sucursal, "
                "COALESCE(SUM(dv.cantidad), 0) AS unidades_vendidas, "
                "COALESCE(SUM(dv.subtotal), 0) AS ingresos_generados_bs "
                "FROM detalles_venta dv "
                "JOIN variantes_producto vp ON dv.variante_id = vp.id "
                "JOIN productos p ON vp.producto_id = p.id "
                "JOIN ventas v ON dv.venta_id = v.id "
                "JOIN sucursales s ON v.sucursal_id = s.id "
                "WHERE v.estado = 'COMPLETADA' "
                "GROUP BY p.id, p.nombre" + detail_group + " "
                "ORDER BY " + ranking_expression + " DESC, p.nombre ASC" + rank_tie + " "
                f"LIMIT {top_n}"
            )
            metric_text = "ingresos generados por prendas" if income_ranking else "unidades vendidas"
            grain = "variantes" if variant_level else "productos (tallas y colores sumados)"
            interp = (
                f"Top {top_n} de {grain} por {metric_text}. La columna sucursal indica "
                "las sucursales donde se vendió cada variante durante el periodo solicitado."
            )
            return sql, interp

        # R3: Gráfico / evolución de ventas por semana
        asks_weekly_series = any(term in q for term in (
            "por semana", "semanal", "grafico", "evolucion", "tendencia semanal",
        ))
        if asks_weekly_series and any(w in q for w in ["venta", "ingreso", "recaud"]):
            sql = (
                "SELECT TO_CHAR(DATE_TRUNC('week', v.created_at), 'YYYY-\"Semana\" IW') AS periodo_semanal, "
                "COUNT(v.id) AS cantidad_ventas, "
                "COALESCE(SUM(v.monto_total), 0) AS total_ventas_bs "
                "FROM ventas v "
                "WHERE v.estado = 'COMPLETADA' "
                "GROUP BY periodo_semanal "
                "ORDER BY periodo_semanal ASC"
            )
            interp = "Reporte canónico: Serie temporal de ventas y facturación agrupada por semana."
            return sql, interp

        # R4: Categorías con mayores ingresos / Comparativa
        if any(w in q for w in ["categoría", "categoria"]) and any(w in q for w in ["mayor", "mas", "comparativa", "ingreso", "vendid"]):
            sql = (
                "SELECT c.nombre AS categoria, "
                "COUNT(DISTINCT v.id) AS transacciones_realizadas, "
                "COALESCE(SUM(dv.cantidad), 0) AS prendas_vendidas, "
                "COALESCE(SUM(dv.subtotal), 0) AS ingresos_totales_bs "
                "FROM detalles_venta dv "
                "JOIN variantes_producto vp ON dv.variante_id = vp.id "
                "JOIN productos p ON vp.producto_id = p.id "
                "JOIN categorias c ON p.categoria_id = c.id "
                "JOIN ventas v ON dv.venta_id = v.id "
                "WHERE v.estado = 'COMPLETADA' "
                "GROUP BY c.nombre "
                "ORDER BY ingresos_totales_bs DESC"
            )
            interp = "Reporte canónico: Comparativa de categorías con mayor generación de ingresos y unidades vendidas."
            return sql, interp

        # R5: Productos con menor existencia / stock crítico
        if any(w in q for w in ["menor existencia", "menor nivel", "nivel de existencia", "existencia", "stock bajo", "poca existencia", "agotados"]):

            sql = (
                "SELECT p.nombre AS producto, "
                "vp.sku, "
                "s.nombre AS sucursal, "
                "i.stock_actual, "
                "i.stock_minimo "
                "FROM inventario i "
                "JOIN variantes_producto vp ON i.variante_id = vp.id "
                "JOIN productos p ON vp.producto_id = p.id "
                "JOIN sucursales s ON i.sucursal_id = s.id "
                "WHERE i.activo = TRUE "
                "ORDER BY i.stock_actual ASC "
                "LIMIT 15"
            )
            interp = "Reporte canónico: Prendas con menor nivel de existencia física en almacén."
            return sql, interp

        return None

    @staticmethod
    def _requested_top_n(normalized_question: str, default: int = 10) -> int:
        match = re.search(r"\btop\s+(\d{1,2})\b", normalized_question)
        if not match:
            match = re.search(r"\b(\d{1,2})\s+(?:productos|prendas|variantes)\b", normalized_question)
        if match:
            return max(1, min(int(match.group(1)), 50))
        word_numbers = {
            "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
            "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
            "diez": 10, "once": 11, "doce": 12, "quince": 15, "veinte": 20,
        }
        word_match = re.search(r"\btop\s+(un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|quince|veinte)\b", normalized_question)
        if word_match:
            return word_numbers[word_match.group(1)]
        for word, value in word_numbers.items():
            if re.search(rf"\b{word}\s+(?:productos|prendas|variantes)\b", normalized_question):
                return value
        return default

    @staticmethod
    def _is_top_products_request(normalized_question: str) -> bool:
        has_product = any(term in normalized_question for term in ("producto", "prenda", "variante"))
        has_ranking = any(term in normalized_question for term in (
            "top ", "mas vendid", "mas ingres", "mayores ingres", "mayor ingreso",
        ))
        return has_product and has_ranking

    @classmethod
    def _criteria_for_question(cls, question: str) -> List[str]:
        q = "".join(
            character for character in unicodedata.normalize("NFKD", question.lower().strip())
            if not unicodedata.combining(character)
        )
        period = cls._question_period_label(q)
        top_n = cls._requested_top_n(q)
        asks_top_products = cls._is_top_products_request(q)
        asks_per_branch = "sucursal" in q and any(term in q for term in (
            "por cada", "por sucursal", "cada sucursal", "agrupad",
        ))
        if asks_top_products:
            variant_level = any(term in q for term in ("sku", "talla", "color", "variante"))
            metric = "ingresos generados" if any(term in q for term in (
                "por ingresos", "mayor ingreso", "mas ingresos", "mayores ingresos",
            )) else "unidades vendidas"
            grain = "variante (SKU, talla y color)" if variant_level else "producto (tallas y colores sumados)"
            scope = "por sucursal" if asks_per_branch else "global"
            criteria = [f"Métrica: {metric}", f"Periodo: {period}", "Estado: ventas completadas"]
            if asks_per_branch:
                criteria.append("Agrupación: sucursal")
            criteria.extend([f"Ranking: top {top_n} {scope}", f"Detalle: {grain}"])
            return criteria
        if "sucursal" in q and any(term in q for term in ("venta", "ingreso", "recaud")):
            return [
                "Métrica: cantidad de ventas e importes", f"Periodo: {period}",
                "Estado: ventas completadas", "Agrupación: sucursal",
            ]
        return []

    @staticmethod
    def _question_period_label(normalized_question: str) -> str:
        from app.core.report_period import parse_relative_period
        relative = parse_relative_period(normalized_question)
        if relative:
            amount, unit = relative
            if unit == "day":
                return f"Últimos {amount} día{'s' if amount != 1 else ''}"
            if unit == "week":
                return f"Últimas {amount} semana{'s' if amount != 1 else ''}"
            return f"Últimos {amount} mes{'es' if amount != 1 else ''}"
        if "mes pasado" in normalized_question or "mes anterior" in normalized_question:
            return "Mes anterior"
        if any(term in normalized_question for term in ("este mes", "del mes", "mes actual")):
            return "Mes actual"
        month_names = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
        for month in month_names:
            if re.search(rf"\b{month}\b", normalized_question):
                year_match = re.search(r"\b(20\d{2})\b", normalized_question)
                return month.capitalize() + (f" {year_match.group(1)}" if year_match else "")
        if "este ano" in normalized_question or "ano actual" in normalized_question:
            return "Año actual"
        return "Todo el histórico"

    @staticmethod
    def _period_label(intent: ReportIntent) -> str:
        period = intent.period
        if period.kind == "all_time":
            return "Todo el histórico"
        if period.kind == "last_days":
            return f"Últimos {period.amount} días"
        if period.kind == "last_weeks":
            return f"Últimas {period.amount} semanas"
        if period.kind == "last_months":
            return f"Últimos {period.amount} meses"
        return {
            "this_month": "Mes actual",
            "last_month": "Mes anterior",
            "this_year": "Año actual",
        }.get(period.kind, "Periodo no especificado")

    @classmethod
    def _prepare_report_query(cls, question: str) -> tuple[str, str, dict[str, Any], List[str]]:
        """Canonical reports bypass Gemini; all other questions use typed intent + allowlisted SQL."""
        from app.core.report_period import ensure_supported_report_period
        ensure_supported_report_period(question)
        canonical = cls._match_canonical_exam_query(question)
        if canonical:
            sql, interpretation = canonical
            return sql, interpretation, {}, cls._criteria_for_question(question)

        intent = ReportIntentService.interpret(question)
        sql, params, metric_label = ReportQueryBuilder.build(intent)
        display_dimensions = list(intent.dimensions)
        if intent.detail == "variant" and "variant" not in display_dimensions:
            display_dimensions.append("variant")
        dimensions = [
            {"branch": "sucursal", "product": "producto", "variant": "variante (SKU, talla y color)",
             "category": "categoría", "payment_method": "método de pago", "sale_type": "tipo de venta",
             "sale_status": "estado de venta", "reservation_status": "estado de reserva", "customer": "cliente",
             "day": "día", "week": "semana", "month": "mes"}[dimension]
            for dimension in display_dimensions
        ]
        if intent.ranking:
            ranking_text = f"Top {intent.ranking.top_n}"
            ranking_text += f" por {dimensions_map}" if (dimensions_map := {
                "branch": "sucursal", "category": "categoría", "month": "mes", "week": "semana",
                "day": "día", "payment_method": "método de pago", "product": "producto",
                "variant": "variante", "customer": "cliente", "sale_type": "tipo de venta",
                "sale_status": "estado de venta", "reservation_status": "estado de reserva",
            }.get(intent.ranking.within or "")) else " global"
        else:
            ranking_text = None
        period_label = "Existencias actuales" if intent.domain == "inventory" else cls._period_label(intent)
        criteria = [f"Métrica: {metric_label}", f"Periodo: {period_label}"]
        if intent.domain == "sales" and "sale_status" not in intent.dimensions:
            criteria.append("Estado: ventas completadas")
        if dimensions:
            criteria.append("Agrupación: " + ", ".join(dimensions))
        if ranking_text:
            criteria.append("Ranking: " + ranking_text.strip())
        if intent.filters:
            criteria.append("Filtros: " + ", ".join(f"{item.field} = {item.value}" for item in intent.filters))
        interpretation = f"{metric_label.capitalize()}" + (f" agrupado por {', '.join(dimensions)}" if dimensions else "")
        if ranking_text:
            interpretation += f"; {ranking_text.strip()}"
        interpretation += f"; {period_label.lower()}."
        if intent.filters:
            interpretation += " Filtros: " + ", ".join(item.value for item in intent.filters) + "."
        return sql, interpretation, params, criteria

    @classmethod
    def _generate_sql_with_gemini(cls, question: str, limit: int = 100) -> Tuple[str, str]:
        """
        Compatibilidad para consumidores internos: Gemini devuelve intención tipada,
        y el servidor construye SQL desde el catálogo permitido.
        """
        sql, interpretation, _params, _criteria = cls._prepare_report_query(question)
        return sql, interpretation

    @classmethod
    def _validate_sql_security(cls, sql: str) -> str:
        """
        Validador defensivo para SQL construido por el catálogo aprobado.
        Garantiza que la consulta sea exclusivamente de solo lectura (SELECT).
        """
        clean_sql = sql.strip()

        # 1. Quitar punto y coma final
        if clean_sql.endswith(";"):
            clean_sql = clean_sql[:-1].strip()

        # 2. Rechazar múltiples consultas encadenadas
        if ";" in clean_sql:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Por seguridad no se permiten múltiples consultas SQL encadenadas."
            )

        # 3. Debe comenzar estrictamente con SELECT o WITH
        if not re.match(r"^(SELECT|WITH)\b", clean_sql, re.IGNORECASE):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Consulta no autorizada: Solo se permiten consultas de tipo SELECT."
            )

        # 4. Verificar ausencia de sentencias DDL/DML destructivas
        forbidden_match = FORBIDDEN_SQL_PATTERN.search(clean_sql)
        if forbidden_match:
            keyword = forbidden_match.group(0)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Consulta rechazada por seguridad: Contiene palabra clave prohibida '{keyword}'."
            )

        return clean_sql

    @classmethod
    def _serialize_value(cls, val: Any) -> Any:
        """Serializa tipos complejos de PostgreSQL a tipos compatibles con JSON."""
        if isinstance(val, (datetime, date)):
            return val.isoformat()
        elif isinstance(val, Decimal):
            return float(val)
        elif isinstance(val, UUID):
            return str(val)
        elif isinstance(val, bytes):
            return val.decode("utf-8", errors="ignore")
        return val

    @classmethod
    def _build_speech_summary(
        cls,
        question: str,
        interpretation: str,
        columns: List[str],
        rows: List[Dict[str, Any]],
        sql: Optional[str] = None,
    ) -> str:
        """
        Genera un resumen narrativo de alto nivel (Audio Briefing) optimizado
        para ser locutado por síntesis de voz (TTS).
        Sintetiza totales, elementos líderes y hallazgos clave sin tecnicismos.
        """
        if not rows:
            return "Reporte generado exitosamente. No se encontraron registros para los criterios solicitados."

        interpretation_lower = interpretation.lower()
        if (
            "sucursal" in columns and "producto" in columns
            and ("por sucursal" in interpretation_lower or "por separado para cada sucursal" in interpretation_lower)
        ):
            metric_column = next((name for name in ("unidades_vendidas", "ingresos_generados_bs", "ingresos_prendas_bs") if name in columns), None)
            metric_label = "unidades vendidas" if metric_column == "unidades_vendidas" else "ingresos generados"
            examples = []
            seen_branches = set()
            for row in rows:
                branch = str(row.get("sucursal", ""))
                if not branch or branch in seen_branches:
                    continue
                seen_branches.add(branch)
                value = row.get(metric_column) if metric_column else None
                value_text = (
                    f" ({value} unidades)" if metric_column == "unidades_vendidas" and value is not None
                    else f" (Bs. {float(value):,.2f})" if value is not None
                    else ""
                )
                examples.append(f"{branch}: {row.get('producto', 'sin nombre')}{value_text}")
                if len(examples) == 3:
                    break
            return (
                f"Top calculado por separado en cada sucursal según {metric_label}. "
                + "; ".join(examples)
                + f". La tabla contiene {len(rows)} resultados."
            )

        total_rows = len(rows)
        # Identificar columnas cuantitativas clave
        money_col = None
        qty_col = None
        label_col = None

        # Counts called total_ventas are not currency. Prefer explicitly
        # monetary columns regardless of their position in the result.
        columns_by_priority=sorted(columns,key=lambda col: 0 if any(w in col.lower() for w in ['recaudado','ingresos','monto','_bs']) else 1)
        for col in columns_by_priority:
            col_l = col.lower()
            if not money_col and any(w in col_l for w in ["total_recaudado", "total_ingresos", "monto_total", "ingresos", "monto_neto", "total_neto", "_bs"]):
                money_col = col
            elif not qty_col and any(w in col_l for w in ["unidades", "cantidad", "total_ventas", "pedidos", "stock", "existencia"]):
                qty_col = col
            elif not label_col and any(w in col_l for w in ["producto", "sucursal", "nombre", "categoria", "cliente", "periodo"]):
                label_col = col

        qty_unit = 'unidades'
        if sql:
            from app.core.report_metrics import metric_units
            units = metric_units(sql)
            money_col = next((col for col in columns if units.get(col.lower()) == 'Bolivianos'), None)
            qty_col = next((col for col in columns if units.get(col.lower()) in ('unidades', 'registros')), None)
            if qty_col:
                qty_unit = units[qty_col.lower()]

        # Si aún no tenemos label_col, tomar la primera columna
        if not label_col and columns:
            label_col = next((col for col in columns if isinstance(rows[0].get(col), str)), None)

        summary_parts = []

        # 1. Apertura con cantidad de elementos
        entity_name = "registros"
        if label_col:
            lbl = label_col.lower()
            if "sucursal" in lbl:
                entity_name = "sucursales"
            elif "producto" in lbl or "prenda" in lbl:
                entity_name = "productos"
            elif "cliente" in lbl:
                entity_name = "clientes"
            elif "categoria" in lbl:
                entity_name = "categorías"
            elif "semana" in lbl or "periodo" in lbl:
                entity_name = "períodos semanales"

        summary_parts.append(f"Reporte generado exitosamente con {total_rows} {entity_name}.")

        # 2. Métricas acumuladas si existen
        total_money = 0.0
        total_qty = 0
        if money_col:
            for r in rows:
                val = r.get(money_col)
                if isinstance(val, (int, float)):
                    total_money += float(val)
            if total_money > 0:
                summary_parts.append(f"La facturación acumulada suma {total_money:,.2f} Bolivianos.")

        if qty_col and qty_col != money_col:
            for r in rows:
                val = r.get(qty_col)
                if isinstance(val, (int, float)):
                    total_qty += int(val)
            if total_qty > 0 and (sql or "unidades" in qty_col.lower() or "cantidad" in qty_col.lower()):
                summary_parts.append(f"Se registraron {total_qty:,} {qty_unit} en total.")

        # 3. Elementos líderes o destacados
        if label_col and total_rows > 0:
            top_1 = rows[0]
            top_1_name = str(top_1.get(label_col, ''))
            top_1_val = ""
            if money_col and top_1.get(money_col) is not None:
                top_1_val = f" con {float(top_1.get(money_col, 0)):,.2f} Bolivianos"
            elif qty_col and top_1.get(qty_col) is not None:
                top_1_val = f" con {top_1.get(qty_col)} {qty_unit}"

            if top_1_name:
                if total_rows > 1:
                    top_2 = rows[1]
                    top_2_name = str(top_2.get(label_col, ''))
                    top_2_val = ""
                    if money_col and top_2.get(money_col) is not None:
                        top_2_val = f" ({float(top_2.get(money_col, 0)):,.2f} Bolivianos)"
                    elif qty_col and top_2.get(qty_col) is not None:
                        top_2_val = f" ({top_2.get(qty_col)} {qty_unit})"

                    summary_parts.append(f"El puesto principal lo lidera {top_1_name}{top_1_val}, seguido por {top_2_name}{top_2_val}.")
                else:
                    summary_parts.append(f"El registro destacado es {top_1_name}{top_1_val}.")

        resumen = " ".join(summary_parts)
        # Limpieza de caracteres para que la voz fluya perfectamente
        resumen = resumen.replace("Bs.", "Bolivianos").replace("Bs", "Bolivianos")
        resumen = re.sub(r"[\*#_`]", "", resumen)
        return resumen

    @classmethod
    def execute_voice_report(
        cls,
        db: Session,
        question: str,
        limit: int = 100
    ) -> VoiceReportQueryResponse:
        """
        Flujo de ejecución con consultas SELECT validadas:
        1. Lenguaje natural -> intención tipada (Gemini) -> SQL del catálogo aprobado.
        2. Validación estricta de seguridad.
        3. Ámbito de empresa obligatorio y SET LOCAL statement_timeout = 5000.
        4. Serialización de filas y columnas.
        5. Generación de resumen narrativo hablado (Audio Briefing).
        """
        start_time = time.time()

        # Validación inmediata de seguridad sobre la entrada en lenguaje natural
        destructive_pattern = re.compile(
            r"\b(DROP\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+\s+SET|INSERT\s+INTO|ALTER\s+TABLE|TRUNCATE|GRANT|REVOKE)\b",
            re.IGNORECASE
        )
        if destructive_pattern.search(question):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pregunta rechazada por motivos de seguridad: contiene instrucciones destructivas o inyección SQL no permitida."
            )

        raw_sql, interpretation, query_params, criteria = cls._prepare_report_query(question)
        # Canonical SQL still uses the shared date resolver; structured queries
        # already receive their period as bound SQL criteria from the builder.
        if cls._match_canonical_exam_query(question):
            from app.core.report_period import apply_report_period
            raw_sql = apply_report_period(raw_sql, question)
        validated_sql = cls._validate_sql_security(raw_sql)

        from app.core.tenant_sql import scope_report_sql
        validated_sql = scope_report_sql(validated_sql,db,db.info.get('tenant_scope'))
        # An inner LIMIT (e.g. in a subquery) is not a limit on this report.
        import sqlglot
        from sqlglot import exp
        tree=sqlglot.parse_one(validated_sql,read='postgres')
        existing=tree.args.get('limit')
        bounded=max(1,min(int(limit),500))
        if existing and isinstance(existing.expression,exp.Literal):
            bounded=min(bounded,int(existing.expression.this))
        validated_sql=tree.limit(bounded).sql(dialect='postgres')

        logger.info(f"Ejecutando SQL validado para reporte de voz: {validated_sql}")

        try:
            # SELECT is restricted by the AST allowlist; bound this transaction's execution time.
            db.execute(text("SET LOCAL statement_timeout = 5000"))
            result = db.execute(text(validated_sql), query_params)

            columns = list(result.keys()) if result.returns_rows else []
            rows: List[Dict[str, Any]] = []

            if result.returns_rows:
                for row in result.fetchall():
                    row_dict = {}
                    for col, val in zip(columns, row):
                        row_dict[col] = cls._serialize_value(val)
                    rows.append(row_dict)

            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            speech_summary = cls._build_speech_summary(question, interpretation, columns, rows, sql=validated_sql)
            logger.info(f"Reporte de voz ejecutado con éxito: {len(rows)} filas en {elapsed_ms}ms")

            return VoiceReportQueryResponse(
                pregunta=question,
                interpretacion=interpretation,
                sql_generado=validated_sql,
                columnas=columns,
                filas=rows,
                total_filas=len(rows),
                tiempo_ms=elapsed_ms,
                resumen_hablado=speech_summary,
                criterios_aplicados=criteria,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error al ejecutar consulta SQL: {e}", exc_info=True)
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La consulta no pudo ejecutarse. Revisa los criterios e intenta nuevamente."
            )

    @classmethod
    def generate_excel_report(
        cls,
        question: str,
        interpretation: str,
        columns: List[str],
        rows: List[Dict[str, Any]]
    ) -> bytes:
        """
        Genera un archivo binario Excel (.xlsx) corporativo profesional con:
        - Membrete y metadatos del reporte.
        - Encabezados estilizados con fondo slate (#334155) y texto blanco.
        - Formato de moneda en Bolivianos (Bs. #,##0.00) y de enteros.
        - Fila final de totales automáticos con fórmula =SUM(...).
        - Auto-ajuste de ancho de columnas y líneas de cuadrícula activas.
        """
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Reporte Ejecutivo"
        ws.views.sheetView[0].showGridLines = True

        # Paleta corporativa
        font_title = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
        fill_title = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")

        font_sub = Font(name="Calibri", size=10, italic=True, color="334155")
        font_meta = Font(name="Calibri", size=9, color="64748B")

        font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        fill_header = PatternFill(start_color="334155", end_color="334155", fill_type="solid")

        font_data = Font(name="Calibri", size=10, color="0F172A")
        fill_zebra = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

        font_total = Font(name="Calibri", size=11, bold=True, color="0F172A")
        fill_total = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")

        thin_side = Side(border_style="thin", color="CBD5E1")
        border_cell = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
        double_bottom = Side(border_style="double", color="0F172A")
        border_total = Border(top=thin_side, bottom=double_bottom, left=thin_side, right=thin_side)

        num_cols = max(len(columns), 1)

        # 1. Membrete
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=num_cols)
        title_cell = ws.cell(row=1, column=1, value="FICCT STORE — REPORTE GERENCIAL INTELIGENTE")
        title_cell.font = font_title
        title_cell.fill = fill_title
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 36

        # Subtítulo con la pregunta
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=num_cols)
        sub_cell = ws.cell(row=2, column=1, value=f'Consulta: "{question}"')
        sub_cell.font = font_sub
        sub_cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[2].height = 20

        # Interpretación
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=num_cols)
        interp_cell = ws.cell(row=3, column=1, value=f"Interpretación: {interpretation}")
        interp_cell.font = font_meta
        interp_cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[3].height = 20

        # Fecha y registros
        ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=num_cols)
        time_cell = ws.cell(row=4, column=1, value=f"Emitido el: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} | Total de filas: {len(rows)}")
        time_cell.font = font_meta
        time_cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[4].height = 18

        ws.row_dimensions[5].height = 8

        # 2. Encabezados de Tabla (Fila 6)
        ws.row_dimensions[6].height = 26
        for col_idx, col_name in enumerate(columns, start=1):
            cell = ws.cell(row=6, column=col_idx, value=col_name.replace("_", " ").upper())
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border_cell

        # 3. Detectar columnas monetarias y numéricas
        numeric_cols = set()
        currency_cols = set()

        for col_idx, col_name in enumerate(columns, start=1):
            c_lower = col_name.lower()
            if any(k in c_lower for k in ["total", "ingreso", "precio", "monto", "neto", "iva", "subtotal", "dinero", "valor", "recaud"]):
                currency_cols.add(col_idx)
                numeric_cols.add(col_idx)
            elif any(k in c_lower for k in ["cantidad", "unidades", "compras", "stock", "numero", "pedidos", "veces"]):
                numeric_cols.add(col_idx)

        # 4. Filas de datos
        current_row = 7
        for r_idx, row_dict in enumerate(rows):
            ws.row_dimensions[current_row].height = 20
            is_even = (r_idx % 2 == 0)
            row_fill = None if is_even else fill_zebra

            for col_idx, col_name in enumerate(columns, start=1):
                val = row_dict.get(col_name)
                cell = ws.cell(row=current_row, column=col_idx)

                if val is not None:
                    if isinstance(val, (int, float, Decimal)):
                        cell.value = float(val) if isinstance(val, Decimal) else val
                        if col_idx in currency_cols:
                            cell.number_format = '"Bs." #,##0.00'
                            cell.alignment = Alignment(horizontal="right", vertical="center")
                        else:
                            cell.number_format = '#,##0'
                            cell.alignment = Alignment(horizontal="right", vertical="center")
                    else:
                        cell.value = str(val)
                        cell.data_type = 's'
                        cell.alignment = Alignment(horizontal="left", vertical="center")
                else:
                    cell.value = "-"
                    cell.alignment = Alignment(horizontal="center", vertical="center")

                cell.font = font_data
                if row_fill:
                    cell.fill = row_fill
                cell.border = border_cell

            current_row += 1

        # 5. Fila de Totales con fórmula =SUM(...)
        if rows and numeric_cols:
            ws.row_dimensions[current_row].height = 24
            for col_idx in range(1, num_cols + 1):
                cell = ws.cell(row=current_row, column=col_idx)
                cell.font = font_total
                cell.fill = fill_total
                cell.border = border_total

                if col_idx == 1:
                    cell.value = "TOTAL GENERAL"
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif col_idx in numeric_cols:
                    col_letter = get_column_letter(col_idx)
                    cell.value = f"=SUM({col_letter}7:{col_letter}{current_row - 1})"
                    if col_idx in currency_cols:
                        cell.number_format = '"Bs." #,##0.00'
                    else:
                        cell.number_format = '#,##0'
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                else:
                    cell.value = ""

        # Auto-ajuste de ancho de columnas
        for col_idx in range(1, num_cols + 1):
            col_letter = get_column_letter(col_idx)
            max_len = 12
            for row in range(6, current_row + 1):
                val = ws.cell(row=row, column=col_idx).value
                if val:
                    val_str = str(val)
                    if len(val_str) > max_len:
                        max_len = min(len(val_str), 40)
            ws.column_dimensions[col_letter].width = max_len + 5

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue()
