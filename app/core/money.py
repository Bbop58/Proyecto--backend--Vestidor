"""Server-owned monetary arithmetic and catalog quotation."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from app.models.product_variant import ProductVariant
from app.models.reservation import Reservation
from app.models.coupon import Coupon


def money(value, *, positive=False):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) > Decimal('99999999.99'):
            raise InvalidOperation
        result = result.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(400, 'Importe monetario inválido')
    if result < 0 or (positive and result <= 0):
        raise HTTPException(400, 'El importe debe ser positivo')
    return result


def catalog_price(variant, now=None):
    now = now or datetime.now(timezone.utc)
    product = variant.producto
    start, end = product.fecha_inicio_oferta, product.fecha_fin_oferta
    offered = (product.en_oferta and product.precio_oferta is not None
               and (start is None or start <= now) and (end is None or now <= end))
    base = product.precio_oferta if offered else product.precio_base
    return money(Decimal(str(base)) + Decimal(str(variant.precio_extra)))


def catalog_product_price(product, now=None):
    """Informational starting price; checkout still quotes the selected variant."""
    from types import SimpleNamespace
    variants = [variant for variant in product.variantes if variant.activo]
    return min(catalog_price(variant, now) for variant in variants) if variants else catalog_price(
        SimpleNamespace(producto=product, precio_extra=0), now
    )


def quote(db, items, tenant_id, coupon_code=None, *, consume=False):
    subtotal = Decimal('0.00')
    rows = []
    for item in items:
        variant = db.query(ProductVariant).filter_by(id=item.variante_id, activo=True).first()
        if not variant or not variant.producto.activo or variant.producto.tenant_id != tenant_id:
            raise HTTPException(404, 'Prenda no disponible')
        price = catalog_price(variant)
        if getattr(item, 'reserva_id', None):
            reservation = db.query(Reservation).filter_by(id=item.reserva_id, tenant_id=tenant_id).first()
            details = [d for d in reservation.detalles if d.variante_id == item.variante_id] if reservation else []
            if not details or len({d.precio_unitario for d in details}) != 1:
                raise HTTPException(409, 'Precio de reserva no disponible')
            price = money(details[0].precio_unitario)
        supplied = getattr(item, 'precio_unitario', None)
        if supplied is not None and money(supplied) != price:
            raise HTTPException(409, 'El precio cambió o no coincide con la cotización del servidor; actualiza el carrito')
        subtotal += price * item.cantidad
        rows.append(dict(variante_id=str(item.variante_id), cantidad=item.cantidad,
                         precio_unitario=str(price), reserva_id=str(item.reserva_id) if getattr(item, 'reserva_id', None) else None))
    discount = Decimal('0.00')
    if coupon_code:
        coupon = (db.query(Coupon).filter_by(tenant_id=tenant_id, codigo=coupon_code.strip().upper())
                  .populate_existing().with_for_update(of=Coupon).first())
        if not coupon:
            raise HTTPException(400, 'Cupón no disponible')
        valid, reason = coupon.is_valid(subtotal)
        if not valid:
            raise HTTPException(400, reason)
        if not 0 <= coupon.porcentaje_descuento <= 100:
            raise HTTPException(409, 'Cupón con configuración inválida')
        discount = money(subtotal * coupon.porcentaje_descuento / Decimal(100))
        if consume:
            coupon.usos_actuales += 1
    return dict(subtotal=subtotal, descuento=discount, total=subtotal-discount, items=rows,
                codigo_cupon=coupon_code.strip().upper() if coupon_code else None)
