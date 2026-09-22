from app.database import SessionLocal
from app.models.sale import Sale, PaymentMethod, SaleType
from app.models.sale_detail import SaleDetail
from app.models.reservation import Reservation
from app.models.user import User

def fix_sales():
    db = SessionLocal()
    try:
        cliente_user = db.query(User).filter(User.email == "cliente@ficttstore.com").first()
        if not cliente_user:
            print("No se encontró cliente@ficttstore.com")
            return

        updated_count = 0

        # 1. Asociar ventas que provienen de reservas
        sales = db.query(Sale).all()
        for sale in sales:
            # Si no tiene cliente asignado:
            if not sale.cliente_id:
                # Buscar en detalles si hay reserva_id
                target_client_id = None
                for d in sale.detalles:
                    if d.reserva_id:
                        res = db.query(Reservation).filter(Reservation.id == d.reserva_id).first()
                        if res and res.cliente_id:
                            target_client_id = res.cliente_id
                            break
                
                # Si no está en detalle, buscar en la nota (ej. 'Liquidación de reserva RES-...')
                if not target_client_id and sale.nota and "reserva RES-" in sale.nota:
                    import re
                    match = re.search(r"RES-[\w-]+", sale.nota)
                    if match:
                        cod = match.group(0)
                        res = db.query(Reservation).filter(Reservation.codigo == cod).first()
                        if res and res.cliente_id:
                            target_client_id = res.cliente_id

                # Si es venta por PayPal Sandbox sin cliente, asignarla al cliente de prueba
                if not target_client_id and sale.metodo_pago == PaymentMethod.PAYPAL:
                    target_client_id = cliente_user.id
                    sale.tipo = SaleType.DIGITAL

                if target_client_id:
                    sale.cliente_id = target_client_id
                    updated_count += 1
                    print(f"  [FIX] Venta {sale.numero_recibo} asociada al cliente {target_client_id}")

            # Asegurarse de que monto_total, impuesto_iva y monto_neto estén calculados
            if sale.monto_total and (not sale.impuesto_iva or not sale.monto_neto):
                sale.impuesto_iva = round(float(sale.monto_total) * 0.13, 2)
                sale.monto_neto = round(float(sale.monto_total) * 0.87, 2)

        db.commit()
        print(f"\n[SUCCESS] Se corrigieron y vincularon {updated_count} ventas.")
    finally:
        db.close()

if __name__ == "__main__":
    fix_sales()
