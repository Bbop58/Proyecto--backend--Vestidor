"""Authenticated secret storage bound to a company and immutable credential revision."""
import base64
import json
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from app.config import settings


def _key():
    try:
        value = settings.PAYMENT_CREDENTIALS_KEY
        raw = value.get_secret_value().strip() if value else ''
        if not raw or raw in (settings.WALLET_SIGNING_KEY, settings.SECRET_KEY):
            raise ValueError()
        key = base64.b64decode(raw + '=' * (-len(raw) % 4), altchars=b'-_', validate=True)
        if len(key) != 32: raise ValueError()
        return key
    except Exception:
        raise HTTPException(503, 'La protección de credenciales de pago no está disponible') from None


def _aad(tenant_id, identifier, revision, client_id, mode):
    return json.dumps([str(tenant_id), str(identifier), revision, client_id, mode], separators=(',', ':')).encode()


def encrypt_secret(secret, tenant_id, identifier, revision, client_id, mode):
    nonce = os.urandom(12)
    payload = AESGCM(_key()).encrypt(nonce, secret.encode(), _aad(tenant_id, identifier, revision, client_id, mode))
    return 'PP2.' + base64.urlsafe_b64encode(nonce + payload).decode()


def decrypt_secret(value, tenant_id, identifier, revision, client_id, mode):
    key = _key()
    try:
        if not value.startswith('PP2.'): raise ValueError()
        data = base64.b64decode(value[4:], altchars=b'-_', validate=True)
        return AESGCM(key).decrypt(data[:12], data[12:], _aad(tenant_id, identifier, revision, client_id, mode)).decode()
    except Exception:
        raise HTTPException(503, 'No se puede recuperar la credencial de pago; requiere revisión') from None
