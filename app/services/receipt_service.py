import uuid
from datetime import datetime
from typing import Dict, Any
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.sale import Sale
from app.models.product_exchange import ProductExchange
from app.models.tenant import Tenant


def numero_a_letras(monto: float) -> str:
    """Convierte un importe numérico a su representación literal en Bolivianos."""
    unidades = [
        "", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE",
        "DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE", "DIECISEIS", "DIECISIETE",
        "DIECIOCHO", "DIECINUEVE", "VEINTE", "VEINTIUNO", "VEINTIDOS", "VEINTITRES",
        "VEINTICUATRO", "VEINTICINCO", "VEINTISEIS", "VEINTISIETE", "VEINTIOCHO", "VEINTINUEVE"
    ]
    decenas = ["", "DIEZ", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"]
    centenas = [
        "", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS",
        "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"
    ]

    entero = int(monto)
    centavos = int(round((monto - entero) * 100))
    if entero == 0:
        texto = "CERO"
    elif entero == 100:
        texto = "CIEN"
    else:
        partes = []
        miles = entero // 1000
        resto = entero % 1000
        if miles == 1:
            partes.append("MIL")
        elif miles > 1:
            partes.append(f"{miles} MIL")

        if resto == 100:
            partes.append("CIEN")
        else:
            c = resto // 100
            d = (resto % 100) // 10
            u = resto % 10
            if c > 0:
                partes.append(centenas[c])
            du = resto % 100
            if du < 30:
                if du > 0:
                    partes.append(unidades[du])
            else:
                if u == 0:
                    partes.append(decenas[d])
                else:
                    partes.append(f"{decenas[d]} Y {unidades[u]}")
        texto = " ".join(partes)
    return f"{texto} {centavos:02d}/100 BOLIVIANOS"


class ReceiptService:
    @staticmethod
    def get_sale_receipt(db: Session, venta_id: uuid.UUID) -> Dict[str, Any]:
        venta = db.query(Sale).filter(Sale.id == venta_id).first()
        if not venta:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venta no encontrada.")

        tenant = db.query(Tenant).filter(Tenant.id == venta.tenant_id).first() if venta.tenant_id else None
        sucursal = venta.sucursal

        items = []
        for det in venta.detalles:
            var = det.variante
            prod = var.producto if var else None
            items.append({
                "sku": var.sku if var else "N/A",
                "producto": prod.nombre if prod else "Prenda sin catálogo",
                "talla": var.talla if var else "-",
                "color": var.color if var else "-",
                "cantidad": det.cantidad,
                "precio_unitario": float(det.precio_unitario),
                "subtotal": float(det.subtotal)
            })

        total_num = float(venta.monto_total)
        iva_num = float(getattr(venta, "impuesto_iva", 0) or round(total_num * 0.13, 2))
        neto_num = float(getattr(venta, "monto_neto", 0) or round(total_num - iva_num, 2))
        recibido_num = float(venta.monto_recibido) if venta.monto_recibido is not None else total_num
        cambio_num = float(venta.cambio) if venta.cambio is not None else 0.0

        return {
            "tipo_documento": "NOTA DE VENTA OFICIAL",
            "numero_recibo": venta.numero_recibo,
            "tipo_venta": venta.tipo.value if hasattr(venta.tipo, "value") else str(venta.tipo),
            "estado": venta.estado.value if hasattr(venta.estado, "value") else str(venta.estado),
            "fecha_emision": venta.created_at.isoformat() if venta.created_at else datetime.utcnow().isoformat(),
            "empresa": {
                "nombre": tenant.nombre if tenant else "FashionStore Retail",
                "razon_social": tenant.razon_social if tenant else "FashionStore SRL",
                "nit": tenant.nit_rut if tenant else "1028374029",
                "slug": tenant.slug if tenant else "urbanstyle",
                "logo_url": tenant.logo_url if tenant else None,
                "telefono": tenant.telefono if tenant else (sucursal.telefono if sucursal else "+591 70000000"),
                "email": tenant.email_contacto if tenant else "contacto@fashionstore.bo",
            },
            "sucursal": {
                "id": str(sucursal.id) if sucursal else None,
                "nombre": sucursal.nombre if sucursal else "Sucursal Central",
                "ciudad": sucursal.ciudad if sucursal else "Santa Cruz",
                "direccion": sucursal.direccion if sucursal else "Av. Principal #100",
                "telefono": sucursal.telefono if sucursal else "+591 3 3456789"
            },
            "cajero": {
                "id": str(venta.cajero.id) if venta.cajero else None,
                "nombre": venta.cajero.full_name if venta.cajero else "Venta Digital / En Línea",
                "email": venta.cajero.email if venta.cajero else "digital_checkout"
            },
            "cliente": {
                "id": str(venta.cliente.id) if venta.cliente else None,
                "nombre": venta.cliente.full_name if venta.cliente else "Cliente Casual / Mostrador",
                "email": venta.cliente.email if venta.cliente else "N/A"
            },
            "pago": {
                "metodo": venta.metodo_pago.value if hasattr(venta.metodo_pago, "value") else str(venta.metodo_pago),
                "referencia": venta.referencia_pago or "PAGO_DIRECTO_CAJA",
                "monto_recibido": recibido_num,
                "cambio": cambio_num
            },
            "items": items,
            "totales": {
                "total_articulos": sum(i["cantidad"] for i in items),
                "monto_total": total_num,
                "impuesto_iva": iva_num,
                "monto_neto": neto_num,
                "total_literal": numero_a_letras(total_num)
            },
            "nota": venta.nota,
            "politica_garantia": (
                "GARANTÍA DE COMPRA (30 DÍAS):\n"
                "1. Se aceptan cambios de prendas dentro de los 30 días posteriores a la emisión de esta nota.\n"
                "2. La prenda debe encontrarse sin uso, limpia y con sus etiquetas originales intactas.\n"
                "3. Si la nueva prenda elegida tiene un costo menor, la diferencia se acredita a su Billetera Virtual.\n"
                "4. Conserve este comprobante físico o digital para cualquier trámite o cambio en sucursal."
            ),
            "codigo_seguridad_qr": f"RECIBO:{venta.numero_recibo}|NIT:{tenant.nit_rut if tenant else '1028374029'}|TOTAL:{total_num:.2f}|FECHA:{venta.created_at.strftime('%Y%m%d') if venta.created_at else ''}"
        }

    @staticmethod
    def get_exchange_receipt(db: Session, cambio_id: uuid.UUID) -> Dict[str, Any]:
        cambio = db.query(ProductExchange).filter(ProductExchange.id == cambio_id).first()
        if not cambio:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nota de cambio no encontrada.")

        venta = cambio.venta
        tenant = db.query(Tenant).filter(Tenant.id == venta.tenant_id).first() if venta and venta.tenant_id else None
        sucursal = cambio.sucursal or (venta.sucursal if venta else None)

        prendas_devueltas = []
        prendas_entregadas = []

        for det in cambio.detalles:
            v_dev = det.variante_devuelta
            p_dev = v_dev.producto if v_dev else None
            sub_dev = float(det.precio_unitario_devuelto) * det.cantidad_devuelta
            prendas_devueltas.append({
                "sku": v_dev.sku if v_dev else "N/A",
                "producto": p_dev.nombre if p_dev else "Prenda Devuelta",
                "talla": v_dev.talla if v_dev else "-",
                "color": v_dev.color if v_dev else "-",
                "cantidad": det.cantidad_devuelta,
                "precio_unitario": float(det.precio_unitario_devuelto),
                "subtotal": sub_dev
            })

            v_ent = det.variante_entregada
            p_ent = v_ent.producto if v_ent else None
            sub_ent = float(det.precio_unitario_entregado) * det.cantidad_entregada
            prendas_entregadas.append({
                "sku": v_ent.sku if v_ent else "N/A",
                "producto": p_ent.nombre if p_ent else "Prenda Entregada",
                "talla": v_ent.talla if v_ent else "-",
                "color": v_ent.color if v_ent else "-",
                "cantidad": det.cantidad_entregada,
                "precio_unitario": float(det.precio_unitario_entregado),
                "subtotal": sub_ent
            })

        dif = float(cambio.diferencia)
        if dif > 0:
            tipo_diferencia = "COBRO_ADICIONAL"
            mensaje_balance = f"El cliente abonó una diferencia de Bs. {dif:.2f} vía {cambio.metodo_pago_diferencia}."
        elif dif < 0:
            tipo_diferencia = "SALDO_A_FAVOR_CLIENTE"
            saldo_favor = abs(dif)
            mensaje_balance = f"Saldo a favor del cliente de Bs. {saldo_favor:.2f} acreditado en Billetera Virtual."
        else:
            tipo_diferencia = "CAMBIO_EXACTO"
            mensaje_balance = "Cambio de prenda exacto. Saldo 0.00 Bs."

        return {
            "tipo_documento": "NOTA DE CAMBIO Y DEVOLUCIÓN",
            "numero_cambio": cambio.numero_cambio,
            "numero_recibo_original": venta.numero_recibo if venta else "N/A",
            "fecha_emision": cambio.fecha_cambio.isoformat() if cambio.fecha_cambio else datetime.utcnow().isoformat(),
            "dias_transcurridos": cambio.dias_transcurridos,
            "motivo": cambio.motivo,
            "estado_prenda": cambio.estado_prenda,
            "empresa": {
                "nombre": tenant.nombre if tenant else "FashionStore Retail",
                "razon_social": tenant.razon_social if tenant else "FashionStore SRL",
                "nit": tenant.nit_rut if tenant else "1028374029",
                "slug": tenant.slug if tenant else "urbanstyle",
                "logo_url": tenant.logo_url if tenant else None,
            },
            "sucursal": {
                "nombre": sucursal.nombre if sucursal else "Sucursal Central",
                "ciudad": sucursal.ciudad if sucursal else "Santa Cruz",
                "direccion": sucursal.direccion if sucursal else "Av. Principal #100",
                "telefono": sucursal.telefono if sucursal else "+591 3 3456789"
            },
            "cajero": {
                "nombre": cambio.cajero.full_name if cambio.cajero else "Encargado de Caja",
                "email": cambio.cajero.email if cambio.cajero else "cajero"
            },
            "cliente": {
                "nombre": (cambio.cliente.full_name if cambio.cliente else (venta.cliente.full_name if venta and venta.cliente else "Cliente")),
                "email": (cambio.cliente.email if cambio.cliente else (venta.cliente.email if venta and venta.cliente else "N/A"))
            },
            "prendas_devueltas": prendas_devueltas,
            "prendas_entregadas": prendas_entregadas,
            "balance_liquidacion": {
                "monto_total_devuelto": float(cambio.monto_total_devuelto),
                "monto_total_entregado": float(cambio.monto_total_entregado),
                "diferencia": dif,
                "tipo_diferencia": tipo_diferencia,
                "metodo_pago_diferencia": cambio.metodo_pago_diferencia,
                "mensaje_balance": mensaje_balance,
                "total_literal": numero_a_letras(abs(dif)) if dif != 0 else "CERO 00/100 BOLIVIANOS"
            },
            "nota": cambio.nota,
            "politica_garantia": (
                "DOCUMENTO DE REGULARIZACIÓN DE INVENTARIO Y GARANTÍA:\n"
                "Este comprobante certifica el reingreso al Kardex de la mercadería devuelta y la entrega formal de la nueva prenda.\n"
                "Las prendas cambiadas mantienen su garantía original desde la fecha de emisión del recibo inicial."
            ),
            "codigo_seguridad_qr": f"CAMBIO:{cambio.numero_cambio}|RECIBO_ORIGINAL:{venta.numero_recibo if venta else 'N/A'}|DIF:{dif:.2f}|FECHA:{cambio.fecha_cambio.strftime('%Y%m%d') if cambio.fecha_cambio else ''}"
        }
