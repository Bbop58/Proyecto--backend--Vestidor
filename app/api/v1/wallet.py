import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Any, Dict
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.api.deps import get_current_user, require_permission, get_history_user
from app.models.user import User
from app.models.tenant import Tenant
from app.schemas.wallet import (
    OfflineOrderPayload,
    WalletResponse,
    WalletTransactionResponse,
    PayPalTopupRequest,
    AdminCashTopupRequest,
    AdminTopupCashResponse,
    BatchOfflineSyncRequest,
    BatchOfflineSyncResponse,
)
from app.services.wallet_service import WalletService
from app.services.paypal_service import PayPalService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wallet", tags=["Billetera Virtual / Fondos Offline"])


@router.post('/pay',response_model=BatchOfflineSyncResponse)
def pay_online(order: OfflineOrderPayload,db: Session=Depends(get_db),current_user: User=Depends(get_current_user)):
    # Explicit online checkout needs a current period, not the offline benefit.
    return WalletService.sync_offline_orders_batch(db,current_user.id,current_user.tenant_id,[order],_online=True)


@router.get(
    "/me",
    response_model=WalletResponse,
    summary="Obtener la billetera, saldo y firma criptográfica del usuario actual"
)
def get_my_wallet(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_history_user)
):
    try:
        tenant_id = current_user.tenant_id
        wallet = WalletService.get_or_create_wallet(db, current_user.id, tenant_id)
        return WalletResponse.model_validate(wallet)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en get_my_wallet: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo consultar la billetera"
        )


@router.get(
    "/history",
    response_model=List[WalletTransactionResponse],
    summary="Obtener el extracto y movimientos contables de la billetera"
)
def get_my_wallet_history(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_history_user)
):
    try:
        tenant_id = current_user.tenant_id

        wallet = WalletService.get_or_create_wallet(db, current_user.id, tenant_id)
        history = WalletService.get_wallet_history(db, wallet.id, limit=limit)
        return [WalletTransactionResponse.model_validate(t) for t in history]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en get_my_wallet_history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo consultar el historial"
        )


@router.post(
    "/topup/paypal",
    summary="Acreditar fondos a la billetera mediante pago o crear orden en PayPal"
)
def topup_with_paypal(
    data: PayPalTopupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    from app.services.payment_flow_service import PaymentFlowService
    if data.order_id:
        wallet = PaymentFlowService.capture(db, current_user, data.order_id, purpose='WALLET')
        return WalletResponse.model_validate(wallet)
    return PaymentFlowService.create(db, current_user, data, purpose='WALLET')


@router.post(
    "/sync-offline-orders",
    response_model=BatchOfflineSyncResponse,
    summary="Sincronizar lote de órdenes de compra realizadas offline con saldo de billetera"
)
def sync_offline_wallet_orders(
    data: BatchOfflineSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_history_user)
):
    tenant_id = current_user.tenant_id

    res = WalletService.sync_offline_orders_batch(
        db=db,
        user_id=current_user.id,
        tenant_id=tenant_id,
        orders=data.orders
    )
    return res


# ── ENDPOINTS ADMINISTRATIVOS (WEB) ──────────────────────────────────────────

@router.get(
    "/admin/list",
    response_model=List[WalletResponse],
    dependencies=[Depends(require_permission("ventas.list"))],
    summary="Listar billeteras y saldos de clientes para auditoría contable"
)
def list_client_wallets(
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    from app.models.user_wallet import UserWallet
    q = db.query(UserWallet)
    if current_user.tenant_id:
        q = q.filter(UserWallet.tenant_id == current_user.tenant_id)
    wallets = q.order_by(UserWallet.saldo_actual.desc()).limit(limit).all()
    return [WalletResponse.model_validate(w) for w in wallets]


@router.post(
    "/admin/topup-cash",
    response_model=AdminTopupCashResponse,
    dependencies=[Depends(require_permission("ventas.create"))],
    summary="Recarga manual de saldo en efectivo desde mostrador por cajero"
)
def admin_topup_cash(
    data: AdminCashTopupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    cliente = None
    if data.cliente_id:
        cliente = db.query(User).filter(User.id == data.cliente_id).first()
    elif data.user_email:
        cliente = db.query(User).filter(User.email == data.user_email.strip()).first()

    if not cliente:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró ningún usuario con el correo o ID especificado"
        )

    if not data.client_operation_id:
        raise HTTPException(400, "La recarga requiere una clave de operación para evitar duplicados")
    monto = data.monto or data.monto_bs or 0.0
    if monto <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El monto debe ser un valor mayor a cero Bolivianos"
        )

    wallet = WalletService.topup_cash_admin(
        db=db,
        admin_user=current_user,
        cliente_id=cliente.id,
        monto_bs=monto,
        notas=data.notas,
        client_operation_id=data.client_operation_id
    )

    from app.models.wallet_transaction import WalletTransaction
    transaction = db.query(WalletTransaction).filter_by(client_operation_id=data.client_operation_id,
                                                        wallet_id=wallet.id).one()
    monto = float(transaction.monto)
    now_str = transaction.created_at.astimezone(timezone(timedelta(hours=-4))).strftime("%d/%m/%Y %H:%M:%S")
    comp_num = f"REC-{data.client_operation_id}"

    return {
        "success": True,
        "message": "Recarga de billetera en efectivo procesada exitosamente",
        "user_email": cliente.email,
        "nuevo_saldo": float(wallet.saldo_actual),
        "recibo_caja": {
            "numero_comprobante": comp_num,
            "fecha": now_str,
            "cajero": current_user.email,
            "monto": float(monto),
            "moneda": "BOB",
            "metodo": "EFECTIVO"
        }
    }


