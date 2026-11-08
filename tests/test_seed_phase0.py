"""Phase 0: real PostgreSQL, only the disposable engine validated by conftest."""
import json
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app import seed_database as seed
from app.seed_audit import reconcile
from app.models.product import Product
from app.models.user import User
from app.models.role import Role
from app.models.coupon import Coupon
from app.models.delivery_rate import DeliveryRate
from app.models.sale import Sale
from app.models.inventory import Inventory
from app.models.inventory_movement import InventoryMovement
from tests.conftest import test_engine


@pytest.fixture
def seed_database(monkeypatch):
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    factory = sessionmaker(bind=test_engine, autoflush=False)
    monkeypatch.setattr(seed, "SessionLocal", factory)
    monkeypatch.setattr(seed, "engine", test_engine)
    # Historical implementation used an independently owned session.
    import app.seed_permissions as permissions
    monkeypatch.setattr(permissions, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)


def snapshot(factory):
    with factory() as db:
        return {t.name: sorted(json.dumps(dict(r),default=str,sort_keys=True)
                for r in db.execute(select(t)).mappings()) for t in Base.metadata.sorted_tables}


def test_demo_contract_starts_on_bolivia_day_when_utc_host_is_already_tomorrow(seed_database,monkeypatch):
    from datetime import date,timedelta
    from app.core import billing_calendar
    from app.models import Subscription,SubscriptionTerm
    from app.services.entitlement_service import EntitlementService
    day=date(2026,11,7)
    class UtcHostDate(date):
        @classmethod
        def today(cls):return cls(2026,11,8)
    monkeypatch.setattr(seed,'date',UtcHostDate)
    monkeypatch.setattr(billing_calendar,'business_today',lambda:day)
    seed.seed_all()
    with seed_database() as db:
        for email in ('admin@urbanstyle.com','admin@modaexpress.com'):
            user=db.query(User).filter_by(email=email).one()
            effective=EntitlementService.resolve(db,user)
            assert effective['active'],effective['estado_efectivo']
            assert effective['term']['fecha_inicio']==day
            assert effective['subscription']['fecha_inicio']==day
        original=[dict(r) for r in db.execute(select(SubscriptionTerm.__table__).order_by(SubscriptionTerm.id)).mappings()]
    # A rerun on the next business day preserves the existing contract.
    monkeypatch.setattr(billing_calendar,'business_today',lambda:day+timedelta(days=1))
    seed.seed_all()
    with seed_database() as db:
        assert [dict(r) for r in db.execute(select(SubscriptionTerm.__table__).order_by(SubscriptionTerm.id)).mappings()]==original


def test_seed_reconciles_and_preserves_edits(seed_database):
    seed.seed_all()
    with seed_database() as db:
        assert not any(reconcile(db).values()), reconcile(db)
        product = db.query(Product).first()
        product.precio_base = 1999
        product.activo = False
        user = db.query(User).filter(User.email == "admin@urbanstyle.com").one()
        user.full_name = "Preserved audit edit"
        user.hashed_password = "not-a-login-hash"
        role = db.query(Role).filter(Role.name == "cajero").one()
        role.permissions = []
        db.commit()
        from app.services.wallet_service import WalletService
        customer = db.query(User).filter(User.email == "cliente@ficttstore.com").one()
        wallet = WalletService.topup_cash_admin(db, user, customer.id, 100)
        wallet = WalletService.topup_cash_admin(db, user, customer.id, 50)
        assert wallet.saldo_actual == 150
        assert wallet.nonce_secuencia == 2
        assert WalletService.verify_wallet_integrity(wallet)
        assert reconcile(db)["wallet_balance"] == 0
    before = snapshot(seed_database)
    seed.seed_all()
    assert snapshot(seed_database) == before


def test_seed_recovers_missing_aggregates_above_100_sales(seed_database):
    seed.seed_all()
    with seed_database() as db:
        expected = {c: db.query(c).count() for c in (Sale,Coupon,DeliveryRate)}
        sale = db.query(Sale).filter(Sale.numero_recibo == "VTA-MODA-10-0089-TJC").one()
        # Simulate absence of a complete aggregate, undoing its stock effect.
        for mov in db.query(InventoryMovement).filter_by(referencia=sale.numero_recibo).all():
            db.get(Inventory,mov.inventario_id).stock_actual += mov.cantidad
            db.delete(mov)
        db.delete(sale)
        db.delete(db.query(Coupon).first())
        db.delete(db.query(DeliveryRate).first())
        db.commit()
        assert db.query(Sale).count()>100
    seed.seed_all()
    with seed_database() as db:
        assert {c:db.query(c).count() for c in expected} == expected
        assert not any(reconcile(db).values()), reconcile(db)


def test_seed_failure_rolls_back_and_retry_succeeds(seed_database,monkeypatch):
    before = snapshot(seed_database)
    original = seed.seed_delivery_rates_and_favorites
    def fail(*args,**kwargs):
        raise RuntimeError("Injected interruption after historical sales")
    monkeypatch.setattr(seed,"seed_delivery_rates_and_favorites",fail)
    with pytest.raises(RuntimeError,match="Injected interruption"):
        seed.seed_all()
    assert snapshot(seed_database) == before
    monkeypatch.setattr(seed,"seed_delivery_rates_and_favorites",original)
    seed.seed_all()
    with seed_database() as db:
        assert db.query(Sale).count()==1155
        assert not any(reconcile(db).values())
