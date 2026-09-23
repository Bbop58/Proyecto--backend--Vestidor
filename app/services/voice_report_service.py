import os
import re
import json
import time
import logging
from decimal import Decimal
from uuid import UUID
from datetime import datetime, date
from typing import Dict, Any, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import HTTPException, status
from google import genai
from google.genai import types

from app.config import settings
from app.schemas.voice_report import VoiceReportQueryResponse

logger = logging.getLogger("voice_report_service")

# Esquema de base de datos PostgreSQL acotado exclusivamente a entidades de negocio
DATABASE_SCHEMA_CONTEXT = """
Database: PostgreSQL (Dialect: postgresql)

Tables & Columns:
1. ventas (Sales table):
   - id: UUID (Primary Key)
   - numero_recibo: VARCHAR (e.g. 'REC-2026-0001')
   - tipo: VARCHAR (Enum: 'PRESENCIAL', 'DIGITAL')
   - estado: VARCHAR (Enum: 'PENDIENTE_PAGO', 'COMPLETADA', 'CANCELADA')
   - metodo_pago: VARCHAR (Enum: 'EFECTIVO', 'TARJETA', 'QR', 'PAYPAL')
   - monto_total: NUMERIC(10,2) (Total amount of sale in Bolivianos)
   - impuesto_iva: NUMERIC(10,2) (13% IVA tax collected)
   - monto_neto: NUMERIC(10,2) (87% net revenue / ganancia neta after tax)
   - sucursal_id: UUID (Foreign key to sucursales.id)
   - cliente_id: UUID (Foreign key to users.id)
   - cajero_id: UUID (Foreign key to users.id)
   - created_at: TIMESTAMPTZ (Date and time of the sale)

2. detalles_venta (Sale Items table):
   - id: UUID (Primary Key)
   - venta_id: UUID (Foreign key to ventas.id)
   - variante_id: UUID (Foreign key to variantes_producto.id)
   - cantidad: INTEGER (Quantity sold)
   - precio_unitario: NUMERIC(10,2) (Unit price)
   - subtotal: NUMERIC(10,2) (subtotal = cantidad * precio_unitario)

3. productos (Products table):
   - id: UUID (Primary Key)
   - nombre: VARCHAR (Product name)
   - descripcion: TEXT
   - precio_base: NUMERIC(10,2)
   - categoria_id: UUID (Foreign key to categorias.id)
   - temporada: VARCHAR
   - proveedor: VARCHAR
   - activo: BOOLEAN

4. variantes_producto (Product Variants table):
   - id: UUID (Primary Key)
   - producto_id: UUID (Foreign key to productos.id)
   - talla: VARCHAR (Size e.g. 'S', 'M', 'L', 'XL', '38', '40')
   - color: VARCHAR (Color name)
   - sku: VARCHAR (SKU barcode)
   - precio_extra: NUMERIC(10,2)
   - activo: BOOLEAN

5. categorias (Categories table):
   - id: UUID (Primary Key)
   - nombre: VARCHAR (Category name)
   - descripcion: TEXT
   - activo: BOOLEAN

6. sucursales (Branches/Stores table):
   - id: UUID (Primary Key)
   - nombre: VARCHAR (Branch name e.g. 'Sucursal Central', 'Sucursal Norte')
   - direccion: VARCHAR
   - telefono: VARCHAR
   - activo: BOOLEAN

7. users (Customers / Employees table):
   - id: UUID (Primary Key)
   - full_name: VARCHAR (Full name of customer or employee)
   - email: VARCHAR
   - is_active: BOOLEAN
   - role_id: UUID

8. inventarios (Stock/Inventory table):
   - id: UUID (Primary Key)
   - variante_id: UUID (Foreign key to variantes_producto.id)
   - sucursal_id: UUID (Foreign key to sucursales.id)
   - stock_actual: INTEGER (Current stock units)
   - stock_minimo: INTEGER (Safety minimum stock)

9. reservas (Reservations table):
   - id: UUID (Primary Key)
   - cliente_id: UUID (Foreign key to users.id)
   - sucursal_id: UUID (Foreign key to sucursales.id)
   - estado: VARCHAR (Enum: 'PENDIENTE', 'CONFIRMADA', 'COMPLETADA', 'CANCELADA')
   - total: NUMERIC(10,2)
   - created_at: TIMESTAMPTZ

Business Rules & Logic:
- For completed revenue/sales queries, filter by `v.estado = 'COMPLETADA'`.
- "Clientes más platudos" or "mejores clientes" means users who spent the most total money in completed sales.
- "Productos más vendidos" means products with highest sum of `dv.cantidad` in completed sales.
- Always use aliases (e.g. `v`, `dv`, `p`, `u`, `s`, `c`).
- Format aliases nicely for presentation (e.g. `SELECT p.nombre AS producto, SUM(dv.cantidad) AS total_unidades, SUM(dv.subtotal) AS total_ingresos_bs`).
"""

# Palabras clave y sentencias destructivas prohibidas
FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE|EXEC|EXECUTE|COPY|LOCK|VACUUM|REINDEX|PG_|INFORMATION_SCHEMA)\b",
    re.IGNORECASE
)


class VoiceReportService:

    @classmethod
    def _generate_sql_with_gemini(cls, question: str, limit: int = 100) -> Tuple[str, str]:
        """
        Traduce una pregunta en lenguaje natural a consulta SQL usando el modelo de texto Gemini 2.5 Flash.
        Utiliza el Free Tier de texto (100% gratuito).
        """
        api_key = (
            settings.VOICE_GEMINI_API_KEY
            or os.getenv("VOICE_GEMINI_API_KEY")
            or settings.GEMINI_API_KEY
            or os.getenv("GEMINI_API_KEY")
        )
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Ni VOICE_GEMINI_API_KEY ni GEMINI_API_KEY están configuradas en el servidor."
            )

        client = genai.Client(api_key=api_key)

        prompt = f"""
You are an expert PostgreSQL Database Architect and Business Intelligence Analyst.
Translate the user's natural language question in Spanish into a single, highly-optimized, read-only PostgreSQL SQL query.

{DATABASE_SCHEMA_CONTEXT}

STRICT CONSTRAINTS:
1. Generate ONLY a single `SELECT` query.
2. NEVER generate statements that modify data (NO INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE).
3. Do NOT include any semicolon (;) or multiple queries.
4. Ensure all column names and table names match the schema provided above exactly.
5. Apply `LIMIT {limit}` if the query could return multiple rows and does not already have a smaller limit.
6. Provide a concise explanation in Spanish describing what data the query retrieves.

User Question: "{question}"

Respond strictly with a JSON object in this exact format:
{{
  "sql": "SELECT ...",
  "interpretation": "Explicación breve en español de los datos analizados"
}}
"""

        candidate_models = ["gemini-3.1-flash-lite", "gemini-3.6-flash", "gemini-flash-latest"]
        response = None
        last_err = None

        for m_name in candidate_models:
            try:
                response = client.models.generate_content(
                    model=m_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    )
                )
                if response and response.text:
                    break
            except Exception as m_err:
                last_err = m_err
                logger.warning(f"Error con modelo {m_name}: {m_err}, intentando siguiente...")

        if not response or not response.text:
            raise RuntimeError(f"Ningún modelo pudo procesar la consulta: {last_err}")

        try:
            raw_text = response.text.strip()
            # Limpiar posibles bloques markdown si los hubiera
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)

            data = json.loads(raw_text)
            sql_query = data.get("sql", "").strip()
            interpretation = data.get("interpretation", "").strip()

            if not sql_query:
                raise ValueError("La IA no generó ninguna consulta SQL.")

            return sql_query, interpretation

        except Exception as e:
            logger.error(f"Error generando SQL con Gemini: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"No se pudo traducir la pregunta a SQL ({type(e).__name__}): {str(e)}"
            )

    @classmethod
    def _validate_sql_security(cls, sql: str) -> str:
        """
        Validador estricto de seguridad para consultas SQL generadas por IA.
        Garantiza que la consulta sea exclusivamente de solo lectura (SELECT).
        """
        clean_sql = sql.strip()

        # 1. Quitar punto y coma final si lo tiene
        if clean_sql.endswith(";"):
            clean_sql = clean_sql[:-1].strip()

        # 2. Rechazar múltiples consultas encadenadas
        if ";" in clean_sql:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Por seguridad no se permiten múltiples consultas SQL encadenadas."
            )

        # 3. Debe comenzar estrictamente con SELECT o WITH (Common Table Expressions)
        if not re.match(r"^(SELECT|WITH)\b", clean_sql, re.IGNORECASE):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Consulta no autorizada: Solo se permiten consultas de tipo SELECT."
            )

        # 4. Verificar ausencia de palabras destructivas
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
    def execute_voice_report(
        cls,
        db: Session,
        question: str,
        limit: int = 100
    ) -> VoiceReportQueryResponse:
        """
        Flujo completo:
        1. Lenguaje natural -> SQL (con Gemini 2.5 Flash).
        2. Validación estricta de seguridad.
        3. Ejecución segura con timeout en PostgreSQL.
        4. Mapeo y serialización de resultados para visualización y PDF.
        """
        start_time = time.time()

        # 1. Generar SQL con IA
        raw_sql, interpretation = cls._generate_sql_with_gemini(question, limit=limit)

        # 2. Validar seguridad
        validated_sql = cls._validate_sql_security(raw_sql)

        # 3. Asegurar LIMIT
        if not re.search(r"\bLIMIT\s+\d+\b", validated_sql, re.IGNORECASE):
            validated_sql = f"{validated_sql} LIMIT {limit}"

        logger.info(f"Ejecutando SQL validado para reporte de voz: {validated_sql}")

        # 4. Ejecutar consulta de solo lectura
        try:
            # Configurar statement_timeout a 5 segundos para proteger el servidor
            db.execute(text("SET statement_timeout = 5000;"))
            result = db.execute(text(validated_sql))
            
            columns = list(result.keys()) if result.returns_rows else []
            rows: List[Dict[str, Any]] = []

            if result.returns_rows:
                for row in result.fetchall():
                    row_dict = {}
                    for col, val in zip(columns, row):
                        row_dict[col] = cls._serialize_value(val)
                    rows.append(row_dict)

            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            logger.info(f"Reporte de voz ejecutado con éxito: {len(rows)} filas en {elapsed_ms}ms")

            return VoiceReportQueryResponse(
                pregunta=question,
                interpretacion=interpretation,
                sql_generado=validated_sql,
                columnas=columns,
                filas=rows,
                total_filas=len(rows),
                tiempo_ms=elapsed_ms
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error al ejecutar consulta SQL en base de datos: {e}", exc_info=True)
            # Revertir transacción para no dejar la sesión en estado fallido
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La consulta generada no pudo ejecutarse: {str(e)}"
            )
