import requests
from datetime import datetime, timedelta, timezone

BASE_URL = 'http://127.0.0.1:8000/api/v1'

def run_test():
    # 1. Login Cliente
    r = requests.post(f'{BASE_URL}/auth/login', json={'email': 'cliente@ficttstore.com', 'password': 'Password123!'})
    assert r.status_code == 200, f'Login failed: {r.text}'
    client_token = r.json()['access_token']
    print('[OK] 1. Login Cliente exitoso')

    # 2. Get branches & products
    r_branches = requests.get(f'{BASE_URL}/sucursales', headers={'Authorization': f'Bearer {client_token}'})
    branches = r_branches.json()
    branch_id = branches[0]['id']
    branch_name = branches[0]['nombre']

    r_prods = requests.get(f'{BASE_URL}/productos', headers={'Authorization': f'Bearer {client_token}'})
    prods = r_prods.json()
    variant_id = prods[0]['variantes'][0]['id']
    prod_name = prods[0]['nombre']
    print(f'[OK] 2. Sucursal: {branch_name}, Producto: {prod_name}')

    # 3. Create Reservation
    future_date = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    payload = {
        'sucursal_id': branch_id,
        'fecha_hora_esperada': future_date,
        'items': [{'variante_id': variant_id, 'cantidad': 1}],
        'nota': 'Prueba automatizada de reserva'
    }
    r_res = requests.post(f'{BASE_URL}/reservas', json=payload, headers={'Authorization': f'Bearer {client_token}'})
    assert r_res.status_code == 201, f'Create reservation failed: {r_res.text}'
    res_data = r_res.json()
    res_id = res_data['id']
    codigo = res_data['codigo']
    print(f'[OK] 3. Reserva Creada: {codigo} (Estado: {res_data["estado"]})')

    # 4. Login Cajero
    r_cajero = requests.post(f'{BASE_URL}/auth/login', json={'email': 'cajero@ficttstore.com', 'password': 'Password123!'})
    cajero_token = r_cajero.json()['access_token']
    print('[OK] 4. Login Cajero exitoso')

    # 5. Process Sale in POS
    sale_payload = {
        'sucursal_id': branch_id,
        'items': [{
            'variante_id': variant_id,
            'cantidad': 1,
            'precio_unitario': res_data['detalles'][0]['precio_unitario'],
            'reserva_id': res_id
        }],
        'pago': {'metodo': 'EFECTIVO', 'monto_recibido': 200.0},
        'nota': f'Cobro en caja de {codigo}'
    }
    r_sale = requests.post(f'{BASE_URL}/ventas', json=sale_payload, headers={'Authorization': f'Bearer {cajero_token}'})
    assert r_sale.status_code == 201, f'Sale failed: {r_sale.text}'
    sale_data = r_sale.json()
    print(f'[OK] 5. Venta POS Registrada: Recibo {sale_data["numero_recibo"]}, Total: {sale_data["monto_total"]} Bs., Cambio: {sale_data["cambio"]} Bs.')

    # 6. Verify Reservation Completed
    r_check = requests.get(f'{BASE_URL}/reservas/{res_id}', headers={'Authorization': f'Bearer {cajero_token}'})
    final_res = r_check.json()
    print(f'[OK] 6. Estado final de la reserva: {final_res["estado"]}')
    assert final_res['estado'] == 'COMPLETADA', 'Reservation should be COMPLETADA'

    print('\n=============================================')
    print('¡TODOS LOS FLUJOS PROBADOS Y 100% OPERATIVOS!')
    print('=============================================')

if __name__ == '__main__':
    run_test()
