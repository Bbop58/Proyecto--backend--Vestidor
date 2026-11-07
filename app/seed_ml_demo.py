"""Supplemental, explicitly fictional2026 receipts; never changes trained metrics."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from sqlalchemy import or_
from app.core.security import get_password_hash
from app.models import User,Role,Inventory,Sale,SaleDetail,SaleType,SaleStatus,PaymentMethod,InventoryMovement,MovementType


def seed_ml_demo(db,tenant,branches,variants,cashier):
    if (tenant.es_operadora or len(branches)!=2 or not variants or not cashier
        or cashier.tenant_id!=tenant.id or any(b is None or b.tenant_id!=tenant.id for b in branches)
        or any(v.producto.tenant_id!=tenant.id for v in variants)):
        raise ValueError('ML demo requires one explicit commercial company and its own references')
    role=(db.query(Role).filter(Role.name=='cliente',or_(Role.tenant_id==tenant.id,Role.tenant_id.is_(None)))
          .order_by(Role.tenant_id.is_(None)).first())
    if not role:raise ValueError('ML demo customer role missing')
    variants=sorted(variants,key=lambda v:v.sku)
    inventory={(i.sucursal_id,i.variante_id):i for i in db.query(Inventory).filter_by(tenant_id=tenant.id).all()}
    password_hash=None
    # Fixed historical dates keep reruns stable and never add a moving year's
    # worth of receipts. Diverse frequency/recency, not a target silhouette/R².
    anchor=datetime(2026,10,31,16,tzinfo=timezone.utc)
    ages=(7,21,45,100,10,30,60,150)
    for index,age in enumerate(ages):
        email=f'demo.ml.moda.{index+1:02d}@example.com'
        buyer=db.query(User).filter_by(email=email).first()
        if buyer and buyer.tenant_id!=tenant.id:
            raise ValueError('ML demo customer belongs to another company; no reassignment')
        if buyer is None:
            if password_hash is None:password_hash=get_password_hash('Password123!')
            buyer=User(tenant_id=tenant.id,role_id=role.id,email=email,full_name=f'Cliente demo Moda {index+1:02d}',
                hashed_password=password_hash,is_active=True,is_verified=True,
                created_at=anchor-timedelta(days=age+60))
            db.add(buyer);db.flush()
        for purchase in range(1+index%4):
            receipt=f'VTA-ML-MODA-{index+1:02d}-{purchase+1:02d}'
            existing=db.query(Sale).filter_by(numero_recibo=receipt).first()
            if existing:
                if existing.tenant_id!=tenant.id:raise ValueError('ML demo receipt belongs to another company')
                continue  # Preserve dates, ownership, cancellation and manual edits.
            branch=branches[(index+purchase)%2];variant=variants[(index+purchase)%len(variants)]
            inv=inventory.get((branch.id,variant.id))
            if inv is None or inv.stock_actual-inv.stock_reservado<1:
                raise ValueError('ML demo has no available stock; seed transaction must roll back')
            when=anchor-timedelta(days=age+purchase*14)
            amount=(Decimal(str(variant.producto.precio_base))+Decimal(str(variant.precio_extra))).quantize(Decimal('0.01'))
            if amount<=0:raise ValueError('ML demo historical price must be positive')
            tax=(amount*Decimal('0.13')).quantize(Decimal('0.01'))
            sale=Sale(tenant_id=tenant.id,numero_recibo=receipt,tipo=SaleType.PRESENCIAL,
                estado=SaleStatus.COMPLETADA,sucursal_id=branch.id,cliente_id=buyer.id,cajero_id=cashier.id,
                metodo_pago=PaymentMethod.EFECTIVO,monto_subtotal=amount,descuento=0,costo_envio=0,
                monto_total=amount,impuesto_iva=tax,monto_neto=amount-tax,monto_recibido=amount,cambio=0,
                nota='DEMO ML: compra historica ficticia; no es un cobro real.',created_at=when,updated_at=when)
            db.add(sale);db.flush()
            db.add(SaleDetail(venta_id=sale.id,variante_id=variant.id,cantidad=1,precio_unitario=amount,
                              subtotal=amount,created_at=when,updated_at=when))
            previous=inv.stock_actual;inv.stock_actual-=1
            db.add(InventoryMovement(tenant_id=tenant.id,inventario_id=inv.id,tipo=MovementType.SALIDA_VENTA,
                cantidad=1,stock_antes=previous,stock_despues=inv.stock_actual,referencia=receipt,
                nota='DEMO ML: salida por recibo historico ficticio',usuario_id=cashier.id,created_at=when))
            db.flush()
