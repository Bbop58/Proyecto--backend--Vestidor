import json
import base64
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple, Optional
from fastapi import HTTPException, status
from app.config import settings


class PayPalService:
    @staticmethod
    def _get_base_url(mode: Optional[str] = None) -> str:
        current_mode = (mode or settings.PAYPAL_MODE or "sandbox").lower()
        if current_mode == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @staticmethod
    def _get_auth_header(client_id: Optional[str] = None, client_secret: Optional[str] = None) -> str:
        cid = client_id or settings.PAYPAL_CLIENT_ID
        csec = client_secret or settings.PAYPAL_CLIENT_SECRET
        credentials = f"{cid}:{csec}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    @classmethod
    def get_access_token(
        cls,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        mode: Optional[str] = None
    ) -> str:
        """Obtiene un Access Token OAuth2 desde la API de PayPal (propio del tenant o global de plataforma)."""
        base_url = cls._get_base_url(mode)
        url = f"{base_url}/v1/oauth2/token"
        headers = {
            "Authorization": cls._get_auth_header(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = "grant_type=client_credentials".encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as response:
                body = response.read().decode("utf-8")
                payload = json.loads(body)
                return payload.get("access_token")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Error autenticando con PayPal ({e.code}): {err_body}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error interno conectando con PayPal: {str(e)}"
            )

    @classmethod
    def convert_bob_to_usd(cls, monto_bob: float) -> float:
        """Convierte monto en Bolivianos a Dólares USD redondeado a 2 decimales."""
        rate = settings.PAYPAL_EXCHANGE_RATE or 6.96
        monto_usd = round(float(monto_bob) / rate, 2)
        # PayPal requiere mínimo 0.01 USD
        return max(0.01, monto_usd)

    @classmethod
    def create_order(
        cls,
        monto_bob: float,
        items: Optional[list] = None,
        sucursal_id: Optional[str] = None,
        reserva_id: Optional[str] = None,
        descripcion: str = "Compra en FICCT STORE",
        return_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        mode: Optional[str] = None,
        brand_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Crea una orden de pago en PayPal REST API v2 en la cuenta correspondiente."""
        token = cls.get_access_token(client_id=client_id, client_secret=client_secret, mode=mode)
        base_url = cls._get_base_url(mode)
        url = f"{base_url}/v2/checkout/orders"
        monto_usd = cls.convert_bob_to_usd(monto_bob)

        # Construir ítems si fueron proporcionados
        item_list = []
        if items:
            for it in items:
                u_usd = cls.convert_bob_to_usd(it.unit_amount_bob)
                item_list.append({
                    "name": it.name[:127],
                    "quantity": str(it.quantity),
                    "unit_amount": {
                        "currency_code": "USD",
                        "value": f"{u_usd:.2f}"
                    }
                })

        purchase_unit = {
            "reference_id": f"FICCT_{sucursal_id or 'POS'}",
            "description": descripcion[:127],
            "amount": {
                "currency_code": "USD",
                "value": f"{monto_usd:.2f}"
            }
        }

        if reserva_id:
            purchase_unit["custom_id"] = str(reserva_id)

        # URLs de retorno y cancelación
        final_return_url = return_url or "https://proyecto-backend-vestidor-production.up.railway.app/api/v1/payments/paypal/return"
        final_cancel_url = cancel_url or "https://proyecto-backend-vestidor-production.up.railway.app/api/v1/payments/paypal/cancel"

        payload = {
            "intent": "CAPTURE",
            "purchase_units": [purchase_unit],
            "application_context": {
                "brand_name": brand_name or "FICCT STORE",
                "landing_page": "NO_PREFERENCE",
                "user_action": "PAY_NOW",
                "return_url": final_return_url,
                "cancel_url": final_cancel_url
            }
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req) as response:
                body = response.read().decode("utf-8")
                res_data = json.loads(body)
                
                # Extraer approve link
                approve_url = None
                for link in res_data.get("links", []):
                    if link.get("rel") == "approve":
                        approve_url = link.get("href")
                        break

                return {
                    "order_id": res_data.get("id"),
                    "status": res_data.get("status"),
                    "monto_bob": monto_bob,
                    "monto_usd": monto_usd,
                    "exchange_rate": settings.PAYPAL_EXCHANGE_RATE,
                    "approve_url": approve_url
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Error de PayPal al crear la orden ({e.code}): {err_body}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error interno creando orden PayPal: {str(e)}"
            )

    @classmethod
    def capture_order(
        cls,
        order_id: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """Captura los fondos de una orden aprobada en PayPal REST API v2."""
        token = cls.get_access_token(client_id=client_id, client_secret=client_secret, mode=mode)
        base_url = cls._get_base_url(mode)
        url = f"{base_url}/v2/checkout/orders/{order_id}/capture"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        # Empty body for capture POST
        data = json.dumps({}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req) as response:
                body = response.read().decode("utf-8")
                res_data = json.loads(body)
                
                capture_id = None
                purchase_units = res_data.get("purchase_units", [])
                if purchase_units:
                    payments = purchase_units[0].get("payments", {})
                    captures = payments.get("captures", [])
                    if captures:
                        capture_id = captures[0].get("id")

                capture_status = res_data.get("status")  # Should be 'COMPLETED'
                
                amount_usd = 0.0
                if purchase_units and purchase_units[0].get("amount"):
                    amount_usd = float(purchase_units[0]["amount"].get("value", 0.0))

                return {
                    "order_id": res_data.get("id"),
                    "capture_id": capture_id or res_data.get("id"),
                    "status": capture_status,
                    "monto_usd": amount_usd,
                    "raw_response": res_data
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Error de PayPal al capturar la orden ({e.code}): {err_body}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error interno capturando orden PayPal: {str(e)}"
            )

    @classmethod
    def get_order_details(
        cls,
        order_id: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """Consulta el estado y detalle de una orden en PayPal."""
        token = cls.get_access_token(client_id=client_id, client_secret=client_secret, mode=mode)
        base_url = cls._get_base_url(mode)
        url = f"{base_url}/v2/checkout/orders/{order_id}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        req = urllib.request.Request(url, headers=headers, method="GET")

        try:
            with urllib.request.urlopen(req) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Orden PayPal no encontrada ({e.code}): {err_body}"
            )
