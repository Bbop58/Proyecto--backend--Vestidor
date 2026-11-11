"""Build bounded parameterized report SQL from the approved intent catalog."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.schemas.report_intent import ReportIntent


class ReportQueryBuilder:
    ENUM_FILTER_VALUES = {
        "payment_method": {"EFECTIVO", "PAYPAL", "BILLETERA", "TARJETA", "QR"},
        "sale_status": {"PENDIENTE_PAGO", "COMPLETADA", "CANCELADA"},
        "sale_type": {"PRESENCIAL", "DIGITAL"},
        "reservation_status": {"PENDIENTE", "PREPARADA", "RECOGIDA", "CANCELADA", "EXPIRADA", "COMPLETADA"},
    }
    DIMENSIONS = {
        "branch": ("s.nombre", "sucursal"),
        "product": ("p.nombre", "producto"),
        "variant": ("vp.sku", "sku"),
        "category": ("c.nombre", "categoria"),
        "payment_method": ("v.metodo_pago::text", "metodo_pago"),
        "sale_type": ("v.tipo::text", "tipo_venta"),
        "sale_status": ("v.estado::text", "estado_venta"),
        "reservation_status": ("r.estado::text", "estado_reserva"),
        "customer": ("u.full_name", "cliente"),
        "day": ("DATE(v.created_at AT TIME ZONE 'America/La_Paz')", "dia"),
        "week": ("DATE_TRUNC('week', v.created_at AT TIME ZONE 'America/La_Paz')::date", "semana_desde"),
        "month": ("DATE_TRUNC('month', v.created_at AT TIME ZONE 'America/La_Paz')::date", "mes_desde"),
    }

    SALES_METRICS = {
        "units_sold": ("SUM(dv.cantidad)", "unidades_vendidas", True),
        "sales_count": ("COUNT(DISTINCT v.id)", "cantidad_ventas", False),
        "gross_revenue": ("SUM(v.monto_total)", "ingresos_brutos_bs", False),
        "net_revenue": ("SUM(v.monto_neto)", "ingresos_netos_bs", False),
        "item_revenue": ("SUM(dv.subtotal)", "ingresos_prendas_bs", True),
        "discount_total": ("SUM(v.descuento)", "descuentos_bs", False),
    }
    INVENTORY_METRICS = {
        "stock_available": ("SUM(i.stock_actual - i.stock_reservado)", "stock_disponible"),
        "stock_physical": ("SUM(i.stock_actual)", "stock_fisico"),
        "stock_reserved": ("SUM(i.stock_reservado)", "stock_reservado"),
        "stock_minimum": ("SUM(i.stock_minimo)", "stock_minimo"),
    }
    RESERVATION_METRICS = {
        "reservation_count": ("COUNT(DISTINCT r.id)", "cantidad_reservas"),
        "reservation_value": ("SUM(r.total_estimado)", "monto_estimado_bs"),
        "units_reserved": ("SUM(dr.cantidad)", "unidades_reservadas"),
    }

    @classmethod
    def build(cls, intent: ReportIntent) -> tuple[str, dict[str, Any], str]:
        if intent.status != "ready":
            message = intent.clarification or "Indica qué dato quieres consultar y cómo agruparlo."
            raise HTTPException(422, message)
        cls._validate_compatibility(intent)
        if intent.domain == "sales":
            sql, params, metric_label = cls._build_sales(intent)
        elif intent.domain == "inventory":
            sql, params, metric_label = cls._build_inventory(intent)
        elif intent.domain == "reservations":
            sql, params, metric_label = cls._build_reservations(intent)
        elif intent.domain == "exchanges":
            sql, params, metric_label = cls._build_exchanges(intent)
        else:
            raise HTTPException(422, "Ese tipo de reporte todavía no está disponible.")
        return sql, params, metric_label

    @classmethod
    def _validate_compatibility(cls, intent: ReportIntent) -> None:
        allowed_dimensions = {
            "sales": {"branch", "product", "variant", "category", "payment_method", "sale_type", "sale_status", "customer", "day", "week", "month"},
            "inventory": {"branch", "product", "variant", "category"},
            "reservations": {"branch", "product", "variant", "category", "reservation_status", "customer", "day", "week", "month"},
            "exchanges": {"branch", "month"},
        }
        allowed_filters = {
            "sales": {"branch", "category", "product", "payment_method", "sale_status", "sale_type"},
            "inventory": {"branch", "category", "product"},
            "reservations": {"branch", "category", "product", "reservation_status"},
            "exchanges": {"branch"},
        }
        if intent.domain != "none":
            if not set(intent.dimensions) <= allowed_dimensions[intent.domain]:
                raise HTTPException(422, "La agrupación solicitada no está disponible para este reporte.")
            if any(item.field not in allowed_filters[intent.domain] for item in intent.filters):
                raise HTTPException(422, "Uno de los filtros solicitados no está disponible para este reporte.")
        if intent.domain == "sales" and intent.metric not in cls.SALES_METRICS:
            raise HTTPException(422, "La métrica solicitada no corresponde a reportes de ventas.")
        if intent.domain == "inventory" and intent.metric not in cls.INVENTORY_METRICS:
            raise HTTPException(422, "La métrica solicitada no corresponde a inventario.")
        if intent.domain == "reservations" and intent.metric not in cls.RESERVATION_METRICS:
            raise HTTPException(422, "La métrica solicitada no corresponde a reservas.")
        if intent.domain == "exchanges" and intent.metric not in {"exchange_count", "exchange_difference"}:
            raise HTTPException(422, "La métrica solicitada no corresponde a cambios de prenda.")
        if intent.ranking and not intent.dimensions:
            raise HTTPException(422, "Para ordenar un top, indica qué elemento quieres comparar.")
        if intent.ranking and intent.ranking.within and intent.ranking.within not in intent.dimensions:
            raise HTTPException(422, "El top por grupo debe incluir ese grupo en la consulta.")

    @classmethod
    def _where_filters(cls, intent: ReportIntent, aliases: dict[str, str], params: dict[str, Any]) -> list[str]:
        clauses: list[str] = []
        for index, item in enumerate(intent.filters):
            expression = aliases.get(item.field)
            if not expression:
                raise HTTPException(422, f"No se puede filtrar este reporte por {item.field}.")
            key = f"filter_{index}"
            if item.field in cls.ENUM_FILTER_VALUES:
                normalized = item.value.strip().upper()
                if item.match != "exact" or normalized not in cls.ENUM_FILTER_VALUES[item.field]:
                    raise HTTPException(422, f"El valor solicitado para {item.field} no está reconocido.")
                params[key] = normalized
                clauses.append(f"CAST({expression} AS TEXT) = :{key}")
                continue
            if item.match == "contains":
                params[key] = f"%{item.value.strip()}%"
                clauses.append(f"CAST({expression} AS TEXT) ILIKE :{key}")
            else:
                params[key] = item.value.strip()
                clauses.append(f"LOWER(CAST({expression} AS TEXT)) = LOWER(:{key})")
        return clauses

    @classmethod
    def _period_predicate(cls, intent: ReportIntent, params: dict[str, Any], column: str) -> str | None:
        kind = intent.period.kind
        if kind == "all_time":
            return None
        if kind in {"last_days", "last_weeks", "last_months"}:
            amount = intent.period.amount
            if amount is None:
                raise HTTPException(422, "No pude determinar la duración del periodo.")
            params["period_amount"] = amount
            interval = {"last_days": "day", "last_weeks": "week", "last_months": "month"}[kind]
            local_column = f"({column} AT TIME ZONE 'America/La_Paz')"
            return f"{local_column} >= (CURRENT_TIMESTAMP AT TIME ZONE 'America/La_Paz') - (:period_amount * INTERVAL '1 {interval}') AND {local_column} < (CURRENT_TIMESTAMP AT TIME ZONE 'America/La_Paz')"
        if kind in {"this_month", "last_month", "this_year"}:
            if kind == "this_month":
                start = "DATE_TRUNC('month', CURRENT_TIMESTAMP AT TIME ZONE 'America/La_Paz')"
                end = f"{start} + INTERVAL '1 month'"
            elif kind == "last_month":
                end = "DATE_TRUNC('month', CURRENT_TIMESTAMP AT TIME ZONE 'America/La_Paz')"
                start = f"{end} - INTERVAL '1 month'"
            else:
                start = "DATE_TRUNC('year', CURRENT_TIMESTAMP AT TIME ZONE 'America/La_Paz')"
                end = f"{start} + INTERVAL '1 year'"
            local_column = f"({column} AT TIME ZONE 'America/La_Paz')"
            return f"{local_column} >= {start} AND {local_column} < {end}"
        raise HTTPException(422, "El periodo solicitado no está soportado.")

    @classmethod
    def _grouped_select(cls, intent: ReportIntent, from_sql: str, metric_sql: str, metric_alias: str,
                        where: list[str], params: dict[str, Any], label: str,
                        dimension_map: dict[str, tuple[str, str]] | None = None) -> tuple[str, dict[str, Any], str]:
        dimension_map = dimension_map or cls.DIMENSIONS
        dims = list(intent.dimensions)
        expanded = []
        for dimension in dims:
            if dimension == "variant":
                expanded.extend([("vp.sku", "sku"), ("vp.talla", "talla"), ("vp.color", "color")])
            else:
                expanded.append(dimension_map[dimension])
        select_dims = [f"{expression} AS {alias}" for expression, alias in expanded]
        group_exprs = [expression for expression, _alias in expanded]
        where_sql = " AND ".join(where) if where else "TRUE"
        if intent.ranking:
            base = (
                f"SELECT {', '.join(select_dims + [f'COALESCE({metric_sql}, 0) AS {metric_alias}'])} "
                f"FROM {from_sql} WHERE {where_sql} "
                + (f"GROUP BY {', '.join(group_exprs)} " if group_exprs else "")
            )
            partition = f"PARTITION BY grouped.{dimension_map[intent.ranking.within][1]} " if intent.ranking.within else ""
            stable = ", ".join(f"grouped.{alias}" for _expression, alias in expanded)
            order = "ASC" if intent.ranking.direction == "asc" else "DESC"
            tie_break = f", {stable} ASC" if stable else ""
            output_order = (
                f"ranked.{dimension_map[intent.ranking.within][1]}, ranked.posicion_ranking"
                if intent.ranking.within else "ranked.posicion_ranking"
            )
            sql = (
                f"SELECT ranked.* FROM (SELECT grouped.*, ROW_NUMBER() OVER ({partition}"
                f"ORDER BY {metric_alias} {order}{tie_break}) AS posicion_ranking "
                f"FROM ({base}) AS grouped) AS ranked WHERE posicion_ranking <= :top_n "
                f"ORDER BY {output_order} ASC"
            )
            params["top_n"] = intent.ranking.top_n
        else:
            sql = (
                f"SELECT {', '.join(select_dims + [f'COALESCE({metric_sql}, 0) AS {metric_alias}'])} "
                f"FROM {from_sql} WHERE {where_sql} "
                + (f"GROUP BY {', '.join(group_exprs)} " if group_exprs else "")
            )
            if dims:
                sql += "ORDER BY " + ", ".join(dimension_map[d][1] for d in dims)
        return sql, params, label

    @classmethod
    def _build_sales(cls, intent: ReportIntent):
        query_intent = intent
        if intent.detail == "variant" and "variant" not in intent.dimensions:
            query_intent = intent.model_copy(update={"dimensions": [*intent.dimensions, "variant"]})
        params: dict[str, Any] = {}
        needs_details = intent.metric in {"units_sold", "item_revenue"} or any(
            d in query_intent.dimensions for d in ("product", "variant", "category")
        ) or any(f.field in {"product", "category"} for f in intent.filters)
        if intent.metric == "net_revenue" and needs_details:
            raise HTTPException(422, "El ingreso neto por prenda no se calcula para no duplicar impuestos o descuentos.")
        metric_sql, metric_alias, metric_needs_detail = cls.SALES_METRICS[intent.metric]
        if metric_needs_detail and not needs_details:
            needs_details = True
        from_sql = "ventas v JOIN sucursales s ON s.id = v.sucursal_id"
        if needs_details:
            from_sql += " JOIN detalles_venta dv ON dv.venta_id = v.id JOIN variantes_producto vp ON vp.id = dv.variante_id JOIN productos p ON p.id = vp.producto_id"
        if "category" in query_intent.dimensions or any(f.field == "category" for f in intent.filters):
            from_sql += " JOIN categorias c ON c.id = p.categoria_id"
        if "customer" in query_intent.dimensions:
            from_sql += " LEFT JOIN users u ON u.id = v.cliente_id"
        where = ["v.estado = 'COMPLETADA'"]
        if "sale_status" in query_intent.dimensions or any(f.field == "sale_status" for f in intent.filters):
            where = []
        aliases = {"branch": "s.nombre", "payment_method": "v.metodo_pago", "sale_status": "v.estado", "sale_type": "v.tipo"}
        if needs_details:
            aliases.update({"product": "p.nombre", "category": "c.nombre"})
        if "customer" in query_intent.dimensions:
            aliases["customer"] = "u.full_name"
        where += cls._where_filters(intent, aliases, params)
        period = cls._period_predicate(intent, params, "v.created_at")
        if period:
            where.append(period)
        # A whole-sale amount cannot be grouped by product without multiplying it by its lines.
        if needs_details and intent.metric in {"gross_revenue", "discount_total"}:
            raise HTTPException(422, "Para agrupar por prenda, solicita ingresos de prendas; el total de venta incluye otros conceptos.")
        label = {
            "units_sold": "unidades vendidas",
            "sales_count": "cantidad de ventas",
            "gross_revenue": "ingresos brutos (Bs.)",
            "net_revenue": "monto neto sin IVA (Bs.; no equivale a utilidad)",
            "item_revenue": "ingresos de prendas (Bs.)",
            "discount_total": "descuentos (Bs.)",
        }[intent.metric]
        return cls._grouped_select(query_intent, from_sql, metric_sql, metric_alias, where, params, label)

    @classmethod
    def _build_inventory(cls, intent: ReportIntent):
        if any(d in intent.dimensions for d in ("payment_method", "sale_type", "sale_status", "reservation_status", "customer", "day", "week", "month")):
            raise HTTPException(422, "Inventario muestra existencias actuales; no admite agrupación por ventas o fechas.")
        if intent.period.kind != "all_time":
            raise HTTPException(422, "El inventario es una fotografía actual; no se puede filtrar por un periodo de ventas.")
        metric_sql, metric_alias = cls.INVENTORY_METRICS[intent.metric]
        from_sql = (
            "inventario i JOIN sucursales s ON s.id = i.sucursal_id "
            "JOIN variantes_producto vp ON vp.id = i.variante_id JOIN productos p ON p.id = vp.producto_id"
        )
        if "category" in intent.dimensions or any(f.field == "category" for f in intent.filters):
            from_sql += " JOIN categorias c ON c.id = p.categoria_id"
        aliases = {"branch": "s.nombre", "product": "p.nombre"}
        if "category" in intent.dimensions or any(f.field == "category" for f in intent.filters):
            aliases["category"] = "c.nombre"
        params: dict[str, Any] = {}
        where = ["i.activo = TRUE", "s.activa = TRUE", "p.activo = TRUE", "vp.activo = TRUE"]
        where += cls._where_filters(intent, aliases, params)
        return cls._grouped_select(
            intent, from_sql, metric_sql, metric_alias, where, params,
            metric_alias.replace("_", " "), cls.DIMENSIONS,
        )

    @classmethod
    def _build_reservations(cls, intent: ReportIntent):
        detail_dims = any(d in intent.dimensions for d in ("product", "variant", "category")) or any(
            item.field in {"product", "category"} for item in intent.filters
        )
        needs_detail_rows = detail_dims or intent.metric == "units_reserved"
        if intent.metric == "units_reserved":
            metric_sql, metric_alias = cls.RESERVATION_METRICS[intent.metric]
        elif intent.metric == "reservation_count":
            metric_sql, metric_alias = cls.RESERVATION_METRICS[intent.metric]
        else:
            if detail_dims:
                raise HTTPException(422, "El total de reservas no se reparte por producto para evitar duplicar importes.")
            metric_sql, metric_alias = cls.RESERVATION_METRICS[intent.metric]
        from_sql = "reservas r JOIN sucursales s ON s.id = r.sucursal_id"
        if needs_detail_rows:
            from_sql += " JOIN detalles_reserva dr ON dr.reserva_id = r.id JOIN variantes_producto vp ON vp.id = dr.variante_id JOIN productos p ON p.id = vp.producto_id"
        if "category" in intent.dimensions or any(f.field == "category" for f in intent.filters):
            from_sql += " JOIN categorias c ON c.id = p.categoria_id"
        if "customer" in intent.dimensions:
            from_sql += " JOIN users u ON u.id = r.cliente_id"
        params: dict[str, Any] = {}
        aliases = {"branch": "s.nombre", "reservation_status": "r.estado"}
        if detail_dims:
            aliases.update({"product": "p.nombre", "category": "c.nombre"})
        if "customer" in intent.dimensions:
            aliases["customer"] = "u.full_name"
        where = ["r.activo = TRUE"] + cls._where_filters(intent, aliases, params)
        period = cls._period_predicate(intent, params, "r.created_at")
        if period:
            where.append(period)
        reservation_dimensions = {
            **cls.DIMENSIONS,
            "day": ("DATE(r.created_at AT TIME ZONE 'America/La_Paz')", "dia"),
            "week": ("DATE_TRUNC('week', r.created_at AT TIME ZONE 'America/La_Paz')::date", "semana_desde"),
            "month": ("DATE_TRUNC('month', r.created_at AT TIME ZONE 'America/La_Paz')::date", "mes_desde"),
        }
        return cls._grouped_select(
            intent, from_sql, metric_sql, metric_alias, where, params,
            {
                "reservation_count": "cantidad de reservas",
                "reservation_value": "monto estimado de reservas (Bs.)",
                "units_reserved": "unidades reservadas",
            }[intent.metric], reservation_dimensions,
        )

    @classmethod
    def _build_exchanges(cls, intent: ReportIntent):
        if any(d not in {"branch", "month"} for d in intent.dimensions):
            raise HTTPException(422, "Los cambios de prenda se pueden agrupar por sucursal y mes.")
        metric_sql, metric_alias = (
            ("COUNT(cp.id)", "cantidad_cambios") if intent.metric == "exchange_count"
            else ("SUM(cp.diferencia)", "diferencia_total_bs")
        )
        from_sql = "cambios_producto cp JOIN sucursales s ON s.id = cp.sucursal_id"
        params: dict[str, Any] = {}
        where = cls._where_filters(intent, {"branch": "s.nombre"}, params)
        period = cls._period_predicate(intent, params, "cp.created_at")
        if period:
            where.append(period)
        # Exchange month is tied to the change timestamp, not the sales table.
        dimension_map = {**cls.DIMENSIONS, "month": ("DATE_TRUNC('month', cp.created_at AT TIME ZONE 'America/La_Paz')::date", "mes_desde")}
        return cls._grouped_select(
            intent, from_sql, metric_sql, metric_alias, where, params,
            metric_alias.replace("_", " "), dimension_map,
        )
