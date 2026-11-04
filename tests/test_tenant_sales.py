import uuid
from datetime import datetime, timedelta, timezone
import pytest
from tests.support_payments import fake_paypal
from app.api.deps import get_current_user
from app.main import app
from app.seed_permissions import seed
from app.models.tenant import Tenant
from app.models.role import Role
from app.models.user import User
from app.models.branch import Branch
from app.models.category import Category
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.inventory import Inventory
from app.models.sale import Sale
from app.models.sale_detail import SaleDetail
from app.models.inventory_movement import InventoryMovement
from app.models.reservation import Reservation, ReservationStatus


@pytest.fixture
def shops(db_session):
    db=db_session
    seed(db)
    role=db.query(Role).filter_by(name='admin').one()
    result={}
    for key in ('a','b'):
        t=Tenant(nombre=key,razon_social=key,nit_rut=key,slug=key,activo=True)
        db.add(t);db.flush()
        user=User(email=f'{key}@example.com',full_name=f'User {key}',hashed_password='unused',role_id=role.id,tenant_id=t.id,is_active=True)
        cat=Category(nombre=f"Category {key}",tenant_id=t.id,activa=True)
        db.add_all([user,cat]);db.flush()
        p=Product(nombre=f"Product {key}",categoria_id=cat.id,tenant_id=t.id,precio_base=10,activo=True)
        db.add(p);db.flush()
        v=ProductVariant(producto_id=p.id,sku=key,talla='M',color='Azul',precio_extra=0,activo=True)
        db.add(v);db.flush()
        branches=[]
        for index in range(2):
            branch=Branch(nombre=f'{key}{index}',tenant_id=t.id,ciudad='SC',direccion='Test',activa=True)
            db.add(branch);db.flush()
            db.add(Inventory(tenant_id=t.id,sucursal_id=branch.id,variante_id=v.id,stock_actual=10,stock_reservado=0,activo=True))
            branches.append(branch)
        result[key]=(user,v,branches)
    db.commit()
    return result


def payload(branch,variant,digital=False):
    p={'sucursal_id':str(branch.id),'items':[{'variante_id':str(variant.id),'cantidad':1}]}
    if not digital:p['pago']={'metodo':'EFECTIVO','monto_recibido':100}
    return p


def snapshot(db):
    return (db.query(Sale).count(),db.query(SaleDetail).count(),db.query(InventoryMovement).count(),
            sorted((str(i.id),i.stock_actual,i.stock_reservado) for i in db.query(Inventory).all()))


@pytest.mark.parametrize('attack,digital',[
    ('branch',False),('branch',True),('client',False),('variant',False),('variant',True),
    ('tenantless',False),('tenantless',True),('reservation',False)])
def test_sales_reject_foreign_references_without_writes(client,db_session,shops,digital,attack):
    a,va,ba=shops['a'];b,vb,bb=shops['b']
    app.dependency_overrides[get_current_user]=lambda:a
    data=payload(ba[0],va,digital)
    if attack=='branch':data=payload(bb[0],vb,digital)
    if attack=='client':
        data['cliente_id']=str(b.id)
    if attack=='variant':data['items'].append({'variante_id':str(vb.id),'cantidad':1})
    if attack=='tenantless':a.tenant_id=None;db_session.commit()
    if attack=='reservation':
        r=Reservation(tenant_id=b.tenant_id,codigo='B',cliente_id=b.id,sucursal_id=bb[0].id,
            estado=ReservationStatus.PREPARADA,fecha_hora_esperada=datetime.now(timezone.utc),
            fecha_expiracion=datetime.now(timezone.utc)+timedelta(days=1))
        db_session.add(r);db_session.commit()
        data['items'][0]['reserva_id']=str(r.id)
    before=snapshot(db_session)
    response=client.post('/api/v1/ventas/'+('digital' if digital else 'presencial'),json=data)
    assert response.status_code in (400,403,404),response.text
    assert snapshot(db_session)==before


@pytest.mark.parametrize('digital',[False,True])
def test_account_buys_in_both_own_branches(client,db_session,shops,digital,fake_paypal):
    user,var,branches=shops['a']
    if digital:
        user.role_id=db_session.query(Role).filter_by(name='cliente').one().id
        db_session.commit()
    app.dependency_overrides[get_current_user]=lambda:user
    for branch in branches:
        if digital:
            created=client.post('/api/v1/payments/paypal/create-order',json={
                'monto_bob':10,'sucursal_id':str(branch.id),'detalles':payload(branch,var,True)['items']})
            assert created.status_code==201,created.text
            response=client.post('/api/v1/payments/paypal/capture-order',json={
                'order_id':created.json()['order_id'],'sucursal_id':str(branch.id),'detalles':payload(branch,var,True)['items']})
            assert response.status_code==200,response.text
        else:
            response=client.post('/api/v1/ventas/presencial',json=payload(branch,var))
        assert response.status_code==(200 if digital else 201),response.text
    assert all(s.tenant_id==user.tenant_id for s in db_session.query(Sale).all())
    assert all(m.tenant_id==user.tenant_id for m in db_session.query(InventoryMovement).all())


def test_receipt_requires_authentication(client):
    response=client.get(f'/api/v1/ventas/{uuid.uuid4()}/nota-venta')
    assert response.status_code==401


@pytest.mark.parametrize('operation',['receipt','cancel','detail'])
def test_foreign_sale_cannot_be_read_or_cancelled(client,db_session,shops,operation):
    a,_,_=shops['a'];b,vb,bb=shops['b']
    app.dependency_overrides[get_current_user]=lambda:b
    response=client.post('/api/v1/ventas/presencial',json=payload(bb[0],vb))
    assert response.status_code==201,response.text
    sale_id=response.json()['id']
    app.dependency_overrides[get_current_user]=lambda:a
    before=snapshot(db_session)
    if operation=='cancel':
        response=client.patch(f'/api/v1/ventas/{sale_id}/cancelar',json={'motivo':'Prueba cruzada'})
    else:
        path=f'/api/v1/ventas/{sale_id}'+('/nota-venta' if operation=='receipt' else '')
        response=client.get(path)
    assert response.status_code in (403,404),response.text
    assert snapshot(db_session)==before


@pytest.mark.parametrize('operation',['create-order','capture-order'])
def test_paypal_rejects_foreign_branch_before_contacting_provider(client,db_session,shops,monkeypatch,operation):
    from app.services.paypal_service import PayPalService
    def forbidden(*args,**kwargs):
        pytest.fail('Unauthorized request contacted PayPal')
    monkeypatch.setattr(PayPalService,'create_order',forbidden)
    monkeypatch.setattr(PayPalService,'capture_order',forbidden)
    a,_,_=shops['a'];_,vb,bb=shops['b']
    app.dependency_overrides[get_current_user]=lambda:a
    data={'sucursal_id':str(bb[0].id),'monto_bob':10}
    if operation=='capture-order':
        data={'sucursal_id':str(bb[0].id),'order_id':'audit-no-charge',
              'detalles':[{'variante_id':str(vb.id),'cantidad':1}]}
    before=snapshot(db_session)
    response=client.post('/api/v1/payments/paypal/'+operation,json=data)
    assert response.status_code in (403,404),response.text
    assert snapshot(db_session)==before
