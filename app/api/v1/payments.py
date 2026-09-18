import uuid
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.api.deps import get_current_user
from app.models.user import User
from app.models.sale import PaymentMethod
from app.schemas.sale import PresencialSaleCreate, PaymentInfo, SaleItemCreate, SaleResponse
from app.schemas.paypal import (
    PayPalCreateOrderRequest,
    PayPalCreateOrderResponse,
    PayPalCaptureOrderRequest,
    PayPalCaptureOrderResponse,
)
from app.services.paypal_service import PayPalService
from app.services.sale_service import SaleService

router = APIRouter(prefix="/payments", tags=["Pagos y Pasarelas"])


@router.get(
    "/paypal/config",
    summary="Obtener configuración pública de PayPal Sandbox"
)
def get_paypal_config():
    """Retorna la configuración necesaria para el SDK de PayPal en el cliente."""
    return {
        "client_id": settings.PAYPAL_CLIENT_ID,
        "mode": settings.PAYPAL_MODE,
        "exchange_rate": settings.PAYPAL_EXCHANGE_RATE,
        "currency": "USD"
    }


@router.post(
    "/paypal/create-order",
    response_model=PayPalCreateOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear orden de pago en PayPal"
)
def create_paypal_order(
    data: PayPalCreateOrderRequest,
    current_user: User = Depends(get_current_user)
):
    """Genera una orden de cobro en la API de PayPal v2 y devuelve el order_id."""
    order_data = PayPalService.create_order(
        monto_bob=data.monto_bob,
        items=data.items,
        sucursal_id=str(data.sucursal_id) if data.sucursal_id else None,
        reserva_id=str(data.reserva_id) if data.reserva_id else None,
        descripcion=data.descripcion or "Compra en FICCT STORE"
    )
    return PayPalCreateOrderResponse(**order_data)


@router.post(
    "/paypal/capture-order",
    response_model=PayPalCaptureOrderResponse,
    summary="Capturar orden de PayPal y registrar la venta oficial"
)
def capture_paypal_order(
    data: PayPalCaptureOrderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Captura los fondos en PayPal tras la aprobación del cliente.
    Si la captura es exitosa, registra la venta en la BD, descuenta el stock
    y emite el recibo de venta oficial.
    """
    # 1. Capturar orden en PayPal REST API
    capture_res = PayPalService.capture_order(data.order_id)
    
    if capture_res.get("status") != "COMPLETED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La orden de PayPal no fue completada. Estado: {capture_res.get('status')}"
        )

    capture_id = capture_res.get("capture_id")
    monto_usd = capture_res.get("monto_usd", 0.0)

    # 2. Transformar detalles a items de venta
    sale_items = []
    for d in data.detalles:
        sale_items.append(SaleItemCreate(
            variante_id=d.variante_id,
            cantidad=d.cantidad,
            precio_unitario=d.precio_unitario,
            reserva_id=data.reserva_id
        ))

    # 3. Construir payload de venta presencial con método PAYPAL
    sale_payload = PresencialSaleCreate(
        sucursal_id=data.sucursal_id,
        cliente_id=data.cliente_id,
        items=sale_items,
        pago=PaymentInfo(
            metodo=PaymentMethod.PAYPAL,
            monto_recibido=None,
            referencia=f"PAYPAL_{capture_id}"
        ),
        nota=data.nota or f"Venta pagada vía PayPal Sandbox (Capture ID: {capture_id}, USD {monto_usd:.2f})"
    )

    # 4. Registrar venta en base de datos
    sale_dict = SaleService.create_presencial_sale(
        db=db,
        data=sale_payload,
        cajero_id=current_user.id
    )

    return PayPalCaptureOrderResponse(
        venta=SaleResponse(**sale_dict),
        paypal_order_id=data.order_id,
        paypal_capture_id=capture_id,
        paypal_status=capture_res.get("status"),
        monto_usd=monto_usd
    )
