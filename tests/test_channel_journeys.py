"""HTTP journeys used by the Angular/Flutter clients, isolated PostgreSQL only."""
import uuid,json
from datetime import datetime,timedelta,timezone,date
from pathlib import Path
from decimal import Decimal
import pytest
from tests.test_tenant_sales import shops
from tests.test_tenant_contract import headers
from app.models.user import User
from app.models.role import Role
from app.models.inventory import Inventory
from app.models.sale import Sale
from app.models.subscription import Subscription
from app.models.subscription_plan import SubscriptionPlan
from app.core.security import get_password_hash

@pytest.fixture
def journey(db_session,shops):
    admin,variant,branches=shops['a']
    variant.producto.nombre='Camisa Journey';variant.producto.categoria.nombre='Categoria Base'
    # shops already supplies an explicit current contract; do not create a second subscription.
    customer=User(email='journey@example.com',full_name='Journey Customer',tenant_id=admin.tenant_id,
        role_id=db_session.query(Role).filter_by(name='cliente').one().id,hashed_password=get_password_hash('JourneyTest123!'),is_active=True)
    db_session.add(customer);db_session.commit()
    return admin,customer,variant,branches

def request(client,method,path,actor,expected=200,**kwargs):
    response=client.request(method,'/api/v1'+path,headers=headers(actor),**kwargs)
    assert response.status_code==expected,(method,path,response.status_code,response.text)
    return response.json() if response.content else None

@pytest.mark.parametrize('branch_index',[0,1])
def test_login_catalog_reserve_cancel_wallet_purchase_history(client,db_session,journey,branch_index):
    from app.services.wallet_service import WalletService
    admin,customer,v,branches=journey;b=branches[branch_index]
    login=client.post('/api/v1/auth/login',json={'email':customer.email,'password':'JourneyTest123!'})
    assert login.status_code==200,login.text
    assert login.json()['user']['tenant_id']==str(admin.tenant_id)
    v.producto.en_oferta=True;v.producto.precio_oferta=7;v.precio_extra=2;db_session.commit()
    products=request(client,'GET','/productos',customer,params={'activo':True,'search':v.producto.nombre})
    assert len(products)==1 and Decimal(str(products[0]['precio_oferta']))==7
    product=request(client,'GET',f'/productos/{v.producto_id}',customer)
    assert product['variantes'][0]['id']==str(v.id)
    availability=request(client,'GET','/inventario/disponibilidad',customer,params={'producto_id':str(v.producto_id),'talla':'M','color':'Azul'})
    assert len(availability)==2 and all(Decimal(str(row['precio']))==9 for row in availability)
    favorite=request(client,'POST','/favorites',customer,201,json={'product_id':str(v.producto_id)})
    assert favorite['producto']['id']==str(v.producto_id)
    assert request(client,'GET','/favorites',customer)[0]['product_id']==str(v.producto_id)
    reservation=request(client,'POST','/reservas',customer,201,json={'sucursal_id':str(b.id),
        'fecha_hora_esperada':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),
        'items':[{'variante_id':str(v.id),'cantidad':1}]})
    assert Decimal(str(reservation['total_estimado']))==9
    assert request(client,'GET','/reservas/mis-reservas',customer)[0]['id']==reservation['id']
    assert request(client,'PATCH',f"/reservas/{reservation['id']}/cancelar",customer)['estado']=='CANCELADA'
    WalletService.topup_cash_admin(db_session,admin,customer.id,20)
    order={'client_operation_id':str(uuid.uuid4()),'sucursal_id':str(b.id),'monto_total':9,
        'items':[{'variante_id':str(v.id),'cantidad':1,'precio_unitario':9}]}
    paid=request(client,'POST','/wallet/sync-offline-orders',customer,json={'orders':[order]})
    assert paid['results'][0]['status']=='CONFIRMADA' and paid['nuevo_saldo']==11
    replay=request(client,'POST','/wallet/sync-offline-orders',customer,json={'orders':[order]})
    assert replay['results'][0]['receipt_number']==paid['results'][0]['receipt_number']
    history=request(client,'GET','/ventas/mis-compras',customer)
    assert len(history)==1 and history[0]['metodo_pago']=='BILLETERA'
    request(client,'GET',f"/ventas/{history[0]['id']}",customer,403)
    sale=request(client,'GET',f"/ventas/{history[0]['id']}",admin)
    assert Decimal(str(sale['monto_total']))==9
    request(client,'GET',f"/ventas/{sale['id']}/nota-venta",customer)
    request(client,'DELETE',f'/favorites/{v.producto_id}',customer)
    assert request(client,'GET','/favorites',customer)==[]
    assert db_session.query(Inventory).filter_by(sucursal_id=b.id,variante_id=v.id).one().stock_actual==9
    # Real API wire examples consumed by client contract tests. No JWTs/credentials.
    target=Path(__file__).resolve().parents[2]/'docs'/'auditoria_software'/'FASE_5_CONTRATOS_EJEMPLO.json'
    target.write_text(json.dumps({'product':product,'reservation':reservation,'sale':sale,'wallet_sync':{**paid,'nueva_firma':'TEST_ONLY_SIGNATURE'}},indent=2),encoding='utf-8')

@pytest.mark.parametrize('inactive',['product','branch','variant'])
def test_availability_does_not_offer_inactive_merchandise(client,db_session,journey,inactive):
    _,customer,v,bs=journey
    if inactive=='product':v.producto.activo=False
    elif inactive=='branch':bs[0].activa=False
    else:v.activo=False
    db_session.commit()
    result=request(client,'GET','/inventario/disponibilidad',customer,params={'producto_id':str(v.producto_id),'sucursal_id':str(bs[0].id)})
    assert result==[]

@pytest.mark.parametrize('path,payload,changes',[
 ('/categorias',{'nombre':'Camisas Journey','descripcion':'test'},{'nombre':'Camisas actualizadas'}),
 ('/proveedores',{'nombre':'Proveedor Journey','email':'provider@example.com'},{'telefono':'70000000','email':None}),
 ('/temporadas',{'nombre':'Temporada Journey','año':2026,'fecha_inicio':'2026-01-01','fecha_fin':'2026-12-31','activa':False},{'nombre':'Temporada actualizada'}),
 ('/sucursales',{'nombre':'Sucursal Journey','ciudad':'SC','direccion':'Calle Prueba'},{'direccion':'Calle Actualizada'}),
])
def test_admin_crud_contract_and_customer_mutation_denial(client,journey,path,payload,changes):
    admin,customer,_,_=journey
    request(client,'POST',path,customer,403,json=payload)
    created=request(client,'POST',path,admin,201,json=payload)
    request(client,'GET',path,admin)
    detail=request(client,'GET',f"{path}/{created['id']}",admin)
    assert detail['nombre']==payload['nombre']
    updated=request(client,'PUT',f"{path}/{created['id']}",admin,json=changes)
    assert all(updated[k]==value for k,value in changes.items())
    request(client,'DELETE',f"{path}/{created['id']}",admin,204 if path=='/temporadas' else 200)


def test_openapi_current_inventory_is_reproducible(client):
    schema=client.get('/api/v1/openapi.json').json()
    rows=[]
    for path,verbs in schema['paths'].items():
        for method,operation in verbs.items():
            if method.upper() not in ('GET','POST','PUT','PATCH','DELETE'):continue
            rows.append({'method':method.upper(),'path':path,'operation_id':operation.get('operationId'),
                'parameters':[p['name'] for p in operation.get('parameters',[])],
                'request':operation.get('requestBody',{}).get('content',{}),
                'responses':operation.get('responses',{})})
    target=Path(__file__).resolve().parents[2]/'docs'/'auditoria_software'/'FASE_5_OPENAPI.json'
    target.write_text(json.dumps({'operations':rows,'schemas':schema['components']['schemas']},indent=2),encoding='utf-8')
    assert len(rows)>=140


def test_admin_catalog_reception_reservation_cash_receipt_and_reports(client,db_session,journey):
    admin,customer,_,branches=journey;b=branches[0]
    from app.models.category import Category
    category=db_session.query(Category).filter_by(tenant_id=admin.tenant_id).one()
    product=request(client,'POST','/productos',admin,201,json={'nombre':'Pantalon Journey','precio_base':100,
        'categoria_id':str(category.id),'variantes':[{'talla':'L','color':'Negro','sku':'JOURNEY-L','precio_extra':2}]})
    vid=product['variantes'][0]['id']
    reception=request(client,'POST','/inventario/recepcion',admin,201,json={'sucursal_id':str(b.id),
        'productos':[{'variante_id':vid,'cantidad':3}],'factura':'QA-RECEPTION'})
    assert reception['items_procesados']==1
    stock=request(client,'GET',f'/inventario/sucursal/{b.id}',admin)
    assert next(row for row in stock if row['variante_id']==vid)['stock_disponible']==3
    reservation=request(client,'POST','/reservas',customer,201,json={'sucursal_id':str(b.id),
        'fecha_hora_esperada':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),
        'items':[{'variante_id':vid,'cantidad':1}]})
    rid=reservation['id']
    assert request(client,'PATCH',f'/reservas/{rid}/preparar',admin)['estado']=='PREPARADA'
    sale=request(client,'POST','/ventas/presencial',admin,201,json={'sucursal_id':str(b.id),'cliente_id':str(customer.id),
        'items':[{'variante_id':vid,'cantidad':1,'reserva_id':rid}], 'pago':{'metodo':'EFECTIVO','monto_recibido':110}})
    assert Decimal(str(sale['monto_total']))==102 and Decimal(str(sale['cambio']))==8
    assert request(client,'GET',f'/reservas/{rid}',customer)['estado']=='COMPLETADA'
    assert request(client,'GET','/notifications',customer)
    request(client,'POST','/notifications/mark-all-read',customer)
    assert request(client,'GET','/notifications/unread-count',customer)['unread_count']==0
    report=request(client,'GET','/ventas/reportes/resumen',admin)
    assert Decimal(str(report['ingresos_totales']))==102
    request(client,'GET','/ventas/reportes/resumen',customer,403)
    request(client,'GET',f"/ventas/{sale['id']}/nota-venta",customer)
    request(client,'PATCH',f"/ventas/{sale['id']}/cancelar",admin,json={'motivo':'Prueba de reversión HTTP'})
    assert Decimal(str(request(client,'GET','/ventas/reportes/resumen',admin)['ingresos_totales']))==0


def test_customer_cannot_create_discounts_but_can_validate_an_authorized_coupon(client,journey):
    admin,customer,_,_=journey
    payload={'codigo':'JOURNEY10','porcentaje_descuento':10,'monto_minimo':0}
    request(client,'POST','/coupons',customer,403,json=payload)
    coupon=request(client,'POST','/coupons',admin,201,json=payload)
    assert coupon['codigo']=='JOURNEY10'
    validation=request(client,'POST','/coupons/validate',customer,json={'codigo':' journey10 ','monto_subtotal':75})
    assert validation['valido'] and Decimal(str(validation['monto_descuento']))==Decimal('7.50')
    assert Decimal(str(validation['monto_total']))==Decimal('67.50')
