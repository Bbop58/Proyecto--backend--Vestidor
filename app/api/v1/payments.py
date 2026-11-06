import uuid
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.api.deps import get_current_user, get_history_user
from app.models.user import User
from app.models.tenant import Tenant
from app.models.branch import Branch
from app.models.sale import Sale, PaymentMethod

logger = logging.getLogger(__name__)
from app.schemas.sale import PresencialSaleCreate, PaymentInfo, SaleItemCreate, SaleResponse
from app.schemas.paypal import (
    PayPalCreateOrderRequest,
    PayPalCreateOrderResponse,
    PayPalCaptureOrderRequest,
    PayPalCaptureOrderResponse,
)
from app.services.paypal_service import PayPalService
from app.services.sale_service import SaleService
from app.services.role_service import RoleService
from app.services.audit_service import AuditService

router = APIRouter(prefix="/payments", tags=["Pagos y Pasarelas"])


def _resolve_tenant_paypal(db: Session, sucursal_id: Optional[uuid.UUID] = None, user: Optional[User] = None, tenant_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    if user is not None:
        account_tenant = SaleService._actor_tenant(db, user.id)
        if tenant_id and tenant_id != account_tenant:
            raise HTTPException(status_code=404, detail="Empresa no disponible")
        if sucursal_id and not db.query(Branch.id).filter_by(id=sucursal_id,tenant_id=account_tenant,activa=True).first():
            raise HTTPException(status_code=404, detail="Sucursal no disponible")
        tenant_id = account_tenant
    # Internal callers derive tenant_id from the authenticated database user.
    # Never substitute the platform account for an unknown/unconfigured business.
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Identifica la empresa autenticada")
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant or tenant.es_operadora:
        raise HTTPException(status_code=404, detail="Empresa no disponible")
    if sucursal_id and not db.query(Branch.id).filter_by(
            id=sucursal_id,tenant_id=tenant.id,activa=True).first():
        raise HTTPException(status_code=404, detail="Sucursal no disponible")
    from app.services.company_paypal_service import CompanyPayPalService
    return CompanyPayPalService.resolve(db,tenant.id)



@router.get(
    "/paypal/config",
    summary="Obtener configuración SDK de PayPal de la empresa autenticada"
)
def get_paypal_config(
    tenant_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Only non-secret SDK fields for the authenticated company."""
    creds = _resolve_tenant_paypal(db=db, tenant_id=tenant_id, user=user)
    return {
        "client_id": creds["client_id"],
        "mode": creds["mode"],
        "exchange_rate": settings.PAYPAL_EXCHANGE_RATE,
        "currency": "USD",
        "brand_name": creds["brand_name"]
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

        <button class="btn-action" onclick="closeWindow();">Volver a la App / Cerrar Ventana</button>
        <p style="font-size: 11px; color: #94a3b8; margin-top: 12px; margin-bottom: 0;">
            Si la pestaña no se cierra automáticamente por políticas de tu navegador, simplemente pulsa 'Atrás' o cambia a la <strong>App FICCT STORE</strong>.
        </p>
    </div>
    <script>
        function closeWindow() {
            window.close();
            if (window.history.length > 1) {
                window.history.back();
            }
            setTimeout(function() {
                try { window.location.href = "about:blank"; } catch(e) {}
            }, 300);
        }
    </script>
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
        <p>La autorización se canceló. Regresa a la aplicación y consulta el estado de la orden antes de volver a intentar el pago.</p>
        <button class="btn-action" onclick="closeWindow();">Volver a la App / Cerrar Ventana</button>
        <p style="font-size: 11px; color: #94a3b8; margin-top: 12px; margin-bottom: 0;">
            Si la pestaña no se cierra automáticamente por políticas de tu navegador, simplemente pulsa 'Atrás' o cambia a la <strong>App FICCT STORE</strong>.
        </p>
    </div>
    <script>
        function closeWindow() {
            window.close();
            if (window.history.length > 1) {
                window.history.back();
            }
            setTimeout(function() {
                try { window.location.href = "about:blank"; } catch(e) {}
            }, 300);
        }
    </script>
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    from app.services.payment_flow_service import PaymentFlowService
    return PaymentFlowService.create(db, current_user, data)


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
    from app.services.payment_flow_service import PaymentFlowService
    return PaymentFlowService.capture(db, current_user, data.order_id, supplied=data)


@router.get('/paypal/orders/{order_id}/status', summary='Consultar estado conciliado de una orden propia')
def paypal_order_status(order_id: str, db: Session = Depends(get_db), current_user=Depends(get_history_user)):
    from app.models.payment_intent import PaymentIntent
    row = db.query(PaymentIntent).filter_by(provider_order_id=order_id, tenant_id=current_user.tenant_id,
                                          user_id=current_user.id).first()
    if not row:
        raise HTTPException(404, 'Orden no disponible')
    return {'order_id': row.provider_order_id, 'state': row.state, 'purpose': row.purpose,
            'monto_bob': row.amount_bob, 'monto_usd': row.amount_usd, 'refund_id': row.refund_id}


@router.post('/paypal/orders/{order_id}/resume', summary='Conciliar una orden propia previamente iniciada')
def resume_paypal_order(order_id: str, db: Session = Depends(get_db), current_user=Depends(get_history_user)):
    from app.models.payment_intent import PaymentIntent
    from app.services.payment_flow_service import PaymentFlowService
    row = db.query(PaymentIntent).filter_by(provider_order_id=order_id,
        tenant_id=current_user.tenant_id, user_id=current_user.id).first()
    if not row: raise HTTPException(404, 'Orden no disponible')
    return PaymentFlowService.capture(db, current_user, order_id, purpose=row.purpose)


@router.get('/paypal/availability')
def paypal_availability(db: Session=Depends(get_db), user: User=Depends(get_current_user)):
    from app.services.company_paypal_service import CompanyPayPalService
    return CompanyPayPalService.availability(db,user)
