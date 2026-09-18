import json
import base64
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple, Optional
from fastapi import HTTPException, status
from app.config import settings


class PayPalService:
    @staticmethod
    def _get_auth_header() -> str:
        credentials = f"{settings.PAYPAL_CLIENT_ID}:{settings.PAYPAL_CLIENT_SECRET}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    @classmethod
    def get_access_token(cls) -> str:
        """Obtiene un Access Token OAuth2 desde la API de PayPal."""
        url = f"{settings.PAYPAL_API_BASE_URL}/v1/oauth2/token"
        headers = {
            "Authorization": cls._get_auth_header(),
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
        descripcion: str = "Compra en FICCT STORE"
    ) -> Dict[str, Any]:
        """Crea una orden de pago en PayPal REST API v2."""
        token = cls.get_access_token()
        url = f"{settings.PAYPAL_API_BASE_URL}/v2/checkout/orders"
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

        payload = {
            "intent": "CAPTURE",
            "purchase_units": [purchase_unit],
            "application_context": {
                "brand_name": "FICCT STORE",
                "landing_page": "NO_PREFERENCE",
                "user_action": "PAY_NOW",
                "return_url": "http://localhost:4200/ventas/pos?paypal=success",
                "cancel_url": "http://localhost:4200/ventas/pos?paypal=cancel"
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
    def capture_order(cls, order_id: str) -> Dict[str, Any]:
        """Captura los fondos de una orden aprobada en PayPal REST API v2."""
        token = cls.get_access_token()
        url = f"{settings.PAYPAL_API_BASE_URL}/v2/checkout/orders/{order_id}/capture"

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
    def get_order_details(cls, order_id: str) -> Dict[str, Any]:
        """Consulta el estado y detalle de una orden en PayPal."""
        token = cls.get_access_token()
        url = f"{settings.PAYPAL_API_BASE_URL}/v2/checkout/orders/{order_id}"

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
