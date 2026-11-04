"""Units come from selected SQL expressions, never a model's chosen alias."""
import sqlglot
from sqlglot import exp


def metric_units(sql):
    tree = sqlglot.parse_one(sql, read='postgres')
    units = {}
    for selected in tree.expressions:
        name = selected.alias_or_name.lower()
        value = selected.this if isinstance(selected, exp.Alias) else selected
        if isinstance(value, exp.Filter):
            value = value.this
        fields = {column.name.lower() for column in value.find_all(exp.Column)}
        words = {word for field in fields for word in field.split('_')}
        if isinstance(value, exp.Count):
            units[name] = 'registros'
        elif words & {'monto', 'precio', 'subtotal', 'saldo', 'costo', 'descuento',
                      'impuesto', 'iva', 'diferencia', 'importe', 'ingresos', 'recaudado'} or 'total_estimado' in fields:
            units[name] = 'Bolivianos'
        elif words & {'cantidad', 'stock', 'unidades', 'existencia'}:
            units[name] = 'unidades'
    return units
