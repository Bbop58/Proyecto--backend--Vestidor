import uuid
from datetime import datetime
from typing import List, Optional, Any, Dict
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user, require_permission
from app.models.user import User
from app.schemas.wallet import (
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

router = APIRouter(prefix="/wallet", tags=["Billetera Virtual / Fondos Offline"])


@router.get(
    "/me",
    response_model=WalletResponse,
    summary="Obtener la billetera, saldo y firma criptográfica del usuario actual"
)
def get_my_wallet(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    tenant_id = current_user.tenant_id
    if not tenant_id:
        from app.models.tenant import Tenant
        first_tenant = db.query(Tenant).first()
        tenant_id = first_tenant.id if first_tenant else uuid.uuid4()

    wallet = WalletService.get_or_create_wallet(db, current_user.id, tenant_id)
    return wallet


@router.get(
    "/history",
    response_model=List[WalletTransactionResponse],
    summary="Obtener el extracto y movimientos contables de la billetera"
)
def get_my_wallet_history(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    tenant_id = current_user.tenant_id
    if not tenant_id:
        from app.models.tenant import Tenant
        first_tenant = db.query(Tenant).first()
        tenant_id = first_tenant.id if first_tenant else uuid.uuid4()

    wallet = WalletService.get_or_create_wallet(db, current_user.id, tenant_id)
    return WalletService.get_wallet_history(db, wallet.id, limit=limit)


@router.post(
    "/topup/paypal",
    summary="Acreditar fondos a la billetera mediante pago o crear orden en PayPal"
)
def topup_with_paypal(
    data: PayPalTopupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    tenant_id = current_user.tenant_id
    if not tenant_id:
        from app.models.tenant import Tenant
        first_tenant = db.query(Tenant).first()
        tenant_id = first_tenant.id if first_tenant else uuid.uuid4()

    monto_bob = data.monto_bob or data.monto_bs

    # 1. Si no hay order_id pero hay monto, generar la orden en PayPal Sandbox
    if not data.order_id and monto_bob:
        order_res = PayPalService.create_order(
            monto_bob=monto_bob,
            descripcion="Recarga de Billetera Virtual FICCT",
            return_url=None,
            cancel_url=None
        )
        return {
            "order_id": order_res["order_id"],
            "approve_url": order_res["approve_url"],
            "monto_usd": order_res["monto_usd"],
            "monto_bob": monto_bob
        }

    # 2. Si ya viene el order_id, capturar y acreditar el saldo
    if data.order_id:
        return WalletService.topup_paypal(
            db=db,
            user_id=current_user.id,
            tenant_id=tenant_id,
            order_id=data.order_id,
            monto_usd=data.monto_usd or ((monto_bob or 0.0) / 6.96),
            monto_bs=monto_bob
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Debes proporcionar un order_id o un monto en Bolivianos (monto_bob)"
    )


@router.post(
    "/sync-offline-orders",
    response_model=BatchOfflineSyncResponse,
    summary="Sincronizar lote de órdenes de compra realizadas offline con saldo de billetera"
)
def sync_offline_wallet_orders(
    data: BatchOfflineSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    tenant_id = current_user.tenant_id
    if not tenant_id:
        from app.models.tenant import Tenant
        first_tenant = db.query(Tenant).first()
        tenant_id = first_tenant.id if first_tenant else uuid.uuid4()

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
    return q.order_by(UserWallet.saldo_actual.desc()).limit(limit).all()


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
        notas=data.notas
    )

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    comp_num = f"REC-{datetime.now().strftime('%Y%m%d%H%M%S')}"

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


