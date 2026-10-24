import random
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple


CATEGORIAS_MODA = [
    "Poleras y Camisas",
    "Pantalones y Jeans",
    "Chaquetas y Abrigos",
    "Vestidos y Faldas",
    "Calzados y Accesorios"
]

SUCURSALES_MODA = [
    "Sucursal Central",
    "Sucursal Sopocachi",
    "Sucursal Equipetrol",
    "Tienda Online"
]

TEMPORADAS_POR_MES = {
    1: "Verano", 2: "Verano", 3: "Verano",
    4: "Otoño", 5: "Otoño", 6: "Invierno",
    7: "Invierno", 8: "Invierno", 9: "Primavera",
    10: "Primavera", 11: "Primavera", 12: "Verano"
}


class SyntheticDataGenerator:
    """
    Generador estocástico y determinista de datasets sintéticos de moda retail.
    Crea distribuciones estadísticas realistas para:
    1. Previsión supervisada de demanda (series temporales semanales de ventas).
    2. Segmentación no supervisada de clientes (comportamiento RFM: Recencia, Frecuencia, Monto).
    """

    @classmethod
    def generate_sales_demand_dataset(cls, seed: int = 42) -> pd.DataFrame:
        """
        Genera histórico semanal (52 semanas) para cada sucursal y categoría con:
        - Efectos estacionales (Verano/Invierno).
        - Picos en fechas clave (Navidad, Black Friday, Día de la Madre).
        - Variables de rezago (Lag 1, Lag 2) y promedios móviles para entrenamiento supervisado.
        """
        np.random.seed(seed)
        random.seed(seed)

        records: List[Dict[str, Any]] = []

        # Generar histórico por semana (1 a 52)
        for week in range(1, 53):
            month = min(12, int(np.ceil(week / 4.34)))
            temporada = TEMPORADAS_POR_MES.get(month, "Verano")
            es_fin_de_ano = 1 if week in [48, 49, 50, 51, 52] else 0
            es_festivo = 1 if week in [19, 20, 21, 47, 51, 52] else 0  # Mayo (Día madre), Noviembre, Dic

            # Factor estacional global
            seasonal_factor = 1.0
            if es_fin_de_ano:
                seasonal_factor += 0.85
            elif es_festivo:
                seasonal_factor += 0.40

            for sucursal in SUCURSALES_MODA:
                branch_multiplier = {
                    "Sucursal Central": 1.35,
                    "Sucursal Equipetrol": 1.25,
                    "Sucursal Sopocachi": 1.0,
                    "Tienda Online": 1.45
                }.get(sucursal, 1.0)

                for categoria in CATEGORIAS_MODA:
                    base_price = {
                        "Poleras y Camisas": 120.0,
                        "Pantalones y Jeans": 240.0,
                        "Chaquetas y Abrigos": 380.0,
                        "Vestidos y Faldas": 210.0,
                        "Calzados y Accesorios": 180.0
                    }.get(categoria, 150.0)

                    # Estacionalidad específica por prenda
                    cat_season_boost = 1.0
                    if categoria == "Chaquetas y Abrigos" and temporada == "Invierno":
                        cat_season_boost = 1.75
                    elif categoria == "Poleras y Camisas" and temporada == "Verano":
                        cat_season_boost = 1.50
                    elif categoria == "Vestidos y Faldas" and temporada in ["Primavera", "Verano"]:
                        cat_season_boost = 1.40

                    # Crecimiento orgánico semanal moderado
                    trend = 1.0 + (week * 0.003)

                    # Demanda base con ruido gaussiano
                    base_units = np.random.normal(loc=35, scale=5)
                    units = max(10, int(base_units * branch_multiplier * cat_season_boost * seasonal_factor * trend))

                    total_sales_bs = round(units * base_price * np.random.uniform(0.95, 1.05), 2)

                    records.append({
                        "semana_del_ano": week,
                        "mes": month,
                        "temporada": temporada,
                        "sucursal": sucursal,
                        "categoria": categoria,
                        "es_fin_de_ano": es_fin_de_ano,
                        "es_festivo": es_festivo,
                        "precio_base": base_price,
                        "unidades_demandadas": units,
                        "ingreso_total_bs": total_sales_bs
                    })

        df = pd.DataFrame(records)

        # Construir variables de rezago (Lag Features) por serie (sucursal + categoria)
        df = df.sort_values(by=["sucursal", "categoria", "semana_del_ano"])
        df["ventas_lag1_bs"] = df.groupby(["sucursal", "categoria"])["ingreso_total_bs"].shift(1)
        df["ventas_lag2_bs"] = df.groupby(["sucursal", "categoria"])["ingreso_total_bs"].shift(2)
        df["promedio_movil_3_bs"] = df.groupby(["sucursal", "categoria"])["ingreso_total_bs"].transform(
            lambda x: x.rolling(window=3, min_periods=1).mean()
        )

        # Imputar los primeros rezagos de la serie con la media del grupo
        df["ventas_lag1_bs"] = df["ventas_lag1_bs"].fillna(df["ingreso_total_bs"])
        df["ventas_lag2_bs"] = df["ventas_lag2_bs"].fillna(df["ingreso_total_bs"])
        df["promedio_movil_3_bs"] = df["promedio_movil_3_bs"].fillna(df["ingreso_total_bs"])

        df = df.reset_index(drop=True)
        return df


    @classmethod
    def generate_rfm_customers_dataset(cls, n_samples: int = 250, seed: int = 42) -> pd.DataFrame:
        """
        Genera dataset de 250 clientes con métricas de comportamiento de compra RFM:
        - Recencia: Días desde la última compra (1 a 365).
        - Frecuencia: Número de transacciones anuales (1 a 40).
        - Monto: Gasto total en Bolivianos (Bs. 100 a Bs. 15,000).
        Diseñado con 4 clusters naturales para validación matemática con Silhouette Score > 0.55.
        """
        np.random.seed(seed)
        random.seed(seed)

        n_vip = int(n_samples * 0.15)        # 15% VIP
        n_freq = int(n_samples * 0.35)       # 35% Frecuentes
        n_new = int(n_samples * 0.30)        # 30% Nuevos / Ocasionales
        n_risk = n_samples - n_vip - n_freq - n_new  # 20% En Riesgo / Inactivos

        # 1. VIP (Baja recencia, muy alta frecuencia, gasto alto)
        r_vip = np.random.normal(loc=12, scale=5, size=n_vip).clip(2, 28)
        f_vip = np.random.normal(loc=24, scale=4, size=n_vip).clip(17, 38)
        m_vip = np.random.normal(loc=7200, scale=1200, size=n_vip).clip(4500, 14000)

        # 2. Clientes Frecuentes (Recencia moderada, frecuencia regular, gasto promedio-alto)
        r_freq = np.random.normal(loc=35, scale=10, size=n_freq).clip(14, 65)
        f_freq = np.random.normal(loc=10, scale=2.5, size=n_freq).clip(6, 16)
        m_freq = np.random.normal(loc=2400, scale=450, size=n_freq).clip(1200, 3900)

        # 3. Nuevos / Ocasionales (Recencia reciente o moderada, muy baja frecuencia, gasto bajo)
        r_new = np.random.normal(loc=22, scale=9, size=n_new).clip(3, 45)
        f_new = np.random.normal(loc=2.0, scale=0.8, size=n_new).clip(1, 4)
        m_new = np.random.normal(loc=420, scale=120, size=n_new).clip(120, 850)

        # 4. En Riesgo / Inactivos (Alta recencia > 90 días, frecuencia baja o pasada, gasto medio-bajo)
        r_risk = np.random.normal(loc=180, scale=45, size=n_risk).clip(90, 340)
        f_risk = np.random.normal(loc=3.5, scale=1.2, size=n_risk).clip(1, 6)
        m_risk = np.random.normal(loc=780, scale=220, size=n_risk).clip(200, 1600)

        recency = np.concatenate([r_vip, r_freq, r_new, r_risk])
        frequency = np.concatenate([f_vip, f_freq, f_new, f_risk])
        monetary = np.concatenate([m_vip, m_freq, m_new, m_risk])

        nombres_base = [
            "Carlos", "María", "Alejandro", "Lucía", "Mateo", "Valentina", "Sebastián",
            "Camila", "Joaquín", "Sofía", "Gabriel", "Isabella", "Daniel", "Natalia",
            "Diego", "Paula", "Andrés", "Mariana", "Fernando", "Elena", "Rodrigo", "Valeria"
        ]
        apellidos_base = [
            "Suárez", "Roca", "Mendoza", "Vaca", "Pinto", "Gutiérrez", "Chávez",
            "Aguilera", "Flores", "Torres", "Morales", "Ortiz", "Sánchez", "Rojas", "Justiniano"
        ]

        customers: List[Dict[str, Any]] = []
        for i in range(len(recency)):
            nom = f"{random.choice(nombres_base)} {random.choice(apellidos_base)}"
            c_id = f"CLI-{1000 + i}"
            customers.append({
                "cliente_id": c_id,
                "nombre": nom,
                "email": f"cliente_{1000 + i}@gmail.com",
                "recencia_dias": int(round(recency[i])),
                "frecuencia_compras": int(round(frequency[i])),
                "monto_total_bs": round(float(monetary[i]), 2),
                "ticket_promedio_bs": round(float(monetary[i]) / max(1, int(round(frequency[i]))), 2)
            })

        df = pd.DataFrame(customers)
        return df
