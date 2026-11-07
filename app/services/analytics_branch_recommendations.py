"""Complete branch/variant snapshot; highlights never remove report rows."""
from datetime import datetime, timedelta, timezone
from decimal import InvalidOperation
from fractions import Fraction
from collections import defaultdict
from sqlalchemy import select, text, and_
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from app.models import Product, ProductVariant, Branch, Sale, SaleDetail, Inventory, Tenant
from app.ml.analytics_data import BUSINESS_TZ, _receipts, company_id, normalized_cutoff, fingerprint
from app.schemas.analytics_recommendation import (
    BranchRecommendationCase, BranchRecommendationSnapshot, BranchRecommendationSummary,
    BranchRecommendationTotals, ForecastInterpretation, RecommendationQuality,
    RecommendationResponse, RecommendationSummaryV2, VariantInventoryCoverage,
)
from app.services.analytics_recommendation_service import make_action, build_actions, build_interpretation


def read_recommendation_snapshot(db, tenant_id, cutoff=None) -> BranchRecommendationSnapshot:
    tenant = company_id(db, tenant_id)
    cutoff = normalized_cutoff(cutoff or datetime.now(timezone.utc))
    bind = db.get_bind()
    if isinstance(bind, Connection):
        with db.no_autoflush:
            return _read_snapshot(db, tenant, cutoff)
    with bind.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with Session(bind=connection, autoflush=False) as reader:
            reader.execute(text('SET TRANSACTION READ ONLY'))
            reader.info['tenant_scope'] = tenant
            return _read_snapshot(reader, tenant, cutoff)


def _counts(cases):
    names = {'REPONER':'reponer','EVALUAR_OFERTA':'evaluar_oferta','MANTENER':'mantener',
             'DATOS_INSUFICIENTES':'sin_historial','REVISAR_DATOS':'revisar_datos'}
    counts = BranchRecommendationTotals(total=len(cases),
        variantes_unicas=len({c.variante_id for c in cases}), productos_unicos=len({c.producto_id for c in cases}))
    for c in cases:
        field = names[c.tipo]
        setattr(counts, field, getattr(counts, field)+1)
    return counts


def _case_key(case):
    if case.tipo == 'REPONER':
        return (not (case.disponible == 0 and case.vendidas4 > 0),
                -Fraction(case.reponer, case.stock_objetivo), -case.vendidas4, case.sku, str(case.variante_id))
    if case.tipo == 'EVALUAR_OFERTA':
        return (case.vendidas4, -case.exceso, case.sku, str(case.variante_id))
    return (-case.vendidas4, case.sku, str(case.variante_id))


def summarize_snapshot(snapshot: BranchRecommendationSnapshot) -> RecommendationSummaryV2:
    groups = defaultdict(list)
    for case in snapshot.casos:
        if case.sucursal_id is not None and case.tipo in {'REPONER','EVALUAR_OFERTA','MANTENER'}:
            groups[(case.sucursal_id,case.tipo)].append(case)
    highlights = [min(rows,key=_case_key) for rows in groups.values()]
    order = {'REPONER':0,'EVALUAR_OFERTA':1,'MANTENER':2}
    highlights.sort(key=lambda c:(c.sucursal_nombre.casefold(),str(c.sucursal_id),order[c.tipo]))
    return RecommendationSummaryV2(**snapshot.legacy.model_dump(), empresa_nombre=snapshot.empresa_nombre,
        totales=snapshot.totales,
        sucursales_resumen=snapshot.sucursales_resumen, destacados=highlights,
        resumen_operativo=snapshot.resumen_operativo)


def _read_snapshot(db, tenant, cutoff):
    local = cutoff.astimezone(BUSINESS_TZ)
    end_day = local.date()-timedelta(days=local.weekday())
    end = datetime.combine(end_day,datetime.min.time(),BUSINESS_TZ)
    start, previous = end-timedelta(weeks=4), end-timedelta(weeks=8)
    catalog = db.execute(select(ProductVariant.id,ProductVariant.sku,ProductVariant.talla,
        ProductVariant.color,Product.id.label('product_id'),Product.nombre,Product.en_oferta,
        Product.precio_oferta,Product.precio_base,Product.fecha_inicio_oferta,Product.fecha_fin_oferta)
        .join(Product,Product.id==ProductVariant.producto_id)
        .where(Product.tenant_id==tenant,Product.activo.is_(True),ProductVariant.activo.is_(True))).mappings().all()
    variants = {v.id:v for v in catalog}
    directory = db.execute(select(Branch.id,Branch.nombre,Tenant.nombre.label('empresa_nombre'))
        .select_from(Tenant).outerjoin(Branch,and_(Branch.tenant_id==Tenant.id,Branch.activa.is_(True)))
        .where(Tenant.id==tenant).order_by(Branch.nombre,Branch.id)).all()
    company_name=directory[0].empresa_nombre
    branches=[b for b in directory if b.id is not None]
    branch_names = {b.id:b.nombre for b in branches}
    facts = {}

    def ensure(branch, variant):
        key = (branch,variant)
        if key not in facts:
            v = variants[variant]
            offer = (v.en_oferta and v.precio_oferta is not None and 0<=v.precio_oferta<v.precio_base
                and (v.fecha_inicio_oferta is None or v.fecha_inicio_oferta<=cutoff)
                and (v.fecha_fin_oferta is None or cutoff<v.fecha_fin_oferta))
            facts[key] = dict(variante_id=variant,producto_id=v.product_id,sku=v.sku,producto=v.nombre,
                talla=v.talla,color=v.color,sucursal_id=branch,sucursal_nombre=branch_names.get(branch),
                vendidas4=0,vendidas4_anteriores=0,disponible=None,reservado=0,minimo_operativo=0,
                stock_actual=None,inventario_valido=False,observados_dias=0,observado_desde=None,
                oferta_vigente=bool(offer),sucursales=[])
        return facts[key]

    valid, invalid = _receipts(db,tenant,datetime(1970,1,1,tzinfo=timezone.utc),end)
    valid_ids = {s['id'] for s in valid}
    lines = db.execute(select(Sale.id,Sale.sucursal_id,Sale.created_at,SaleDetail.variante_id,SaleDetail.cantidad)
        .join(SaleDetail,SaleDetail.venta_id==Sale.id)
        .where(Sale.tenant_id==tenant,Sale.created_at<end)).all()
    for sale, branch, when, variant, units in lines:
        if sale not in valid_ids or branch not in branch_names or variant not in variants:
            continue
        row=ensure(branch,variant)
        day=when.astimezone(BUSINESS_TZ).date()
        row['observado_desde']=min(row['observado_desde'] or day,day)
        row['observados_dias']=(end_day-row['observado_desde']).days
        if when>=start:row['vendidas4']+=units
        elif when>=previous:row['vendidas4_anteriores']+=units

    # Explicit own-branch/own-product predicates retain evidence of corrupt
    # Inventory.tenant_id without accepting foreign stock or broad ORM bypass.
    inventories = db.connection().execute(select(Inventory.variante_id,Inventory.sucursal_id,
        Inventory.tenant_id,Inventory.stock_actual,Inventory.stock_reservado,Inventory.stock_minimo)
        .join(Branch,Branch.id==Inventory.sucursal_id)
        .join(ProductVariant,ProductVariant.id==Inventory.variante_id)
        .join(Product,Product.id==ProductVariant.producto_id)
        .where(Branch.tenant_id==tenant,Branch.activa.is_(True),Product.tenant_id==tenant,
            Product.activo.is_(True),ProductVariant.activo.is_(True),Inventory.activo.is_(True))).all()
    for variant,branch,owner,actual,reserved,minimum in inventories:
        row=ensure(branch,variant)
        if owner==tenant and 0<=reserved<=actual and minimum>=0:
            row.update(inventario_valido=True,stock_actual=actual,disponible=actual-reserved,
                reservado=reserved,minimo_operativo=minimum)
    # Unassigned catalogue is a single quality row, NOT a fictional zero-stock
    # case for every branch. Historical own-branch sales without inventory above
    # instead retain their exact branch identity for review.
    assigned = {v for _,v in facts}
    for variant in variants:
        if variant not in assigned:ensure(None,variant)

    cases=[]
    for fact in sorted(facts.values(),key=lambda f:(f['sucursal_nombre'] or '',str(f['sucursal_id']),f['sku'])):
        action=make_action(fact)
        case=BranchRecommendationCase(**action.model_dump(),producto_id=fact['producto_id'],
            sucursal_id=fact['sucursal_id'],sucursal_nombre=fact['sucursal_nombre'],
            stock_actual=fact['stock_actual'],observado_desde=fact['observado_desde'],observados_dias=fact['observados_dias'],
            exceso=max(0,fact['disponible']-action.stock_objetivo) if action.stock_objetivo is not None else None,
            alerta_minimo=fact['inventario_valido'] and fact['disponible']<fact['minimo_operativo'])
        if not fact['inventario_valido']:
            case.reservado=case.minimo_operativo=None
        cases.append(case)

    by_variant=defaultdict(list)
    for case in cases:by_variant[case.variante_id].append(case)
    coverage=[]
    for variant, rows in sorted(by_variant.items(),key=lambda item:variants[item[0]].sku):
        complete=all(c.stock_objetivo is not None and c.disponible is not None for c in rows)
        available=sum(c.disponible for c in rows) if all(c.disponible is not None for c in rows) else None
        target=sum(c.stock_objetivo for c in rows) if complete else None
        coverage.append(VariantInventoryCoverage(variante_id=variant,sku=variants[variant].sku,disponible=available,
            objetivo=target,compra_sugerida=max(0,target-available) if complete else None,datos_completos=complete))
        for case in rows:
            case.otras_sucursales=[dict(id=str(c.sucursal_id),nombre=c.sucursal_nombre,disponible=c.disponible,
                objetivo=c.stock_objetivo,exceso=c.exceso) for c in rows if c.sucursal_id is not None and c.sucursal_id!=case.sucursal_id]
            if case.tipo=='REPONER' and any(c.exceso is not None and c.exceso>0 for c in rows if c.sucursal_id!=case.sucursal_id):
                case.revisar_otras_sucursales=True
                case.texto+=' Revisa existencias en otras sucursales antes de comprar.'
                case.motivos.append('REVISAR_EXCEDENTES_PROPIOS')

    from app.ml.ml_service import MLAnalyticsService
    forecast=MLAnalyticsService.get_demand_forecast(db,tenant)
    try:interpretation=build_interpretation(forecast)
    except (ValueError,KeyError,TypeError,InvalidOperation):
        interpretation=ForecastInterpretation(estado='SIN_ANALISIS',texto='Actualiza el pronóstico: su resultado guardado no permite una interpretación válida.')
    totals=_counts(cases)
    branch_summaries=[BranchRecommendationSummary(id=b.id,nombre=b.nombre,
        totales=_counts([c for c in cases if c.sucursal_id==b.id])) for b in branches]
    first=(interpretation.texto.partition('. ')[0]+'.') if interpretation.estado=='VIGENTE' else (
        'El pronóstico requiere actualizarse.' if interpretation.estado=='DESACTUALIZADO' else 'Actualiza el pronóstico para estimar las ventas.')
    affected=sum(b.totales.reponer>0 for b in branch_summaries)
    summary=(f'{first} Hay {totals.reponer} casos de reposición en {affected} sucursales, '
        f'{totals.evaluar_oferta} de exceso y {totals.mantener} para mantener; '
        f'{totals.sin_historial+totals.revisar_datos} requieren revisar datos.')
    digest=fingerprint(dict(tenant=tenant,empresa_nombre=company_name,
        branches=[dict(id=b.id,nombre=b.nombre) for b in branches],window=end_day,facts=sorted(facts.values(),key=lambda f:(str(f['sucursal_id']),f['sku'])),forecast=forecast))
    legacy=_legacy_response(tenant,cutoff,start.date(),end_day,digest,interpretation,facts,variants,invalid)
    return BranchRecommendationSnapshot(tenant_id=tenant,empresa_nombre=company_name,
        fecha_datos=cutoff,ventana_desde=start.date(),
        ventana_hasta=end_day,fingerprint=digest,interpretacion=interpretation,resumen_operativo=summary,
        ventas_excluidas=invalid,totales=totals,sucursales_resumen=branch_summaries,casos=cases,
        cobertura_empresa=coverage,legacy=legacy)


def _legacy_response(tenant,cutoff,start,end,digest,interpretation,facts,variants,invalid):
    # Only the transitional v1 projection remains company-aggregated. No second
    # SQL reader or competing snapshot; report-data retains every branch case.
    aggregate=[]
    for variant,v in variants.items():
        rows=[row for row in facts.values() if row['variante_id']==variant]
        valid=bool(rows) and all(row['inventario_valido'] for row in rows)
        aggregate.append(dict(variante_id=variant,producto=v.nombre,sku=v.sku,talla=v.talla,color=v.color,
            vendidas4=sum(r['vendidas4'] for r in rows),vendidas4_anteriores=sum(r['vendidas4_anteriores'] for r in rows),
            disponible=sum(r['disponible'] for r in rows) if valid else None,reservado=sum(r['reservado'] for r in rows),
            minimo_operativo=sum(r['minimo_operativo'] for r in rows),inventario_valido=valid,
            observados_dias=max(r['observados_dias'] for r in rows),oferta_vigente=rows[0]['oferta_vigente'],
            sucursales=[dict(id=str(r['sucursal_id']),nombre=r['sucursal_nombre'],disponible=r['disponible'],
                reservado=r['reservado'],minimo=r['minimo_operativo']) for r in rows if r['inventario_valido']]))
    all_actions=[make_action(row) for row in aggregate]
    quality=RecommendationQuality(variantes_evaluadas=sum(a.reponer is not None for a in all_actions),
        sin_historial=sum(a.tipo=='DATOS_INSUFICIENTES' for a in all_actions),
        inventario_inconsistente=sum(a.tipo=='REVISAR_DATOS' for a in all_actions),ventas_excluidas=invalid,
        detalles=[a for a in all_actions if a.reponer is None])
    actions=build_actions(aggregate)
    spoken=' '.join([interpretation.texto]+[a.texto for a in actions]).replace('Bs ','bolivianos ')
    return RecommendationResponse(tenant_id=tenant,fecha_datos=cutoff,ventana_desde=start,
        ventana_hasta=end,fingerprint=digest,interpretacion=interpretation,acciones=actions,calidad=quality,texto_lectura=spoken)
