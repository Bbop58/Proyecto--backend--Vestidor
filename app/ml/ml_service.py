import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List, Dict, Any
from uuid import UUID
from decimal import Decimal
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.ml.synthetic_data_generator import SyntheticDataGenerator
from app.ml.supervised_model import DemandForecaster
from app.ml.unsupervised_model import CustomerSegmenter
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.inventory import Inventory
from app.models.sale_detail import SaleDetail
from app.models.sale import Sale, SaleStatus
from app.schemas.analytics import (
    TrainingMetricsResponse,
    DemandForecastResponse,
    CustomerSegmentationResponse,
    DemandPredictCustomRequest,
    DemandPredictCustomResponse,
    MarkdownRecommendation,
    ApplyMarkdownRequest,
    ApplyMarkdownResponse
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

    @classmethod
    def get_markdown_recommendations(cls, db: Session, tenant_id: UUID) -> List[MarkdownRecommendation]:
        """
        Analiza las existencias físicas de inventario y la tasa de rotación histórica
        para generar recomendaciones inteligentes de liquidación y descuentos predictivos.
        """
        products = (
            db.query(Product)
            .filter(Product.tenant_id == tenant_id, Product.activo == True)
            .all()
        )

        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        recommendations: List[MarkdownRecommendation] = []

        for p in products:
            # Calcular stock total físico entre todas las sucursales
            total_stock = 0
            if p.variantes:
                variant_ids = [v.id for v in p.variantes]
                stock_sum = (
                    db.query(func.coalesce(func.sum(Inventory.stock_actual), 0))
                    .filter(Inventory.variante_id.in_(variant_ids), Inventory.activo == True)
                    .scalar()
                )
                total_stock = int(stock_sum or 0)

            # Calcular unidades vendidas en los últimos 30 días
            units_sold_30d = 0
            if p.variantes:
                variant_ids = [v.id for v in p.variantes]
                sales_count = (
                    db.query(func.coalesce(func.sum(SaleDetail.cantidad), 0))
                    .join(Sale, SaleDetail.venta_id == Sale.id)
                    .filter(
                        SaleDetail.variante_id.in_(variant_ids),
                        Sale.estado == SaleStatus.COMPLETADA,
                        Sale.created_at >= thirty_days_ago
                    )
                    .scalar()
                )
                units_sold_30d = int(sales_count or 0)

            # Si el producto tiene stock físico significativo (> 5 unidades)
            if total_stock >= 5:
                # Ritmo de venta diaria y días de inventario proyectado
                daily_rate = units_sold_30d / 30.0
                if daily_rate > 0:
                    projected_days = int(total_stock / daily_rate)
                else:
                    projected_days = 150  # Prácticamente estancado

                # Evaluar si califica para recomendación de liquidación
                # (o si lleva más de 45 días de inventario proyectado)
                if projected_days >= 40 or p.en_oferta:
                    # Determinar urgencia y descuento óptimo
                    if projected_days >= 90 or units_sold_30d == 0:
                        urgencia = "ALTO"
                        sug_discount = 25.0
                        caida_demanda = 65.0
                    elif projected_days >= 60:
                        urgencia = "MEDIO"
                        sug_discount = 20.0
                        caida_demanda = 45.0
                    else:
                        urgencia = "MODERADO"
                        sug_discount = 15.0
                        caida_demanda = 30.0

                    base_price = float(p.precio_base)
                    offer_price = round(base_price * (1.0 - (sug_discount / 100.0)), 2)
                    ahorro_bs = round(base_price - offer_price, 2)
                    dinero_inmovilizado = round(total_stock * base_price, 2)

                    start_date = (now + timedelta(days=2)).strftime("%Y-%m-%d")
                    end_date = (now + timedelta(days=16)).strftime("%Y-%m-%d")

                    # Estado
                    if p.en_oferta:
                        estado = "ACTIVA"
                        diag = (
                            f"Prenda actualmente en promoción ({p.precio_oferta} Bs.). "
                            f"El stock actual es de {total_stock} unidades con una rotación de {units_sold_30d} ventas en el último mes. "
                            f"Se recomienda mantener la oferta hasta agotar remanente de temporada."
                        )
                    else:
                        estado = "SUGERIDA"
                        diag = (
                            f"El modelo predictivo detecta una velocidad de rotación baja ({units_sold_30d} unidades en 30 días) "
                            f"frente a un stock acumulado de {total_stock} unidades. A este ritmo tardará aproximadamente {projected_days} días en agotarse, "
                            f"inmovilizando Bs. {dinero_inmovilizado:,.2f} en capital de trabajo. Se sugiere programar una oferta de {sug_discount:.0f}% OFF "
                            f"para impulsar la salida de inventario antes de fin de mes."
                        )

                    cat_nombre = p.categoria.nombre if p.categoria else "Indumentaria"

                    recommendations.append(
                        MarkdownRecommendation(
                            producto_id=str(p.id),
                            producto_nombre=p.nombre,
                            categoria=cat_nombre,
                            imagen_url=p.imagen_url,
                            stock_total=total_stock,
                            precio_base=base_price,
                            precio_oferta_actual=float(p.precio_oferta) if p.precio_oferta else None,
                            en_oferta_actualmente=p.en_oferta,
                            dias_inventario_proyectado=projected_days,
                            caida_demanda_pct=caida_demanda,
                            descuento_sugerido_pct=sug_discount,
                            precio_oferta_sugerido=offer_price,
                            ahorro_estimado_bs=ahorro_bs,
                            fecha_inicio_sugerida=start_date,
                            fecha_fin_sugerida=end_date,
                            diagnostico_ia=diag,
                            nivel_urgencia=urgencia,
                            estado=estado
                        )
                    )

        # Ordenar: primero las de mayor urgencia y mayor stock
        recommendations.sort(key=lambda x: (0 if x.nivel_urgencia == "ALTO" else 1 if x.nivel_urgencia == "MEDIO" else 2, -x.stock_total))
        return recommendations

    @classmethod
    def apply_markdown_recommendation(
        cls,
        db: Session,
        tenant_id: UUID,
        product_id: UUID,
        payload: ApplyMarkdownRequest
    ) -> ApplyMarkdownResponse:
        """
        Aplica o programa una oferta sugerida por Machine Learning sobre un producto.
        """
        product = (
            db.query(Product)
            .filter(Product.id == product_id, Product.tenant_id == tenant_id)
            .first()
        )
        if not product:
            raise ValueError("Producto no encontrado o no pertenece a este tenant.")

        base_price = float(product.precio_base)

        if payload.precio_oferta:
            offer_price = round(payload.precio_oferta, 2)
            discount_pct = round(((base_price - offer_price) / base_price) * 100.0, 1)
        elif payload.descuento_pct:
            discount_pct = round(payload.descuento_pct, 1)
            offer_price = round(base_price * (1.0 - (discount_pct / 100.0)), 2)
        else:
            discount_pct = 20.0
            offer_price = round(base_price * 0.80, 2)

        # Parsear fechas si vienen especificadas
        start_dt = None
        end_dt = None
        if payload.fecha_inicio:
            try:
                start_dt = datetime.strptime(payload.fecha_inicio, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                pass
        if payload.fecha_fin:
            try:
                end_dt = datetime.strptime(payload.fecha_fin, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                pass

        product.en_oferta = True
        product.precio_oferta = Decimal(str(offer_price))
        product.fecha_inicio_oferta = start_dt
        product.fecha_fin_oferta = end_dt
        product.motivo_oferta = payload.motivo or f"Liquidación predictiva ML ({discount_pct:.0f}% OFF)"
        product.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(product)

        return ApplyMarkdownResponse(
            success=True,
            mensaje=f"Oferta aprobada exitosamente para '{product.nombre}'. Nuevo precio: Bs. {offer_price:.2f} ({discount_pct:.0f}% OFF).",
            producto_id=str(product.id),
            producto_nombre=product.nombre,
            precio_base=base_price,
            precio_oferta=offer_price,
            descuento_pct=discount_pct,
            fecha_inicio=payload.fecha_inicio,
            fecha_fin=payload.fecha_fin
        )

    @classmethod
    def dismiss_markdown_recommendation(
        cls,
        db: Session,
        tenant_id: UUID,
        product_id: UUID
    ) -> bool:
        """
        Descarta o retira la oferta de un producto manteniéndolo a precio regular.
        """
        product = (
            db.query(Product)
            .filter(Product.id == product_id, Product.tenant_id == tenant_id)
            .first()
        )
        if not product:
            return False

        product.en_oferta = False
        product.precio_oferta = None
        product.fecha_inicio_oferta = None
        product.fecha_fin_oferta = None
        product.motivo_oferta = None
        product.updated_at = datetime.now(timezone.utc)

        db.commit()
        return True
