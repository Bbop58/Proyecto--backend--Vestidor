import os
import joblib
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple, Optional
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

from app.schemas.analytics import (
    UnsupervisedModelMetrics,
    CustomerClusterCentroid,
    CustomerDataPoint
)


RFM_FEATURES = ["recencia_dias", "frecuencia_compras", "monto_total_bs"]


CLUSTER_META_CONFIG = {
    "VIP": {
        "nombre": "Clientes VIP / Alta Fidelidad",
        "color": "#f59e0b",  # Amber / Gold
        "estrategia": "Ofrecer preventas exclusivas de colecciones, trato prioritario y regalos de lealtad."
    },
    "FRECUENTES": {
        "nombre": "Compradores Frecuentes",
        "color": "#3b82f6",  # Blue
        "estrategia": "Incentivar incremento de ticket promedio mediante ventas cruzadas y puntos acumulables."
    },
    "NUEVOS": {
        "nombre": "Nuevos / Ocasionales",
        "color": "#10b981",  # Emerald Green
        "estrategia": "Enviar cupón de bienvenida para incentivar su segunda compra dentro de los primeros 30 días."
    },
    "EN_RIESGO": {
        "nombre": "Clientes en Riesgo de Abandono",
        "color": "#ef4444",  # Red
        "estrategia": "Campaña de reactivación urgente con promociones personalizadas y recordatorio de novedades."
    }
}


class CustomerSegmenter:
    """
    Modelo de Aprendizaje No Supervisado para Segmentación de Clientes.
    Implementa el modelo RFM (Recency, Frequency, Monetary) con StandardScaler y K-Means.
    """

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or os.path.join(
            os.path.dirname(__file__), "artifacts", "customer_segmentation_model.joblib"
        )
        self.scaler: Optional[StandardScaler] = None
        self.kmeans: Optional[KMeans] = None
        self.metrics: Optional[UnsupervisedModelMetrics] = None
        self.cluster_centroids: List[CustomerClusterCentroid] = []
        self.sample_points: List[CustomerDataPoint] = []

    def train(self, df: pd.DataFrame, n_clusters: int = 4) -> Tuple[UnsupervisedModelMetrics, List[CustomerClusterCentroid], List[CustomerDataPoint]]:
        """
        Entrena el agrupador K-Means sobre el espacio RFM normalizado,
        asigna etiquetas semánticas y calcula el Silhouette Score.
        """
        X = df[RFM_FEATURES].copy().values

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.kmeans = KMeans(
            n_clusters=n_clusters,
            init="k-means++",
            n_init=10,
            max_iter=300,
            random_state=42
        )
        labels = self.kmeans.fit_predict(X_scaled)
        df_clustered = df.copy()
        df_clustered["cluster"] = labels

        # Evaluación matemática formal de separación de grupos
        sil_score = float(silhouette_score(X_scaled, labels))
        inertia = float(self.kmeans.inertia_)

        self.metrics = UnsupervisedModelMetrics(
            silhouette_score=round(sil_score, 4),
            inercia_wss=round(inertia, 2),
            n_clusters=n_clusters,
            total_clientes_analizados=len(df)
        )

        # Caracterización semántica de los centroides
        raw_centers = self.scaler.inverse_transform(self.kmeans.cluster_centers_)
        total_customers = len(df)

        # Identificar dinámicamente el rol de cada centroide según sus medias RFM
        cluster_info = []
        for c_id in range(n_clusters):
            c_data = df_clustered[df_clustered["cluster"] == c_id]
            mean_r = float(c_data["recencia_dias"].mean())
            mean_f = float(c_data["frecuencia_compras"].mean())
            mean_m = float(c_data["monto_total_bs"].mean())
            count_c = len(c_data)
            pct = round((count_c / total_customers) * 100, 1)

            cluster_info.append({
                "id": c_id,
                "r": mean_r,
                "f": mean_f,
                "m": mean_m,
                "count": count_c,
                "pct": pct
            })

        # Mapear cada cluster a su arquetipo de negocio
        assigned_clusters: Dict[int, str] = {}
        # 1. VIP = Mayor monto
        sorted_by_monto = sorted(cluster_info, key=lambda x: x["m"], reverse=True)
        vip_c = sorted_by_monto[0]["id"]
        assigned_clusters[vip_c] = "VIP"

        # 2. En Riesgo = Mayor recencia (entre los no VIP)
        non_vip = [c for c in cluster_info if c["id"] != vip_c]
        sorted_by_recency = sorted(non_vip, key=lambda x: x["r"], reverse=True)
        risk_c = sorted_by_recency[0]["id"]
        assigned_clusters[risk_c] = "EN_RIESGO"

        # 3. Frecuentes vs Nuevos
        remaining = [c for c in non_vip if c["id"] != risk_c]
        sorted_by_freq = sorted(remaining, key=lambda x: x["f"], reverse=True)
        freq_c = sorted_by_freq[0]["id"]
        new_c = sorted_by_freq[1]["id"] if len(sorted_by_freq) > 1 else remaining[0]["id"]
        assigned_clusters[freq_c] = "FRECUENTES"
        assigned_clusters[new_c] = "NUEVOS"

        # Construir objetos de centroides
        self.cluster_centroids = []
        for c_item in cluster_info:
            c_id = c_item["id"]
            archetype = assigned_clusters.get(c_id, "FRECUENTES")
            meta = CLUSTER_META_CONFIG[archetype]

            self.cluster_centroids.append(CustomerClusterCentroid(
                cluster_id=c_id,
                nombre_segmento=meta["nombre"],
                color_hex=meta["color"],
                total_clientes=c_item["count"],
                porcentaje_total=c_item["pct"],
                recencia_promedio_dias=round(c_item["r"], 1),
                frecuencia_promedio_compras=round(c_item["f"], 1),
                gasto_promedio_bs=round(c_item["m"], 2),
                ticket_promedio_bs=round(c_item["m"] / max(1.0, c_item["f"]), 2),
                estrategia_recomendada=meta["estrategia"]
            ))

        # Puntos de muestra para graficación (Scatter plot en Frontend)
        self.sample_points = []
        sample_subset = df_clustered.sample(min(150, len(df_clustered)), random_state=42)
        for _, row in sample_subset.iterrows():
            c_id = int(row["cluster"])
            arch = assigned_clusters.get(c_id, "FRECUENTES")
            self.sample_points.append(CustomerDataPoint(
                cliente_id=str(row["cliente_id"]),
                nombre=str(row["nombre"]),
                recencia_dias=int(row["recencia_dias"]),
                frecuencia_compras=int(row["frecuencia_compras"]),
                monto_total_bs=round(float(row["monto_total_bs"]), 2),
                cluster_id=c_id,
                cluster_nombre=CLUSTER_META_CONFIG[arch]["nombre"]
            ))

        # Serialización de artefactos
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        joblib.dump({
            "scaler": self.scaler,
            "kmeans": self.kmeans,
            "metrics": self.metrics,
            "centroids": self.cluster_centroids,
            "sample_points": self.sample_points
        }, self.model_path)

        return self.metrics, self.cluster_centroids, self.sample_points

    def load(self) -> bool:
        """Carga artefactos serializados si existen."""
        if os.path.exists(self.model_path):
            try:
                data = joblib.load(self.model_path)
                self.scaler = data.get("scaler")
                self.kmeans = data.get("kmeans")
                self.metrics = data.get("metrics")
                self.cluster_centroids = data.get("centroids", [])
                self.sample_points = data.get("sample_points", [])
                return True
            except Exception:
                return False
        return False
