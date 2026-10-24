import time
from datetime import datetime
from typing import Optional, Tuple
import pandas as pd

from app.ml.synthetic_data_generator import SyntheticDataGenerator
from app.ml.supervised_model import DemandForecaster
from app.ml.unsupervised_model import CustomerSegmenter
from app.schemas.analytics import (
    TrainingMetricsResponse,
    DemandForecastResponse,
    CustomerSegmentationResponse,
    DemandPredictCustomRequest,
    DemandPredictCustomResponse
)


class MLAnalyticsService:
    """
    Servicio de alto nivel para analítica avanzada con Machine Learning.
    Orquesta el ciclo de vida de los modelos (entrenamiento, persistencia e inferencia en tiempo real).
    """

    _forecaster: Optional[DemandForecaster] = None
    _segmenter: Optional[CustomerSegmenter] = None
    _sales_df: Optional[pd.DataFrame] = None
    _customers_df: Optional[pd.DataFrame] = None

    @classmethod
    def get_forecaster(cls) -> DemandForecaster:
        if cls._forecaster is None:
            cls._forecaster = DemandForecaster()
            if not cls._forecaster.load():
                cls._init_and_train_if_needed()
        return cls._forecaster

    @classmethod
    def get_segmenter(cls) -> CustomerSegmenter:
        if cls._segmenter is None:
            cls._segmenter = CustomerSegmenter()
            if not cls._segmenter.load():
                cls._init_and_train_if_needed()
        return cls._segmenter

    @classmethod
    def get_sales_data(cls) -> pd.DataFrame:
        if cls._sales_df is None:
            cls._sales_df = SyntheticDataGenerator.generate_sales_demand_dataset()
        return cls._sales_df

    @classmethod
    def get_customers_data(cls) -> pd.DataFrame:
        if cls._customers_df is None:
            cls._customers_df = SyntheticDataGenerator.generate_rfm_customers_dataset()
        return cls._customers_df

    @classmethod
    def _init_and_train_if_needed(cls) -> None:
        """Inicializa y entrena automáticamente los modelos si no existen artefactos en disco."""
        sales_df = cls.get_sales_data()
        customers_df = cls.get_customers_data()

        if cls._forecaster is None:
            cls._forecaster = DemandForecaster()
        if not cls._forecaster.load():
            cls._forecaster.train(sales_df)

        if cls._segmenter is None:
            cls._segmenter = CustomerSegmenter()
        if not cls._segmenter.load():
            cls._segmenter.train(customers_df)

    @classmethod
    def train_all(cls) -> TrainingMetricsResponse:
        """
        Entrena simultáneamente el modelo supervisado de demanda y el modelo no supervisado
        de clustering de clientes, cronometrando la ejecución y retornando las métricas de evaluación.
        """
        start_time = time.time()

        sales_df = SyntheticDataGenerator.generate_sales_demand_dataset()
        customers_df = SyntheticDataGenerator.generate_rfm_customers_dataset()

        cls._sales_df = sales_df
        cls._customers_df = customers_df

        if cls._forecaster is None:
            cls._forecaster = DemandForecaster()
        sup_metrics = cls._forecaster.train(sales_df)

        if cls._segmenter is None:
            cls._segmenter = CustomerSegmenter()
        unsup_metrics, _, _ = cls._segmenter.train(customers_df)

        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        return TrainingMetricsResponse(
            supervisado=sup_metrics,
            no_supervisado=unsup_metrics,
            fecha_entrenamiento=now_str,
            estado="COMPLETADO",
            tiempo_entrenamiento_ms=elapsed_ms,
            mensaje=f"Modelos entrenados exitosamente en {elapsed_ms} ms. R²={sup_metrics.r2_score}, Silhouette={unsup_metrics.silhouette_score}"
        )

    @classmethod
    def get_demand_forecast(cls, weeks_ahead: int = 4) -> DemandForecastResponse:
        """
        Obtiene la previsión de demanda semanal (datos reales + pronóstico futuro).
        """
        forecaster = cls.get_forecaster()
        sales_df = cls.get_sales_data()

        timeline = forecaster.predict_timeline(sales_df, weeks_ahead=weeks_ahead)
        metrics = forecaster.metrics or forecaster.train(sales_df)

        # Calcular proyección del próximo mes
        future_points = [p for p in timeline if p.es_proyeccion]
        total_projected_month = sum(p.demanda_proyectada_bs for p in future_points)

        past_points = [p for p in timeline if not p.es_proyeccion]
        recent_month_actual = sum(p.ventas_reales_bs or 0.0 for p in past_points[-4:]) if len(past_points) >= 4 else 1.0

        growth_pct = round(((total_projected_month - recent_month_actual) / max(1.0, recent_month_actual)) * 100, 1)

        summary_text = (
            f"El modelo RandomForestRegressor (R² = {metrics.r2_score:.2f}, RMSE = Bs. {metrics.rmse:,.2f}) "
            f"proyecta una facturación estimada de Bs. {total_projected_month:,.2f} para las próximas {weeks_ahead} semanas, "
            f"con una variación del {growth_pct:+0.1f}% respecto al periodo inmediato anterior."
        )

        return DemandForecastResponse(
            serie_temporal=timeline,
            metricas=metrics,
            resumen_ejecutivo=summary_text,
            crecimiento_proyectado_pct=growth_pct,
            total_proyectado_mes_bs=round(total_projected_month, 2)
        )

    @classmethod
    def get_customer_segments(cls) -> CustomerSegmentationResponse:
        """
        Obtiene la segmentación de clientes RFM (Clusters, Centroides y Datos de dispersión).
        """
        segmenter = cls.get_segmenter()
        customers_df = cls.get_customers_data()

        if not segmenter.cluster_centroids or segmenter.metrics is None:
            segmenter.train(customers_df)

        metrics = segmenter.metrics
        centroids = segmenter.cluster_centroids
        sample_pts = segmenter.sample_points

        vip_group = next((c for c in centroids if "VIP" in c.nombre_segmento), centroids[0])
        risk_group = next((c for c in centroids if "Riesgo" in c.nombre_segmento), centroids[-1])

        summary_text = (
            f"Algoritmo K-Means configurado con k=4 y validado con Silhouette Score de {metrics.silhouette_score:.2f}. "
            f"Se identificó un núcleo VIP ({vip_group.porcentaje_total}% de la base) con un gasto promedio de Bs. {vip_group.gasto_promedio_bs:,.2f}, "
            f"mientras que un {risk_group.porcentaje_total}% se encuentra en riesgo de abandono requiriendo acciones de reactivación."
        )

        return CustomerSegmentationResponse(
            clusters=centroids,
            puntos_muestra=sample_pts,
            metricas=metrics,
            resumen_ejecutivo=summary_text
        )

    @classmethod
    def predict_custom_demand(cls, req: DemandPredictCustomRequest) -> DemandPredictCustomResponse:
        """
        Calcula una predicción puntual de demanda según parámetros customizados.
        """
        forecaster = cls.get_forecaster()
        return forecaster.predict_custom(req)
