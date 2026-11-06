"""Closed server registry, not arbitrary marketing flags."""
from types import MappingProxyType

PLAN_FEATURES = MappingProxyType({
    'reportes_ia': 'permite_reportes_ia',
    'analitica_avanzada': 'permite_analitica_avanzada',
    'offline': 'permite_offline',
})


def feature_values(row):
    return {key:row[column] for key,column in PLAN_FEATURES.items()}
