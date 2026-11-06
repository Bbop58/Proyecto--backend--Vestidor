"""Commercial invariants; PostgreSQL only through the isolated test fixtures."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from tests.test_tenant_sales import shops
from tests.support_payments import confirmed_intent
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement, MovementType
from app.models.product_variant import ProductVariant
from app.models.reservation import Reservation, ReservationStatus
from app.models.sale import Sale
from app.models.sale_detail import SaleDetail
from app.models.product_exchange import ProductExchange
from app.schemas.sale import PresencialSaleCreate, DigitalSaleCreate
from app.schemas.reservation import ReservationCreate
from app.schemas.product_exchange import ProductExchangeCreate
from app.services.sale_service import SaleService
from app.services.reservation_service import ReservationService
from app.services.exchange_service import ExchangeService


def sale_data(branch, variant, count=1, **extra):
    return PresencialSaleCreate(sucursal_id=branch.id,
        items=[dict(variante_id=variant.id, cantidad=count, **extra)],
        pago=dict(metodo='EFECTIVO', monto_recibido=1000))


def reserve(db, user, variant, branch, items=None):
    return ReservationService.create_reservation(db, ReservationCreate(
        sucursal_id=branch.id, fecha_hora_esperada=datetime.now(timezone.utc)+timedelta(hours=2),
        items=items or [dict(variante_id=variant.id, cantidad=2)]), user.id)


def inv_for(db, branch, variant):
    return db.query(Inventory).filter_by(sucursal_id=branch.id, variante_id=variant.id).one()


def snapshot(db):
    db.flush()
    return (db.query(Sale).count(), db.query(SaleDetail).count(),
        db.query(Reservation).count(), db.query(InventoryMovement).count(),
        sorted((str(i.id), i.stock_actual, i.stock_reservado) for i in db.query(Inventory).all()))


def test_sale_movement_uses_positive_quantity(db_session, shops):
    u,v,bs=shops['a']
    SaleService.create_presencial_sale(db_session, sale_data(bs[0],v),u.id)
    mov=db_session.query(InventoryMovement).filter_by(tipo=MovementType.SALIDA_VENTA).one()
    assert mov.cantidad == mov.stock_antes-mov.stock_despues == 1


def test_stored_zero_tax_is_not_recomputed(db_session, shops):
    u,v,bs=shops['a']
    result=SaleService.create_presencial_sale(db_session,sale_data(bs[0],v),u.id)
    sale=db_session.get(Sale,result['id']); sale.impuesto_iva=0; sale.monto_neto=0
    db_session.commit()
    result=SaleService._format_sale_response(sale)
    assert result['impuesto_iva'] == result['monto_neto'] == 0


def test_exact_cash_with_fractional_cents_is_accepted(db_session,shops):
    u,v,bs=shops['a'];v.producto.precio_base=29.95;db_session.commit();data=sale_data(bs[0],v,precio_unitario=29.95)
    data.pago.monto_recibido=29.95
    result=SaleService.create_presencial_sale(db_session,data,u.id)
    assert result['monto_total']==29.95
    assert result['monto_recibido']==29.95
    assert result['cambio']==0


@pytest.mark.parametrize('failure',['stock','cash','flush'])
def test_failure_rolls_back_whole_service_operation(db_session,shops,failure):
    u,v,bs=shops['a']; data=sale_data(bs[0],v,11 if failure=='stock' else 1)
    if failure=='cash': data.pago.monto_recibido=0
    before=snapshot(db_session)
    def fail_flush(session, context, instances):
        if any(isinstance(obj,SaleDetail) for obj in session.new):
            raise RuntimeError('injected detail failure')
    if failure=='flush': event.listen(db_session,'before_flush',fail_flush)
    try:
        with pytest.raises((HTTPException,RuntimeError)):
            SaleService.create_presencial_sale(db_session,data,u.id)
    finally:
        if failure=='flush': event.remove(db_session,'before_flush',fail_flush)
    # Caller must not need its own rollback for a failed standalone service.
    db_session.commit()
    assert snapshot(db_session)==before


def test_partial_reservation_checkout_rejected_without_writes(db_session,shops):
    u,v,bs=shops['a']; r=reserve(db_session,u,v,bs[0]); before=snapshot(db_session)
    with pytest.raises(HTTPException):
        SaleService.create_presencial_sale(db_session,sale_data(bs[0],v,1,reserva_id=r['id']),u.id)
    assert snapshot(db_session)==before


def test_duplicate_lines_can_liquidate_one_reservation(db_session,shops):
    u,v,bs=shops['a']; r=reserve(db_session,u,v,bs[0])
    data=sale_data(bs[0],v,1,reserva_id=r['id']); data.items.append(data.items[0].model_copy())
    SaleService.create_presencial_sale(db_session,data,u.id)
    inv=inv_for(db_session,bs[0],v)
    assert (inv.stock_actual,inv.stock_reservado)==(8,0)
    assert db_session.get(Reservation,r['id']).estado==ReservationStatus.COMPLETADA


def test_unpaid_pickup_cannot_orphan_reserved_stock(db_session,shops):
    u,v,bs=shops['a']; r=reserve(db_session,u,v,bs[0])
    ReservationService.prepare_reservation(db_session,r['id'],u.id)
    before=snapshot(db_session)
    with pytest.raises(HTTPException):
        ReservationService.pickup_reservation(db_session,r['id'],u.id)
    assert snapshot(db_session)==before


def test_prepared_reservations_expire_and_release_only_once(db_session,shops):
    u,v,bs=shops['a']; r=reserve(db_session,u,v,bs[0])
    ReservationService.prepare_reservation(db_session,r['id'],u.id)
    row=db_session.get(Reservation,r['id']);row.fecha_expiracion=datetime.now(timezone.utc)-timedelta(seconds=1)
    db_session.commit()
    assert ReservationService.expire_pending_reservations(db_session)['total_expiradas']==1
    assert ReservationService.expire_pending_reservations(db_session)['total_expiradas']==0
    assert inv_for(db_session,bs[0],v).stock_reservado==0


def exchange_data(receipt, returned, delivered, items=1):
    return ProductExchangeCreate(numero_recibo=receipt,motivo='Cambio de talla',items=[
        dict(variante_devuelta_id=returned.id,cantidad_devuelta=1,
             variante_entregada_id=delivered.id,cantidad_entregada=1) for _ in range(items)])


def test_exchange_aggregates_repeated_outgoing_variant(db_session,shops):
    u,v,bs=shops['a']; result=SaleService.create_presencial_sale(db_session,sale_data(bs[0],v,2),u.id)
    other=ProductVariant(producto_id=v.producto_id,sku='replacement',talla='L',color='Azul',precio_extra=0,activo=True)
    db_session.add(other);db_session.flush()
    db_session.add(Inventory(tenant_id=u.tenant_id,sucursal_id=bs[0].id,variante_id=other.id,stock_actual=1,stock_reservado=0))
    db_session.commit();before=snapshot(db_session)
    with pytest.raises(HTTPException):
        ExchangeService.create_exchange(db_session,exchange_data(result['numero_recibo'],v,other,2),u.id)
    assert snapshot(db_session)==before
    assert db_session.query(ProductExchange).count()==0


def test_cannot_cancel_sale_after_exchange(db_session,shops):
    u,v,bs=shops['a']; result=SaleService.create_presencial_sale(db_session,sale_data(bs[0],v),u.id)
    ExchangeService.create_exchange(db_session,exchange_data(result['numero_recibo'],v,v),u.id)
    before=snapshot(db_session)
    with pytest.raises(HTTPException):
        SaleService.cancel_sale(db_session,result['id'],'Anular prueba',u.id)
    assert snapshot(db_session)==before


@pytest.mark.parametrize('digital',[False,True])
def test_repeated_variant_cannot_oversell(db_session,shops,digital):
    u,v,bs=shops['a']; before=snapshot(db_session)
    data=sale_data(bs[0],v,6);data.items.append(data.items[0].model_copy())
    with pytest.raises(HTTPException):
        if digital:
            SaleService.create_digital_sale(db_session,DigitalSaleCreate(
                sucursal_id=bs[0].id,items=data.items),u.id,
                _payment_intent=confirmed_intent(db_session,u.id,bs[0].id,120))
        else:
            SaleService.create_presencial_sale(db_session,data,u.id)
    assert snapshot(db_session)==before


def test_paid_reservation_pickup_and_repeated_cancel(db_session,shops):
    u,v,bs=shops['a']; r=reserve(db_session,u,v,bs[0])
    result=SaleService.create_presencial_sale(db_session,sale_data(bs[0],v,2,reserva_id=r['id']),u.id)
    ReservationService.pickup_reservation(db_session,r['id'],u.id)
    SaleService.cancel_sale(db_session,result['id'],'Anulación de prueba',u.id)
    assert db_session.get(Reservation,r['id']).estado==ReservationStatus.CANCELADA
    before=snapshot(db_session)
    with pytest.raises(HTTPException): SaleService.cancel_sale(db_session,result['id'],'Repetida',u.id)
    assert snapshot(db_session)==before
    assert (inv_for(db_session,bs[0],v).stock_actual,inv_for(db_session,bs[0],v).stock_reservado)==(10,0)


@pytest.mark.parametrize('state',['PENDIENTE','PREPARADA'])
def test_expired_reservation_cannot_be_sold(db_session,shops,state):
    u,v,bs=shops['a']; r=reserve(db_session,u,v,bs[0])
    row=db_session.get(Reservation,r['id']);row.estado=ReservationStatus(state)
    row.fecha_expiracion=datetime.now(timezone.utc)-timedelta(seconds=1);db_session.commit()
    before=snapshot(db_session)
    with pytest.raises(HTTPException):
        SaleService.create_presencial_sale(db_session,sale_data(bs[0],v,2,reserva_id=r['id']),u.id)
    assert snapshot(db_session)==before


def test_cancel_reservation_is_single_release(db_session,shops):
    u,v,bs=shops['a']; r=reserve(db_session,u,v,bs[0])
    ReservationService.cancel_reservation(db_session,r['id'],u.id)
    before=snapshot(db_session)
    with pytest.raises(HTTPException): ReservationService.cancel_reservation(db_session,r['id'],u.id)
    assert snapshot(db_session)==before
    assert inv_for(db_session,bs[0],v).stock_reservado==0


def test_reservation_cannot_charge_a_different_variant(db_session,shops):
    u,v,bs=shops['a']; r=reserve(db_session,u,v,bs[0]); before=snapshot(db_session)
    # A real but unrelated variant must not consume another reservation's hold.
    from app.models.reservation_detail import ReservationDetail
    detail=db_session.query(ReservationDetail).filter_by(reserva_id=r['id']).one()
    other=ProductVariant(producto_id=v.producto_id,sku='unrelated',talla='L',color='Azul',precio_extra=0,activo=True)
    db_session.add(other);db_session.flush()
    db_session.add(Inventory(tenant_id=u.tenant_id,sucursal_id=bs[0].id,variante_id=other.id,stock_actual=5,stock_reservado=0))
    db_session.commit();before=snapshot(db_session)
    with pytest.raises(HTTPException):
        SaleService.create_presencial_sale(db_session,sale_data(bs[0],other,2,reserva_id=r['id']),u.id)
    assert snapshot(db_session)==before


def test_multiple_variant_reservation_liquidates_all_details(db_session,shops):
    u,v,bs=shops['a']
    other=ProductVariant(producto_id=v.producto_id,sku='second',talla='L',color='Azul',precio_extra=0,activo=True)
    db_session.add(other);db_session.flush()
    db_session.add(Inventory(tenant_id=u.tenant_id,sucursal_id=bs[0].id,variante_id=other.id,stock_actual=5,stock_reservado=0))
    db_session.commit()
    items=[dict(variante_id=v.id,cantidad=2),dict(variante_id=other.id,cantidad=1)]
    r=reserve(db_session,u,v,bs[0],items)
    data=PresencialSaleCreate(sucursal_id=bs[0].id,items=[dict(**i,reserva_id=r['id']) for i in items],
                             pago=dict(metodo='EFECTIVO',monto_recibido=100))
    result=SaleService.create_presencial_sale(db_session,data,u.id)
    assert len(result['detalles'])==2
    assert (inv_for(db_session,bs[0],v).stock_actual, inv_for(db_session,bs[0],v).stock_reservado)==(8,0)
    assert (inv_for(db_session,bs[0],other).stock_actual, inv_for(db_session,bs[0],other).stock_reservado)==(4,0)


def test_reception_failure_and_adjustment_preserve_reserved_stock(db_session,shops):
    from app.services.inventory_service import InventoryService
    from app.schemas.inventory import ReceptionCreate,StockAdjustment
    u,v,bs=shops['a'];reserve(db_session,u,v,bs[0]);before=snapshot(db_session)
    with pytest.raises(HTTPException):
        InventoryService.receive_products(db_session,ReceptionCreate(sucursal_id=bs[0].id,productos=[
            dict(variante_id=v.id,cantidad=3),dict(variante_id=uuid.uuid4(),cantidad=1)]),u.id)
    assert snapshot(db_session)==before
    with pytest.raises(HTTPException):
        InventoryService.adjust_stock(db_session,StockAdjustment(inventario_id=inv_for(db_session,bs[0],v).id,cantidad=-9),u.id)
    assert snapshot(db_session)==before


@pytest.mark.parametrize('count',[0,-1])
def test_api_schema_rejects_nonpositive_quantity(count):
    from pydantic import ValidationError
    from app.schemas.sale import SaleItemCreate
    from app.schemas.reservation import ReservationItemCreate
    for schema in (SaleItemCreate, ReservationItemCreate):
        with pytest.raises(ValidationError): schema(variante_id=uuid.uuid4(),cantidad=count)


def test_reservation_duplicate_lines_do_not_bypass_variant_limit(db_session,shops):
    u,v,bs=shops['a'];before=snapshot(db_session)
    with pytest.raises(HTTPException):
        reserve(db_session,u,v,bs[0],[dict(variante_id=v.id,cantidad=2)]*2)
    assert snapshot(db_session)==before


@pytest.mark.parametrize('channel',['sync','wallet'])
@pytest.mark.parametrize('reason',['reserved','repeated'])
def test_offline_stock_writers_respect_holds_and_aggregate_items(db_session,shops,channel,reason):
    u,v,bs=shops['a']
    if reason=='reserved':
        reserve(db_session,u,v,bs[0])
        items=[dict(variante_id=v.id,cantidad=9,precio_unitario=10)]
    else:
        items=[dict(variante_id=v.id,cantidad=6,precio_unitario=10)]*2
    if channel=='wallet':
        from app.services.wallet_service import WalletService
        from app.schemas.wallet import OfflineOrderPayload
        WalletService.topup_cash_admin(db_session,u,u.id,1000)
    before=snapshot(db_session)
    if channel=='sync':
        from app.services.sync_service import SyncService
        from app.schemas.sync import SyncBatchRequest
        result=SyncService.process_sync_batch(db_session,SyncBatchRequest(operations=[dict(
            client_operation_id=uuid.uuid4(),sucursal_id=bs[0].id,fecha_local=datetime.now(timezone.utc),
            items=items,monto_total=120)]),u,u.tenant_id)
        assert result.total_rejected==1
    else:
        result=WalletService.sync_offline_orders_batch(db_session,u.id,u.tenant_id,[OfflineOrderPayload(
            client_operation_id=uuid.uuid4(),sucursal_id=bs[0].id,items=items,monto_total=120)])
        assert result['fallidas']==1
    assert snapshot(db_session)==before


def test_sync_repeated_valid_variant_has_sequential_ledger(db_session,shops):
    from app.services.sync_service import SyncService
    from app.schemas.sync import SyncBatchRequest
    u,v,bs=shops['a']
    result=SyncService.process_sync_batch(db_session,SyncBatchRequest(operations=[dict(
        client_operation_id=uuid.uuid4(),sucursal_id=bs[0].id,fecha_local=datetime.now(timezone.utc),
        items=[dict(variante_id=v.id,cantidad=1,precio_unitario=10)]*2,monto_total=20)]),u,u.tenant_id)
    assert result.total_confirmed==1
    moves=db_session.query(InventoryMovement).filter_by(tipo=MovementType.SALIDA_VENTA).all()
    assert sorted((m.stock_antes,m.stock_despues,m.cantidad) for m in moves)==[(9,8,1),(10,9,1)]


@pytest.mark.parametrize('operation',['reserve','exchange'])
def test_detail_failure_rolls_back_reservation_or_exchange(db_session,shops,operation):
    from app.models.reservation_detail import ReservationDetail
    from app.models.product_exchange_detail import ProductExchangeDetail
    u,v,bs=shops['a']
    if operation=='exchange':
        result=SaleService.create_presencial_sale(db_session,sale_data(bs[0],v),u.id)
    before=snapshot(db_session)
    target=ReservationDetail if operation=='reserve' else ProductExchangeDetail
    def fail(session,context,instances):
        if any(isinstance(obj,target) for obj in session.new): raise RuntimeError('injected detail failure')
    event.listen(db_session,'before_flush',fail)
    try:
        with pytest.raises(RuntimeError):
            if operation=='reserve': reserve(db_session,u,v,bs[0])
            else: ExchangeService.create_exchange(db_session,exchange_data(result['numero_recibo'],v,v),u.id)
    finally: event.remove(db_session,'before_flush',fail)
    db_session.commit()
    assert snapshot(db_session)==before
    assert db_session.query(ProductExchange).count()==0


@pytest.mark.parametrize('actual,reserved,minimum',[(-1,0,0),(1,2,0),(1,-1,0),(1,0,-1)])
def test_database_rejects_invalid_stock(db_session,shops,actual,reserved,minimum):
    from sqlalchemy.exc import IntegrityError
    u,v,bs=shops['a']; inv=inv_for(db_session,bs[0],v)
    inv.stock_actual=actual;inv.stock_reservado=reserved;inv.stock_minimo=minimum
    with pytest.raises(IntegrityError): db_session.commit()
    db_session.rollback()


@pytest.fixture
def committed_shop():
    """Committed independent connections for concurrency, unlike savepoint fixtures."""
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    from tests.conftest import test_engine
    from app.database import Base
    from app.models.tenant import Tenant
    from app.models.user import User
    from app.models.branch import Branch
    from app.models.category import Category
    from app.models.product import Product
    from app.models.role import Role
    schema='commercial_concurrency_'+uuid.uuid4().hex
    with test_engine.begin() as connection:connection.execute(text('CREATE SCHEMA '+schema))
    engine=test_engine.execution_options(schema_translate_map={None:schema})
    TestingSessionLocal=sessionmaker(bind=engine)
    try:
        Base.metadata.create_all(engine)
        with TestingSessionLocal() as db:
            key=uuid.uuid4().hex
            tenant=Tenant(nombre=key,razon_social=key,nit_rut=key,slug=key,activo=True)
            db.add(tenant);db.flush()
            role=Role(name=key,tenant_id=tenant.id)
            db.add(role);db.flush()
            from app.models.permission import Permission
            buy=Permission(code='reservas.create',module='Reservas',method='POST',path='/reservas')
            db.add(buy);role.permissions.append(buy)
            user=User(email=f'{key}@example.com',full_name='Concurrent',hashed_password='unused',tenant_id=tenant.id,role_id=role.id,is_active=True)
            category=Category(nombre=key,tenant_id=tenant.id,activa=True)
            branch=Branch(nombre=key,tenant_id=tenant.id,ciudad='SC',direccion='Test',activa=True)
            db.add_all([user,category,branch]);db.flush()
            product=Product(nombre=key,categoria_id=category.id,tenant_id=tenant.id,precio_base=10,activo=True)
            db.add(product);db.flush()
            variant=ProductVariant(producto_id=product.id,sku=key,talla='M',color='Azul',precio_extra=0,activo=True)
            db.add(variant);db.flush()
            inventory=Inventory(tenant_id=tenant.id,sucursal_id=branch.id,variante_id=variant.id,stock_actual=1,stock_reservado=0,activo=True)
            db.add(inventory)
            from app.models import SubscriptionPlan,Subscription
            from app.core.billing_calendar import business_today
            from app.core.plan_catalog_ddl import ensure_legacy_versions
            from app.core.subscription_contract_ddl import ensure_legacy_terms
            plan=SubscriptionPlan(codigo='CONCURRENT',nombre='Explicit concurrent test contract',max_sucursales=20,
                precio_mensual_bob=0,permite_offline=True,permite_reportes_ia=True)
            db.add(plan);db.flush()
            db.add(Subscription(tenant_id=tenant.id,plan_id=plan.id,estado='ACTIVA',
                fecha_inicio=business_today(),fecha_vencimiento=business_today()+timedelta(days=30)))
            db.flush();ensure_legacy_versions(db.connection());ensure_legacy_terms(db.connection());db.commit()
            ids=(user.id,variant.id,branch.id,inventory.id)
        yield ids,TestingSessionLocal
    finally:
        # Only this fixture's isolated test schema is removed, including immutable history.
        with test_engine.begin() as connection:
            connection.execute(text('DROP SCHEMA '+schema+' CASCADE'))


@pytest.mark.parametrize('operations',[('sale','sale'),('sale','digital'),('reserve','sale'),('reserve','reserve'),('adjust','sale'),('receive','receive')])
def test_last_unit_is_allocated_once_across_real_transactions(committed_shop,operations):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import text
    from app.models.branch import Branch
    user_id,variant_id,branch_id,inventory_id=committed_shop[0];factory=committed_shop[1]
    barrier=Barrier(2)
    def perform(operation):
        with factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            # Both sessions start with the same cached stock; refresh after locking is required.
            inv=db.get(Inventory,inventory_id)
            assert inv.stock_actual==1
            branch=db.get(Branch,branch_id);variant=db.get(ProductVariant,variant_id)
            barrier.wait(timeout=10)
            try:
                if operation in ('adjust','receive'):
                    from app.services.inventory_service import InventoryService
                    from app.schemas.inventory import ReceptionCreate,StockAdjustment
                    if operation=='adjust':
                        InventoryService.adjust_stock(db,StockAdjustment(inventario_id=inventory_id,cantidad=-1),user_id)
                    else:
                        InventoryService.receive_products(db,ReceptionCreate(sucursal_id=branch_id,
                            productos=[dict(variante_id=variant_id,cantidad=1)]),user_id)
                elif operation=='reserve':
                    ReservationService.create_reservation(db,ReservationCreate(sucursal_id=branch_id,
                        fecha_hora_esperada=datetime.now(timezone.utc)+timedelta(hours=1),
                        items=[dict(variante_id=variant_id,cantidad=1)]),user_id)
                elif operation=='digital':
                    SaleService.create_digital_sale(db,DigitalSaleCreate(sucursal_id=branch_id,
                        items=[dict(variante_id=variant_id,cantidad=1)]),user_id,
                        _payment_intent=confirmed_intent(db,user_id,branch_id,10))
                else:
                    SaleService.create_presencial_sale(db,sale_data(branch,variant),user_id)
                return 'success'
            except HTTPException as error:
                assert error.status_code in (400,409)
                return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(perform,op) for op in operations]
        assert sorted(f.result(timeout=20) for f in futures)==(
            ['success','success'] if operations==('receive','receive') else ['rejected','success'])
    with factory() as db:
        inv=db.get(Inventory,inventory_id)
        if operations==('receive','receive'):
            assert inv.stock_actual==3
        else:
            assert inv.stock_actual-inv.stock_reservado==0
        assert db.query(Sale).count()+db.query(Reservation).count()<=(0 if operations==('receive','receive') else 1)


@pytest.mark.parametrize('holder', ['reservation', 'wallet'])
def test_user_serialization_does_not_block_sale_foreign_key(committed_shop, monkeypatch, holder):
    """A customer mutex must allow FK key-share locks from an unrelated sale."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import text
    from app.models.branch import Branch
    from app.models.user import User
    from app.services.wallet_service import WalletService
    import app.services.reservation_service as reservation_module
    user_id, variant_id, branch_id, _ = committed_shop[0]
    factory = committed_shop[1]
    user_locked, sale_finished = Event(), Event()
    original_lock = reservation_module.lock_branch

    def delayed_branch(db, branch):
        user_locked.set()
        assert sale_finished.wait(15)
        return original_lock(db, branch)

    monkeypatch.setattr(reservation_module, 'lock_branch', delayed_branch)

    def hold_user():
        with factory() as db:
            if holder == 'wallet':
                user = db.get(User, user_id)
                WalletService._lock_user(db, user_id, user.tenant_id)
                user_locked.set()
                assert sale_finished.wait(15)
                db.rollback()
                return 'released'
            try:
                ReservationService.create_reservation(db, ReservationCreate(
                    sucursal_id=branch_id,
                    fecha_hora_esperada=datetime.now(timezone.utc)+timedelta(hours=1),
                    items=[dict(variante_id=variant_id, cantidad=1)]), user_id)
                return 'reserved'
            except HTTPException as error:
                assert error.status_code == 400
                return 'rejected'

    def sell():
        try:
            with factory() as db:
                db.execute(text("SET LOCAL lock_timeout = '3s'"))
                SaleService.create_presencial_sale(db, sale_data(
                    db.get(Branch, branch_id), db.get(ProductVariant, variant_id)), user_id)
                return 'sold'
        finally:
            sale_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(hold_user)
        assert user_locked.wait(15)
        sale = pool.submit(sell)
        assert sale.result(timeout=15) == 'sold'
        assert pending.result(timeout=15) == ('released' if holder == 'wallet' else 'rejected')


@pytest.mark.parametrize('operation',['cancel','exchange'])
def test_concurrent_reversals_cannot_return_same_prenda_twice(committed_shop,operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.models.branch import Branch
    from app.services.inventory_service import InventoryService
    from app.schemas.inventory import ReceptionCreate
    user_id,variant_id,branch_id,inventory_id=committed_shop[0];factory=committed_shop[1]
    with factory() as db:
        result=SaleService.create_presencial_sale(db,sale_data(db.get(Branch,branch_id),db.get(ProductVariant,variant_id)),user_id)
        if operation=='exchange':
            InventoryService.receive_products(db,ReceptionCreate(sucursal_id=branch_id,
                productos=[dict(variante_id=variant_id,cantidad=1)]),user_id)
    barrier=Barrier(2)
    def perform():
        with factory() as db:
            from sqlalchemy import text
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            variant=db.get(ProductVariant,variant_id)
            barrier.wait(timeout=10)
            try:
                if operation=='cancel': SaleService.cancel_sale(db,result['id'],'Prueba concurrente',user_id)
                else: ExchangeService.create_exchange(db,exchange_data(result['numero_recibo'],variant,variant),user_id)
                return 'success'
            except HTTPException as error:
                assert error.status_code in (400,409)
                return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(perform) for _ in range(2)]
        assert sorted(f.result(timeout=20) for f in futures)==['rejected','success']
    with factory() as db:
        assert db.get(Inventory,inventory_id).stock_actual==1
        assert db.query(ProductExchange).count()==(1 if operation=='exchange' else 0)
