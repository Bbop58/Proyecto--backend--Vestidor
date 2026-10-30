import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user, require_permission
from app.models.user import User
from app.schemas.wallet import (
    WalletResponse,
    WalletTransactionResponse,
    PayPalTopupRequest,
    AdminCashTopupRequest,
    BatchOfflineSyncRequest,
    BatchOfflineSyncResponse,
)
from app.services.wallet_service import WalletService

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
    response_model=WalletResponse,
    summary="Acreditar fondos a la billetera mediante pago aprobado en PayPal"
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

    return WalletService.topup_paypal(
        db=db,
        user_id=current_user.id,
        tenant_id=tenant_id,
        order_id=data.order_id,
        monto_usd=data.monto_usd,
        monto_bs=data.monto_bs
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
    response_model=WalletResponse,
    dependencies=[Depends(require_permission("ventas.create"))],
    summary="Recarga manual de saldo en efectivo desde mostrador por cajero"
)
def admin_topup_cash(
    data: AdminCashTopupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return WalletService.topup_cash_admin(
        db=db,
        admin_user=current_user,
        cliente_id=data.cliente_id,
        monto_bs=data.monto_bs,
        notas=data.notas
    )

