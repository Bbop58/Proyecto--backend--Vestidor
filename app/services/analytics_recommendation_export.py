"""Full, literal-cell XLSX from the authoritative recommendation snapshot."""
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

MAX_EXPORT_ROWS = 20000
KIND_LABELS = {'REPONER':'Reponer','EVALUAR_OFERTA':'Evaluar oferta','MANTENER':'Mantener',
               'DATOS_INSUFICIENTES':'Datos insuficientes','REVISAR_DATOS':'Revisar datos'}
HEADERS = ['Sucursal','Producto','SKU','Talla','Color','Ventas 4 semanas','Ventas 4 anteriores',
           'Stock físico','Reservado','Disponible','Mínimo','Objetivo','Reposición local','Exceso',
           'Categoría','Recomendación','Primera venta observada','Días observados','Alerta mínimo',
           'Revisar otras sucursales','ID sucursal','ID variante']


def _append(sheet, values):
    sheet.append(values)
    for cell, value in zip(sheet[sheet.max_row], values):
        if isinstance(value, str):
            # openpyxl recognizes leading '=' as a formula unless explicitly
            # stored as a literal string. Preserve the user's exact catalogue.
            cell.data_type = 's'
        cell.alignment = Alignment(vertical='top',wrap_text=True)


def export_recommendation_xlsx(snapshot) -> bytes:
    if len(snapshot.casos) > MAX_EXPORT_ROWS:
        raise HTTPException(413,'El informe supera el tamaño permitido; no se generó un archivo parcial.')
    wb = Workbook()
    summary = wb.active
    summary.title = 'Resumen'
    _append(summary,['Informe completo de recomendaciones',snapshot.empresa_nombre])
    _append(summary,['Consulta (Bolivia)',snapshot.fecha_datos.astimezone(ZoneInfo('America/La_Paz')).strftime('%d/%m/%Y %H:%M:%S')])
    _append(summary,['Fuente','Base de datos de la empresa · DEMO'])
    _append(summary,['Ventana de ventas',f'{snapshot.ventana_desde} hasta antes de {snapshot.ventana_hasta} (Bolivia)'])
    _append(summary,['Interpretación',snapshot.resumen_operativo])
    _append(summary,['Estado pronóstico',snapshot.interpretacion.estado])
    _append(summary,['Huella',snapshot.fingerprint])
    _append(summary,['Cobertura','Todas las sucursales y categorías; una fila por sucursal y variante'])
    _append(summary,['Regla','Objetivo = mayor de techo(ventas 4 semanas × 1,25) y mínimo; reposición local = déficit frente a disponible.'])
    _append(summary,['Límite','Sugerencias históricas, no pronóstico ML por producto ni compras u ofertas automáticas. Revisar excedentes propios antes de comprar.'])
    for field,label in [('total','Casos completos'),('reponer','Reponer'),('evaluar_oferta','Evaluar oferta'),
        ('mantener','Mantener'),('sin_historial','Sin historial'),('revisar_datos','Revisar datos'),
        ('variantes_unicas','Variantes únicas'),('productos_unicos','Productos únicos')]:
        _append(summary,[label,getattr(snapshot.totales,field)])
    _append(summary,['Ventas excluidas',snapshot.ventas_excluidas])
    _append(summary,[])
    _append(summary,['SKU (cobertura empresarial)','Disponible','Objetivo','Compra empresarial sugerida','Datos completos'])
    for v in snapshot.cobertura_empresa:
        _append(summary,[v.sku,v.disponible,v.objetivo,v.compra_sugerida,'Sí' if v.datos_completos else 'No'])
    detail = wb.create_sheet('Detalle completo')
    quality = wb.create_sheet('Calidad')
    _append(detail,HEADERS)
    _append(quality,HEADERS)
    for c in sorted(snapshot.casos,key=lambda c:(c.sucursal_nombre or '',c.tipo,c.sku,str(c.variante_id))):
        row=[c.sucursal_nombre or 'Sin asignación',c.producto,c.sku,c.talla,c.color,c.vendidas4,
             c.vendidas4_anteriores,c.stock_actual,c.reservado,c.disponible,c.minimo_operativo,
             c.stock_objetivo,c.reponer,c.exceso,KIND_LABELS[c.tipo],c.texto,
             c.observado_desde.isoformat() if c.observado_desde else None,c.observados_dias,
             'Sí' if c.alerta_minimo else 'No','Sí' if c.revisar_otras_sucursales else 'No',
             str(c.sucursal_id) if c.sucursal_id else None,str(c.variante_id)]
        _append(detail,row)
        if c.reponer is None:
            _append(quality,row)
    for sheet in wb:
        sheet.freeze_panes='A2'
        if sheet is not summary:
            sheet.auto_filter.ref=sheet.dimensions
        for cell in sheet[1]:
            cell.fill=PatternFill('solid',fgColor='243247')
            cell.font=Font(color='FFFFFF',bold=True)
        for index in range(1,sheet.max_column+1):
            width=32 if index in (1,2) else 16
            if sheet is summary:
                width=34 if index==1 else 76 if index==2 else 24
            elif index==16:
                width=85
            sheet.column_dimensions[get_column_letter(index)].width=width
    output=BytesIO()
    wb.save(output)
    return output.getvalue()
