import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.seed_permissions import seed
from app.ml.synthetic_data_generator import SyntheticDataGenerator
from app.ml.supervised_model import DemandForecaster
from app.ml.unsupervised_model import CustomerSegmenter
from app.ml.ml_service import MLAnalyticsService

ADMIN_DATA = {
    "email": "admin_analytics@example.com",
    "password": "AdminPassword123!",
    "full_name": "Admin Analytics ML"
}


def test_synthetic_data_generation():
    # 1. Dataset de Demanda de Ventas
    sales_df = SyntheticDataGenerator.generate_sales_demand_dataset(seed=42)
    assert len(sales_df) == 52 * 4 * 5  # 52 semanas * 4 sucursales * 5 categorías = 1040 registros
    assert "ingreso_total_bs" in sales_df.columns
    assert "unidades_demandadas" in sales_df.columns
    assert "ventas_lag1_bs" in sales_df.columns
    assert not sales_df.isnull().values.any()

    # 2. Dataset de Clientes RFM
    customers_df = SyntheticDataGenerator.generate_rfm_customers_dataset(n_samples=200, seed=42)
    assert len(customers_df) == 200
    assert "recencia_dias" in customers_df.columns
    assert "frecuencia_compras" in customers_df.columns
    assert "monto_total_bs" in customers_df.columns
    assert not customers_df.isnull().values.any()


def test_supervised_demand_forecaster_training():
    sales_df = SyntheticDataGenerator.generate_sales_demand_dataset(seed=42)
    forecaster = DemandForecaster()
    metrics = forecaster.train(sales_df)

    # Validaciones matemáticas requeridas por el examen
    assert metrics.r2_score >= 0.70, f"R² esperado >= 0.70, obtenido {metrics.r2_score}"
    assert metrics.rmse > 0
    assert metrics.mae > 0
    assert len(metrics.importancia_caracteristicas) > 0

    # Inferencia de serie temporal
    timeline = forecaster.predict_timeline(sales_df, weeks_ahead=4)
    assert len(timeline) >= 12
    projected_points = [p for p in timeline if p.es_proyeccion]
    assert len(projected_points) == 4
    for p in projected_points:
        assert p.demanda_proyectada_bs > 0
        assert p.unidades_estimadas > 0


def test_unsupervised_customer_segmentation_training():
    customers_df = SyntheticDataGenerator.generate_rfm_customers_dataset(n_samples=250, seed=42)
    segmenter = CustomerSegmenter()
    metrics, centroids, sample_pts = segmenter.train(customers_df, n_clusters=4)

    # Validación formal del Silhouette Score
    assert metrics.silhouette_score >= 0.45, f"Silhouette Score esperado >= 0.45, obtenido {metrics.silhouette_score}"
    assert metrics.n_clusters == 4
    assert len(centroids) == 4
    assert len(sample_pts) > 0

    # Verificar que los 4 arquetipos estén representados
    cluster_names = [c.nombre_segmento for c in centroids]
    assert any("VIP" in name for name in cluster_names)
    assert any("Frecuentes" in name for name in cluster_names)
    assert any("Nuevos" in name for name in cluster_names)
    assert any("Riesgo" in name for name in cluster_names)


def test_analytics_api_endpoints_e2e(client: TestClient, db_session: Session):
    # 1. Sembrado y autenticación
    seed(db_session)
    admin_reg = client.post("/api/v1/auth/register", json=ADMIN_DATA)
    assert admin_reg.status_code == 201
    token = admin_reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. POST /api/v1/analytics/train
    train_resp = client.post("/api/v1/analytics/train", headers=headers)
    assert train_resp.status_code == 200
    train_data = train_resp.json()
    assert train_data["estado"] == "COMPLETADO"
    assert train_data["supervisado"]["r2_score"] >= 0.70
    assert train_data["no_supervisado"]["silhouette_score"] >= 0.45

    # 3. GET /api/v1/analytics/forecast
    forecast_resp = client.get("/api/v1/analytics/forecast?weeks_ahead=4", headers=headers)
    assert forecast_resp.status_code == 200
    forecast_data = forecast_resp.json()
    assert len(forecast_data["serie_temporal"]) > 0
    assert forecast_data["total_proyectado_mes_bs"] > 0
    assert "RandomForest" in forecast_data["resumen_ejecutivo"]

    # 4. GET /api/v1/analytics/segments
    segments_resp = client.get("/api/v1/analytics/segments", headers=headers)
    assert segments_resp.status_code == 200
    segments_data = segments_resp.json()
    assert len(segments_data["clusters"]) == 4
    assert len(segments_data["puntos_muestra"]) > 0
    assert segments_data["metricas"]["silhouette_score"] >= 0.45

    # 5. POST /api/v1/analytics/forecast/predict
    predict_payload = {
        "semana_del_ano": 48,
        "mes": 11,
        "temporada": "Primavera",
        "categoria_nombre": "Chaquetas y Abrigos",
        "sucursal_nombre": "Sucursal Central",
        "ventas_semana_anterior_bs": 15000.0
    }
    predict_resp = client.post("/api/v1/analytics/forecast/predict", json=predict_payload, headers=headers)
    assert predict_resp.status_code == 200
    predict_data = predict_resp.json()
    assert predict_data["demanda_proyectada_bs"] > 0
    assert predict_data["unidades_estimadas"] > 0
    assert predict_data["rango_confianza_superior_bs"] >= predict_data["demanda_proyectada_bs"]
