"""Explicit ledger reconciliation before signing legacy wallets with a private key.

Dry-run by default. An operator must select the database explicitly through
WALLET_RECONCILIATION_DATABASE_URL and review the report before using --apply.
Never imports a database URL from the normal application configuration.
"""
import argparse
import os
import sys
from pathlib import Path
from decimal import Decimal
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    url = os.environ.get('WALLET_RECONCILIATION_DATABASE_URL')
    if not url:
        raise SystemExit('Select an explicit reconciliation database URL; no data changed')
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import Session
    from app.models.user_wallet import UserWallet
    from app.models.wallet_transaction import WalletTransaction
    from app.models.payment_intent import PaymentIntent
    from app.models.sale import Sale
    from app.services.wallet_service import WalletService
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            wallets = db.query(UserWallet).order_by(UserWallet.id).with_for_update().all()
            invalid = 0
            for wallet in wallets:
                transactions = db.query(WalletTransaction).filter_by(wallet_id=wallet.id).order_by(
                    WalletTransaction.created_at, WalletTransaction.id).all()
                balance = Decimal('0.00')
                for tx in transactions:
                    if (tx.tenant_id != wallet.tenant_id or tx.saldo_anterior != balance
                        or tx.saldo_posterior != tx.saldo_anterior+tx.monto):
                        invalid += 1
                    # The old endpoint could credit failed PayPal captures. A
                    # matching arithmetic ledger alone cannot certify those funds.
                    if tx.tipo == 'RECARGA_PAYPAL':
                        intent = db.get(PaymentIntent, tx.payment_intent_id) if tx.payment_intent_id else None
                        if (not intent or intent.state != 'APPLIED' or intent.purpose != 'WALLET'
                            or intent.user_id != wallet.user_id or intent.tenant_id != wallet.tenant_id
                            or intent.amount_bob != tx.monto or not intent.capture_id):
                            invalid += 1
                    if tx.tipo in ('PAGO_ORDEN_OFFLINE', 'PAGO_ORDEN_ONLINE'):
                        sale = db.query(Sale).filter_by(numero_recibo=tx.referencia_externa,
                                                       tenant_id=wallet.tenant_id).first()
                        if (not sale or not (tx.metadatos or {}).get('request_hash')
                            or sale.cliente_id != wallet.user_id or sale.monto_total != -tx.monto
                            or sum(d.subtotal for d in sale.detalles)-sale.descuento+(sale.costo_envio or 0) != sale.monto_total):
                            invalid += 1
                    balance = tx.saldo_posterior
                if (balance != wallet.saldo_actual or wallet.nonce_secuencia != len(transactions)
                    or wallet.user.tenant_id != wallet.tenant_id or wallet.saldo_retenido<0
                    or wallet.saldo_retenido>wallet.saldo_actual or wallet.moneda!='BOB'):
                    invalid += 1
                if args.apply:
                    WalletService._seal(wallet)
            if invalid:
                db.rollback()
                raise SystemExit(f'Rejected: {invalid} reconciliation discrepancies; no signatures changed')
            if args.apply:
                db.commit()
            else:
                db.rollback()
            print(f'Wallets checked: {len(wallets)}; discrepancies: 0; applied: {args.apply}')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
