import sys
import os

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from app.database import SessionLocal
from app.services.voice_report_service import VoiceReportService
from fastapi import HTTPException

def test_voice_reports_suite():
    print("==================================================")
    print("   TEST DE REPORTES POR COMANDO DE VOZ (TEXT-TO-SQL)   ")
    print("==================================================")

    # 1. Test de validador de seguridad
    print("\n[TEST 1] Verificando rechazo de consultas maliciosas / no permitidas...")
    
    malicious_queries = [
        "DROP TABLE ventas;",
        "DELETE FROM users WHERE id IS NOT NULL",
        "INSERT INTO roles (nombre) VALUES ('hacker')",
        "UPDATE productos SET precio_base = 0",
        "SELECT * FROM ventas; DROP TABLE productos;",
        "CREATE TABLE test (id int)",
        "TRUNCATE TABLE ventas",
    ]

    for q in malicious_queries:
        try:
            VoiceReportService._validate_sql_security(q)
            assert False, f"FALLO DE SEGURIDAD: La consulta '{q}' no fue bloqueada."
        except HTTPException as e:
            print(f" -> Bloqueado con éxito: '{q[:30]}...' (Razón: {e.detail})")

    # 2. Test de consulta válida
    print("\n[TEST 2] Verificando consulta válida SELECT...")
    valid_query = "SELECT p.nombre, p.precio_base FROM productos p WHERE p.activo = true LIMIT 10"
    validated = VoiceReportService._validate_sql_security(valid_query)
    assert validated == valid_query, "La consulta válida fue alterada incorrectamente"
    print(" -> Consulta válida aprobada correctamente.")

    # 3. Test de generación y ejecución de reporte real en base de datos
    print("\n[TEST 3] Generando reporte en lenguaje natural con IA...")
    pregunta_test = "Dame los 5 productos con mayor precio o más caros"
    db = SessionLocal()
    try:
        resultado = VoiceReportService.execute_voice_report(
            db=db,
            question=pregunta_test,
            limit=5
        )

        print(f" -> Pregunta: '{resultado.pregunta}'")
        print(f" -> Interpretación IA: '{resultado.interpretacion}'")
        print(f" -> SQL Generado: {resultado.sql_generado}")
        print(f" -> Columnas obtenidas: {resultado.columnas}")
        print(f" -> Filas encontradas: {resultado.total_filas}")
        print(f" -> Tiempo de ejecución: {resultado.tiempo_ms} ms")
        
        if resultado.filas:
            print(f" -> Muestra de primer resultado: {resultado.filas[0]}")

        assert resultado.total_filas >= 0, "No se retornó un conteo válido de filas"
        assert len(resultado.columnas) > 0, "No se retornaron columnas"
        assert "SELECT" in resultado.sql_generado.upper(), "El SQL generado no es SELECT"

        print("\n==================================================")
        print("   TEST DE REPORTES POR VOZ COMPLETADO EXITOSAMENTE   ")
        print("==================================================")

    finally:
        db.close()

if __name__ == "__main__":
    test_voice_reports_suite()
