"""Supplemental demo buyers have real receipt/detail/stock aggregates."""
from datetime import datetime,timezone
import json
import pytest
from sqlalchemy import select
from app.database import Base
from app.models import Sale,SaleDetail,InventoryMovement,Inventory,User,Role
from app.ml.analytics_data import build_customer_rfm
from tests.test_tenant_sales import shops
from app.seed_ml_demo import seed_ml_demo


def snapshot(db):
    return {t.name:sorted(json.dumps(dict(r),default=str,sort_keys=True) for r in db.connection().execute(select(t)).mappings()) for t in Base.metadata.sorted_tables}


def prepare(db,shops):
    for inv in db.query(Inventory).all():inv.stock_actual=100;inv.stock_reservado=2
    db.commit()
    actor,variant,branches=shops['b']
    return actor.tenant,branches,[variant],actor


def test_cohort_is_scoped_reconciled_and_repeat_preserves_every_existing_row(db_session,shops):
    db=db_session;args=prepare(db,shops)
    before_stock={i.id:i.stock_actual for i in db.query(Inventory).all()}
    seed_ml_demo(db,*args);db.flush()
    sales=db.query(Sale).filter(Sale.numero_recibo.like('VTA-ML-MODA-%')).all()
    assert len(sales)==20 and all(s.tenant_id==args[0].id for s in sales)
    assert db.query(SaleDetail).count()==20
    moves=db.query(InventoryMovement).all();assert len(moves)==20
    assert all(m.tenant_id==args[0].id and m.stock_antes-m.stock_despues==m.cantidad==1 for m in moves)
    assert sum(before_stock[i.id]-i.stock_actual for i in db.query(Inventory))==20
    assert all(i.stock_reservado==2 for i in db.query(Inventory))
    rfm=build_customer_rfm(db,args[0].id,datetime(2026,11,6,16,tzinfo=timezone.utc))
    assert len(rfm.rows)==8 and sorted(r['frequency'] for r in rfm.rows)==[1,1,2,2,3,3,4,4]
    assert len({(r['recency_days'],r['frequency'],r['monetary_bob']) for r in rfm.rows})>=4
    assert sum(r['monetary_bob'] for r in rfm.rows)==sum(s.monto_total for s in sales)
    assert build_customer_rfm(db,shops['a'][0].tenant_id,datetime(2026,11,6,16,tzinfo=timezone.utc)).rows==[]
    sales[0].nota='Preserved operator edit';db.query(User).filter_by(email='demo.ml.moda.01@example.com').one().full_name='Preserved name';db.flush()
    before=snapshot(db);seed_ml_demo(db,*args);db.flush();assert snapshot(db)==before


def test_foreign_customer_email_is_not_reassigned(db_session,shops):
    db=db_session;args=prepare(db,shops);role=db.query(Role).filter_by(name='cliente',tenant_id=None).one()
    db.add(User(tenant_id=shops['a'][0].tenant_id,role_id=role.id,email='demo.ml.moda.01@example.com',full_name='Do not reassign',hashed_password='unused'));db.commit()
    before=snapshot(db)
    with pytest.raises(ValueError):
        with db.begin_nested():seed_ml_demo(db,*args)
    assert snapshot(db)==before


def test_reserved_stock_is_never_spent_and_failure_rolls_back(db_session,shops):
    db=db_session;args=prepare(db,shops)
    for i in db.query(Inventory).filter_by(tenant_id=args[0].id):i.stock_reservado=i.stock_actual
    db.commit();before=snapshot(db)
    with pytest.raises(ValueError):
        with db.begin_nested():seed_ml_demo(db,*args)
    assert snapshot(db)==before


@pytest.mark.parametrize('foreign',['branch','variant','cashier'])
def test_foreign_relationships_are_rejected_before_inserting(db_session,shops,foreign):
    db=db_session;tenant,branches,variants,cashier=prepare(db,shops)
    if foreign=='branch':branches=shops['a'][2]
    if foreign=='variant':variants=[shops['a'][1]]
    if foreign=='cashier':cashier=shops['a'][0]
    before=snapshot(db)
    with pytest.raises(ValueError):seed_ml_demo(db,tenant,branches,variants,cashier)
    assert snapshot(db)==before
