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
from app.services.audit_service import AuditService

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


from fastapi.responses import HTMLResponse

@router.get(
    "/paypal/return",
    response_class=HTMLResponse,
    summary="Página de retorno tras autorizar pago en PayPal"
)
def paypal_return_page():
    """Página HTML amigable que se muestra en el navegador cuando el cliente aprueba el pago en PayPal."""
    html_content = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pago Autorizado - FICCT STORE</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
            color: #f8fafc;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .card {
            background: rgba(30, 41, 59, 0.85);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 20px;
            padding: 32px 24px;
            max-width: 440px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
        }
        .icon-circle {
            width: 76px;
            height: 76px;
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 20px;
            box-shadow: 0 10px 20px rgba(16, 185, 129, 0.3);
            font-size: 38px;
            color: white;
        }
        h1 { font-size: 22px; font-weight: 700; margin-bottom: 12px; color: #ffffff; }
        p { font-size: 14px; line-height: 1.6; color: #94a3b8; margin-bottom: 24px; }
        .badge {
            display: inline-block;
            background: rgba(0, 112, 186, 0.15);
            color: #38bdf8;
            border: 1px solid rgba(56, 189, 248, 0.3);
            padding: 6px 14px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 600;
            margin-bottom: 16px;
        }
        .instruction-box {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 12px;
            padding: 16px;
            text-align: left;
            margin-bottom: 24px;
        }
        .instruction-step {
            display: flex;
            align-items: flex-start;
            margin-bottom: 10px;
            font-size: 13px;
            color: #cbd5e1;
        }
        .instruction-step:last-child { margin-bottom: 0; }
        .step-num {
            background: #6366f1;
            color: white;
            width: 20px;
            height: 20px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            font-weight: bold;
            margin-right: 10px;
            flex-shrink: 0;
        }
        .btn-action {
            display: block;
            width: 100%;
            background: linear-gradient(135deg, #0070ba 0%, #003087 100%);
            color: #ffffff;
            text-decoration: none;
            padding: 14px;
            border-radius: 12px;
            font-weight: 600;
            font-size: 15px;
            border: none;
            cursor: pointer;
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }
        .btn-action:active { transform: scale(0.98); }
    </style>
</head>
<body>
    <div class="card">
        <div class="badge">PayPal Sandbox Verificado</div>
        <div class="icon-circle">✓</div>
        <h1>¡Pago Autorizado con Éxito!</h1>
        <p>Tu autorización en PayPal Sandbox se completó satisfactoriamente.</p>
        
        <div class="instruction-box">
            <div class="instruction-step">
                <span class="step-num">1</span>
                <span>Cierra esta pestaña o cambia a la <strong>App FICCT STORE</strong>.</span>
            </div>
            <div class="instruction-step">
                <span class="step-num">2</span>
                <span>Toca el botón <strong>"Confirmar Pago Aprobado"</strong> en tu celular para generar tu recibo oficial.</span>
            </div>
        </div>

        <button class="btn-action" onclick="window.close();">Cerrar Ventana</button>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


@router.get(
    "/paypal/cancel",
    response_class=HTMLResponse,
    summary="Página de cancelación de pago en PayPal"
)
def paypal_cancel_page():
    """Página HTML que se muestra si el usuario cancela la autorización en PayPal."""
    html_content = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pago Cancelado - FICCT STORE</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
            color: #f8fafc;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .card {
            background: rgba(30, 41, 59, 0.85);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 20px;
            padding: 32px 24px;
            max-width: 440px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
        }
        .icon-circle {
            width: 76px;
            height: 76px;
            background: linear-gradient(135deg, #ef4444 0%, #b91c1c 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 20px;
            box-shadow: 0 10px 20px rgba(239, 68, 68, 0.3);
            font-size: 38px;
            color: white;
        }
        h1 { font-size: 22px; font-weight: 700; margin-bottom: 12px; color: #ffffff; }
        p { font-size: 14px; line-height: 1.6; color: #94a3b8; margin-bottom: 24px; }
        .btn-action {
            display: block;
            width: 100%;
            background: #334155;
            color: #ffffff;
            text-decoration: none;
            padding: 14px;
            border-radius: 12px;
            font-weight: 600;
            font-size: 15px;
            border: none;
            cursor: pointer;
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon-circle">✕</div>
        <h1>Operación Cancelada</h1>
        <p>No se realizó ningún cargo en tu cuenta de PayPal. Puedes cerrar esta ventana y regresar a la app FICCT STORE para intentar nuevamente.</p>
        <button class="btn-action" onclick="window.close();">Cerrar Ventana</button>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


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
        descripcion=data.descripcion or "Compra en FICCT STORE",
        return_url=data.return_url,
        cancel_url=data.cancel_url
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

    # 4.1 Registrar pago en bitácora
    recibo_num = sale_dict.get("numero_recibo") or sale_dict.get("numero_comprobante") or "N/A"
    AuditService.log(
        db=db,
        action=f"Pago PayPal confirmado (${monto_usd:.2f} USD) - Comprobante #{recibo_num}",
        module="Pagos",
        user=current_user
    )

    # 5. Notificar al cliente sobre la compra digital exitosa
    try:
        from app.services.notification_service import NotificationService
        target_user_id = data.cliente_id or current_user.id
        comprobante = sale_dict.get("numero_recibo") or sale_dict.get("numero_comprobante", "N/A")
        total_bob = float(sale_dict.get("monto_total") or sale_dict.get("total", 0.0))
        NotificationService.create(
            db=db,
            user_id=target_user_id,
            title="¡Compra Confirmada! 💳",
            message=f"Tu pago de Bs {total_bob:.2f} fue procesado con éxito. Se emitió tu comprobante #{comprobante}.",
            type="PURCHASE_SUCCESS",
            reference_id=str(sale_dict.get("id", ""))
        )
    except Exception:
        pass

    return PayPalCaptureOrderResponse(
        venta=SaleResponse(**sale_dict),
        paypal_order_id=data.order_id,
        paypal_capture_id=capture_id,
        paypal_status=capture_res.get("status"),
        monto_usd=monto_usd
    )
