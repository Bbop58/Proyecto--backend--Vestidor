"""Company sales/stock rules and four reusable templates. No provider calls."""
from decimal import Decimal, InvalidOperation
from app.schemas.analytics_recommendation import (
    ForecastInterpretation, RecommendationAction, RecommendationResponse,
)


def render_action(action: RecommendationAction) -> str:
    name = action.producto
    if action.talla:
        name += f', talla {action.talla}'
    if action.color:
        name += f', color {action.color}'
    if action.tipo == 'REPONER':
        unit = 'unidad' if action.reponer == 1 else 'unidades'
        value = (f'Reponer {action.reponer} {unit} de {name}. '
                 f'Se vendieron {action.vendidas4} unidades en las últimas cuatro semanas cerradas; '
                 f'quedan {action.disponible} disponibles y el objetivo es {action.stock_objetivo}.')
    elif action.tipo == 'EVALUAR_OFERTA':
        verb = 'Revisar la oferta vigente' if action.oferta_vigente else 'Evaluar una promoción'
        value = (f'{verb} de {name}: quedan {action.disponible} unidades y se vendieron '
                 f'{action.vendidas4} en las últimas cuatro semanas cerradas, frente a '
                 f'{action.vendidas4_anteriores} en las cuatro anteriores.')
    elif action.tipo == 'MANTENER':
        value = (f'Mantener las existencias de {name}: hay {action.disponible} unidades disponibles '
                 f'y el objetivo según ventas recientes y mínimo operativo es {action.stock_objetivo}; '
                 'no hace falta reponer por ahora.')
    else:
        reason = 'falta historial suficiente de ventas' if action.tipo == 'DATOS_INSUFICIENTES' else 'falta inventario válido o presenta inconsistencias'
        return f'No se puede calcular una recomendación para {name}: {reason}.'
    if 'MINIMO_DOMINANTE' in action.motivos:
        value += ' El objetivo está determinado por el mínimo operativo configurado.'
    return value


def make_action(fact) -> RecommendationAction:
    fields = {key: fact[key] for key in (
        'variante_id', 'producto', 'sku', 'talla', 'color', 'vendidas4',
        'vendidas4_anteriores', 'disponible', 'reservado', 'minimo_operativo',
        'oferta_vigente', 'sucursales')}
    reasons = []
    target = quantity = None
    if not fact['inventario_valido']:
        kind = 'REVISAR_DATOS'
    elif fact['observados_dias'] < 28:
        kind = 'DATOS_INSUFICIENTES'
    else:
        pace = (fact['vendidas4'] * 5 + 3) // 4  # exact integer ceil(units * 1.25)
        target = max(pace, fact['minimo_operativo'])
        quantity = max(0, target - fact['disponible'])
        if fact['minimo_operativo'] > pace:
            reasons.append('MINIMO_DOMINANTE')
        if quantity:
            kind = 'REPONER'
        elif (fact['observados_dias'] >= 56 and fact['disponible'] > target
              and fact['disponible'] >= 3 * max(fact['vendidas4'], 1)
              and fact['vendidas4'] <= fact['vendidas4_anteriores']):
            kind = 'EVALUAR_OFERTA'
        else:
            kind = 'MANTENER'
    action = RecommendationAction(**fields, tipo=kind, stock_objetivo=target,
                                  reponer=quantity, motivos=reasons)
    action.texto = render_action(action)
    return action


def build_actions(facts) -> list[RecommendationAction]:
    actions = [make_action(row) for row in facts]
    replenish = sorted((a for a in actions if a.tipo == 'REPONER'), key=lambda a: (-a.reponer, a.sku))
    excess = sorted((a for a in actions if a.tipo == 'EVALUAR_OFERTA'), key=lambda a: (-a.disponible, a.sku))
    maintain = sorted((a for a in actions if a.tipo == 'MANTENER'), key=lambda a: a.sku)
    selected = replenish[:2] + excess[:1]
    chosen = {a.variante_id for a in selected}
    selected += [a for a in replenish + excess + maintain if a.variante_id not in chosen][:3-len(selected)]
    return selected


def build_interpretation(saved_forecast) -> ForecastInterpretation:
    cutoff = saved_forecast.get('fecha_corte')
    if saved_forecast.get('estado') != 'LISTO' or not saved_forecast.get('resultado'):
        return ForecastInterpretation(texto='Actualiza el pronóstico para consultar su interpretación.', estado='SIN_ANALISIS', fecha_corte=cutoff)
    if saved_forecast.get('datos_cambiaron'):
        return ForecastInterpretation(texto=f'El pronóstico del corte {str(cutoff)[:10]} está desactualizado. Actualízalo antes de interpretar su importe.', estado='DESACTUALIZADO', fecha_corte=cutoff)
    result = saved_forecast['resultado']
    amount = Decimal(str(result['total_proyectado_bs']))
    if not amount.is_finite() or amount < 0:
        raise ValueError('Invalid forecast amount')
    formatted = f'{amount:,.2f}'.translate(str.maketrans(',.', '.,'))
    labels = {'RANDOM_FOREST': 'Random Forest', 'ULTIMA_SEMANA': 'El método de referencia última semana',
              'MEDIA_PREVIA': 'El método de referencia media de tres semanas'}
    method = labels[result['metodo']]
    return ForecastInterpretation(texto=f'Se estiman Bs {formatted} para cuatro semanas. {method} obtuvo el menor error histórico entre los métodos evaluados; es una estimación, no una garantía.', estado='VIGENTE', fecha_corte=cutoff)


class AnalyticsRecommendationService:
    @classmethod
    def read(cls, db, tenant_id, cutoff=None) -> RecommendationResponse:
        # Transitional aggregate contract for the currently deployed web. Both
        # legacy and v2 derive from the SAME complete branch snapshot.
        from app.services.analytics_branch_recommendations import read_recommendation_snapshot
        return read_recommendation_snapshot(db, tenant_id, cutoff).legacy
