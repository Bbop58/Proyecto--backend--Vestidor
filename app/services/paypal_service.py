"""PayPal transport. Only payment_flow_service may authorize local effects."""
import base64
import json
import re
import urllib.request
import urllib.error
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from fastapi import HTTPException
from app.config import settings
from app.core.money import money


class ProviderError(HTTPException):
    def __init__(self, status_code, detail, provider_code=None):
        super().__init__(status_code, detail)
        self.provider_code = provider_code


class PayPalService:
    @staticmethod
    def _get_base_url(mode=None):
        mode = (mode or settings.PAYPAL_MODE).lower()
        if mode not in ('live', 'sandbox'):
            raise HTTPException(503, 'Modo de pasarela inválido')
        return 'https://api-m.paypal.com' if mode == 'live' else 'https://api-m.sandbox.paypal.com'

    @staticmethod
    def _id(value):
        if not re.fullmatch(r'[A-Za-z0-9-]{3,100}', str(value)):
            raise HTTPException(400, 'Identificador de pago inválido')
        return str(value)

    @staticmethod
    def _request(url, method, headers, payload=None, *, encoded=False):
        body = payload if encoded else (json.dumps(payload).encode() if payload is not None else None)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            try:
                data = json.loads(error.read().decode())
                code = next(iter(data.get('details', [])), {}).get('issue') or data.get('name')
            except (ValueError, AttributeError):
                code = None
            raise ProviderError(502 if error.code >= 500 else 400,
                                'La pasarela rechazó la operación; consulta su estado antes de reintentar', code)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderError(504, 'La pasarela no confirmó el resultado. Reintenta la misma orden para reconciliarla')
        except (ValueError, TypeError):
            raise ProviderError(502, 'Respuesta inválida de la pasarela')

    @classmethod
    def get_access_token(cls, client_id=None, client_secret=None, mode=None):
        # Callers must select the complete merchant pair explicitly.
        # A partial tenant pair must never borrow a platform credential.
        cid = client_id.strip() if isinstance(client_id,str) else ""
        secret = client_secret.strip() if isinstance(client_secret,str) else ""
        if not cid or not secret:
            raise HTTPException(503, 'Configura las credenciales de pago de la empresa')
        headers = {'Authorization': 'Basic '+base64.b64encode(f'{cid}:{secret}'.encode()).decode(),
                   'Content-Type': 'application/x-www-form-urlencoded'}
        data = cls._request(cls._get_base_url(mode)+'/v1/oauth2/token', 'POST', headers,
                            b'grant_type=client_credentials', encoded=True)
        if not data.get('access_token'):
            raise HTTPException(502, 'La pasarela no devolvió autorización')
        return data['access_token']

    @classmethod
    def _headers(cls, client_id=None, client_secret=None, mode=None, request_id=None):
        headers = {'Authorization': 'Bearer '+cls.get_access_token(client_id, client_secret, mode),
                   'Content-Type': 'application/json', 'Prefer': 'return=representation'}
        if request_id:
            headers['PayPal-Request-Id'] = str(request_id)
        return headers

    @classmethod
    def convert_bob_to_usd(cls, monto_bob, rate=None):
        rate = Decimal(str(rate or settings.PAYPAL_EXCHANGE_RATE))
        if not rate.is_finite() or rate <= 0:
            raise HTTPException(503, 'Tipo de cambio configurado inválido')
        value = (money(monto_bob, positive=True)/rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if value <= 0:
            raise HTTPException(400, 'El importe es menor al mínimo admitido por la pasarela')
        return float(value)

    @classmethod
    def create_order(cls, monto_bob, items=None, sucursal_id=None, reserva_id=None,
                     descripcion='Compra en FashionStore', return_url=None, cancel_url=None,
                     client_id=None, client_secret=None, mode=None, brand_name=None,
                     request_id=None, custom_id=None, amount_usd=None):
        usd = money(amount_usd if amount_usd is not None else cls.convert_bob_to_usd(monto_bob), positive=True)
        unit = {'reference_id': f'FICCT_{sucursal_id or "WALLET"}', 'description': descripcion[:127],
                'custom_id': str(custom_id or reserva_id or request_id or uuid.uuid4()),
                'amount': {'currency_code': 'USD', 'value': f'{usd:.2f}'}}
        # Total is authoritative. Independent per-unit FX rounding does not add up reliably.
        payload = {'intent': 'CAPTURE', 'purchase_units': [unit], 'application_context': {
            'brand_name': (brand_name or 'FashionStore')[:127], 'user_action': 'PAY_NOW',
            'return_url': return_url or 'https://proyecto-backend-vestidor-production.up.railway.app/api/v1/payments/paypal/return',
            'cancel_url': cancel_url or 'https://proyecto-backend-vestidor-production.up.railway.app/api/v1/payments/paypal/cancel'}}
        result = cls._request(cls._get_base_url(mode)+'/v2/checkout/orders', 'POST',
            cls._headers(client_id, client_secret, mode, request_id), payload)
        if not result.get('id'):
            raise HTTPException(502, 'La pasarela no devolvió una orden válida')
        return dict(order_id=result['id'], status=result.get('status', 'CREATED'),
                    monto_bob=float(money(monto_bob)), monto_usd=float(usd), exchange_rate=settings.PAYPAL_EXCHANGE_RATE,
                    approve_url=next((l['href'] for l in result.get('links', []) if l.get('rel') in ('approve','payer-action')), None))

    @classmethod
    def capture_order(cls, order_id, client_id=None, client_secret=None, mode=None, request_id=None):
        order_id = cls._id(order_id)
        try:
            result = cls._request(cls._get_base_url(mode)+f'/v2/checkout/orders/{order_id}/capture', 'POST',
                cls._headers(client_id, client_secret, mode, request_id), {})
            if result.get('status') == 'COMPLETED':
                # Capture responses may omit purchase-unit reconciliation fields.
                # Read the same order, then let the flow verify custom_id, amount,
                # currency and capture identity. Never issue a second charge here.
                result = cls.get_order_details(order_id, client_id, client_secret, mode)
        except ProviderError as error:
            if error.provider_code not in ('ORDER_ALREADY_CAPTURED','ORDER_COMPLETED_DONE'):
                raise
            result = cls.get_order_details(order_id, client_id, client_secret, mode)
        return {'order_id': result.get('id'), 'status': result.get('status'), 'raw_response': result}

    @classmethod
    def get_order_details(cls, order_id, client_id=None, client_secret=None, mode=None):
        return cls._request(cls._get_base_url(mode)+f'/v2/checkout/orders/{cls._id(order_id)}', 'GET',
                            cls._headers(client_id, client_secret, mode))

    @classmethod
    def refund_capture(cls, capture_id, client_id=None, client_secret=None, mode=None, request_id=None):
        return cls._request(cls._get_base_url(mode)+f'/v2/payments/captures/{cls._id(capture_id)}/refund', 'POST',
                            cls._headers(client_id, client_secret, mode, request_id), {})

    @classmethod
    def get_refund_details(cls, refund_id, client_id=None, client_secret=None, mode=None):
        return cls._request(cls._get_base_url(mode)+f'/v2/payments/refunds/{cls._id(refund_id)}', 'GET',
                            cls._headers(client_id, client_secret, mode))

    @classmethod
    def verify_webhook_signature(cls, headers, event, webhook_id, client_id=None, client_secret=None, mode=None):
        if not webhook_id:raise HTTPException(503,'El webhook de plataforma no está configurado')
        fields={'auth_algo':'paypal-auth-algo','cert_url':'paypal-cert-url','transmission_id':'paypal-transmission-id',
            'transmission_sig':'paypal-transmission-sig','transmission_time':'paypal-transmission-time'}
        normalized={k.lower():v for k,v in headers.items()}
        if any(not normalized.get(name) or len(normalized[name])>3000 for name in fields.values()):
            raise HTTPException(400,'Firma del evento incompleta')
        payload={key:normalized[name] for key,name in fields.items()}
        payload.update(webhook_id=webhook_id,webhook_event=event)
        result=cls._request(cls._get_base_url(mode)+'/v1/notifications/verify-webhook-signature','POST',
            cls._headers(client_id,client_secret,mode),payload)
        if result.get('verification_status')!='SUCCESS':raise HTTPException(400,'Firma del evento no verificada')
        return True
