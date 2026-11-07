"""Company sales/stock rules and four reusable templates. No provider calls."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from app.models import Sale, SaleDetail, Product, ProductVariant, Inventory, Branch
from app.ml.analytics_data import BUSINESS_TZ, company_id, normalized_cutoff, fingerprint, _receipts
from app.schemas.analytics_recommendation import (
    ForecastInterpretation, RecommendationAction, RecommendationQuality, RecommendationResponse,
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
        # Validate the authenticated session BEFORE opening the independent reader.
        tenant = company_id(db, tenant_id)
        cutoff = normalized_cutoff(cutoff or datetime.now(timezone.utc))
        bind = db.get_bind()
        if isinstance(bind, Connection):
            # Explicit caller-owned transaction (integration fixture/maintenance).
            # No writes or commit, and no altering its isolation level mid-transaction.
            return cls._read(db, tenant, cutoff)
        with bind.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
            with Session(bind=connection, autoflush=False) as reader:
                reader.execute(text('SET TRANSACTION READ ONLY'))
                reader.info['tenant_scope'] = tenant
                return cls._read(reader, tenant, cutoff)

    @staticmethod
    def _read(db, tenant, cutoff):
        local = cutoff.astimezone(BUSINESS_TZ)
        end_day = local.date() - timedelta(days=local.weekday())
        end = datetime.combine(end_day, datetime.min.time(), BUSINESS_TZ)
        start = end - timedelta(weeks=4)
        previous = end - timedelta(weeks=8)
        # All valid observed history supplies first-sale evidence. A bounded 8-week
        # read alone would falsely treat long-established zero-selling items as new.
        receipts, invalid = _receipts(db, tenant, datetime(1970,1,1,tzinfo=timezone.utc), end)
        valid_ids = {r['id'] for r in receipts}
        variants = db.execute(select(ProductVariant.id, ProductVariant.sku, ProductVariant.talla,
            ProductVariant.color, Product.nombre, Product.en_oferta, Product.precio_oferta,
            Product.precio_base, Product.fecha_inicio_oferta, Product.fecha_fin_oferta)
            .join(Product, Product.id == ProductVariant.producto_id)
            .where(Product.tenant_id == tenant, Product.activo.is_(True), ProductVariant.activo.is_(True))).mappings().all()
        facts = {}
        for v in variants:
            offer = (v.en_oferta and v.precio_oferta is not None and 0 <= v.precio_oferta < v.precio_base
                     and (v.fecha_inicio_oferta is None or v.fecha_inicio_oferta <= cutoff)
                     and (v.fecha_fin_oferta is None or cutoff < v.fecha_fin_oferta))
            facts[v.id] = dict(variante_id=v.id, sku=v.sku, talla=v.talla, color=v.color,
                producto=v.nombre, vendidas4=0, vendidas4_anteriores=0, disponible=0, reservado=0,
                minimo_operativo=0, observados_dias=0, inventario_valido=False,
                oferta_vigente=bool(offer), sucursales=[])
        # One join for all details, never multiplied by inventory rows.
        lines = db.execute(select(SaleDetail.variante_id, SaleDetail.cantidad, Sale.id, Sale.created_at)
            .join(Sale, Sale.id == SaleDetail.venta_id)
            .where(Sale.tenant_id == tenant, Sale.created_at < end)).all()
        for variant, quantity, sale_id, when in lines:
            if sale_id not in valid_ids or variant not in facts:
                continue
            row = facts[variant]
            row['observados_dias'] = max(row['observados_dias'], (end.date() - when.astimezone(BUSINESS_TZ).date()).days)
            if when >= start:
                row['vendidas4'] += quantity
            elif when >= previous:
                row['vendidas4_anteriores'] += quantity
        # Explicit own-product/own-branch scope also detects inconsistent owner
        # references rather than letting ORM tenant filtering turn them into zero.
        stocks = db.connection().execute(select(Inventory.variante_id, Inventory.tenant_id, Inventory.stock_actual,
            Inventory.stock_reservado, Inventory.stock_minimo, Branch.id, Branch.nombre)
            .join(Branch, Branch.id == Inventory.sucursal_id)
            .join(ProductVariant, ProductVariant.id == Inventory.variante_id)
            .join(Product, Product.id == ProductVariant.producto_id)
            .where(Product.tenant_id == tenant, Branch.tenant_id == tenant,
                   Branch.activa.is_(True), Inventory.activo.is_(True))).all()
        corrupt = set()
        for variant, owner, actual, reserved, minimum, branch, name in stocks:
            if variant not in facts:
                continue
            if owner != tenant or not 0 <= reserved <= actual or minimum < 0:
                corrupt.add(variant)
                continue
            row = facts[variant]
            row['inventario_valido'] = True
            row['disponible'] += actual - reserved
            row['reservado'] += reserved
            row['minimo_operativo'] += minimum
            row['sucursales'].append(dict(id=str(branch), nombre=name, disponible=actual-reserved, reservado=reserved, minimo=minimum))
        for identifier, row in facts.items():
            row['sucursales'].sort(key=lambda b: b['id'])
            if identifier in corrupt or not row['inventario_valido']:
                row.update(inventario_valido=False, disponible=None)
        from app.ml.ml_service import MLAnalyticsService
        forecast = MLAnalyticsService.get_demand_forecast(db, tenant)
        try:
            interpretation = build_interpretation(forecast)
        except (ValueError, KeyError, TypeError, InvalidOperation):
            # A malformed historical forecast must not hide valid stock advice.
            interpretation = ForecastInterpretation(estado='SIN_ANALISIS',
                texto='Actualiza el pronóstico: su resultado guardado no permite una interpretación válida.')
        ordered = sorted(facts.values(), key=lambda f: f['sku'])
        all_actions = [make_action(row) for row in ordered]
        quality = RecommendationQuality(variantes_evaluadas=sum(a.reponer is not None for a in all_actions),
            sin_historial=sum(a.tipo == 'DATOS_INSUFICIENTES' for a in all_actions),
            inventario_inconsistente=sum(a.tipo == 'REVISAR_DATOS' for a in all_actions), ventas_excluidas=invalid,
            detalles=[a for a in all_actions if a.reponer is None])
        actions = build_actions(ordered)
        spoken = ' '.join([interpretation.texto] + [a.texto for a in actions]).replace('Bs ', 'bolivianos ')
        return RecommendationResponse(tenant_id=tenant, fecha_datos=cutoff, ventana_desde=start.date(),
            ventana_hasta=end_day, fingerprint=fingerprint(dict(tenant=tenant, window=end_day, facts=ordered,
                forecast=forecast, quality=quality.model_dump(mode='json'))),
            interpretacion=interpretation, acciones=actions, calidad=quality, texto_lectura=spoken)
