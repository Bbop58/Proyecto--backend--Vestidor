"""Short access JWTs backed by durable, independently revocable mobile sessions."""
import hashlib
import hmac
import re
import secrets
import uuid
from functools import wraps
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from app.models.mobile_session import MobileSession, utc_now
from app.models.user import User
from app.core.security import create_access_token
from app.schemas.mobile_session import MobileRefreshResponse, MobileSessionResponse

TOKEN = re.compile(r'^ms_([a-f0-9]{32})\.[A-Za-z0-9_-]{43}$')


def fail(code, status=401):
    return HTTPException(status, detail={'code': code, 'message': {
        'MOBILE_SESSION_INVALID': 'La sesión móvil no es válida. Vuelve a ingresar.',
        'MOBILE_SESSION_REVOKED': 'La sesión móvil fue revocada. Vuelve a ingresar.',
        'MOBILE_IDENTITY_CHANGED': 'La identidad de la sesión móvil cambió. Vuelve a ingresar.',
        'MOBILE_ACCOUNT_INACTIVE': 'La cuenta no está activa.',
        'MOBILE_SESSION_UNAVAILABLE': 'No se pudo comprobar la sesión. Vuelve a intentarlo.',
        'MOBILE_SERVICE_RESTRICTED': 'La empresa tiene el acceso restringido.',
    }.get(code, 'Solicitud de sesión móvil no válida.')})


def guarded(function):
    @wraps(function)
    def run(db, *args, **kwargs):
        try:
            return function(db, *args, **kwargs)
        except SQLAlchemyError:
            db.rollback()
            raise fail('MOBILE_SESSION_UNAVAILABLE', 503) from None
    return run


def _user(db, identifier):
    return db.execute(select(User).where(User.id == identifier)
                      .execution_options(populate_existing=True)).scalar_one_or_none()


def _identity_problem(user, company):
    if not user or user.tenant_id != company or not user.tenant:
        return fail('MOBILE_IDENTITY_CHANGED')
    if not user.is_active:
        return fail('MOBILE_ACCOUNT_INACTIVE', 403)
    from app.core.access_policy import resolve_access_scope, AccessScope
    if resolve_access_scope(user) != AccessScope.COMPANY:
        return fail('MOBILE_IDENTITY_CHANGED')
    return None


def _check_contract(user):
    from app.services.entitlement_service import EntitlementService
    if not user.tenant.activo and not EntitlementService.can_recover(user):
        # Contract restrictions must not erase an otherwise valid identity.
        raise fail('MOBILE_SERVICE_RESTRICTED', 403)


def _lookup(db, credential):
    match = TOKEN.fullmatch(credential)
    if not match:
        raise fail('MOBILE_CREDENTIAL_MALFORMED', 400)
    row = db.execute(select(MobileSession).where(MobileSession.id == uuid.UUID(hex=match[1]))
                     .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if row and hmac.compare_digest(row.credential_hash, hashlib.sha256(credential.encode()).hexdigest()):
        return row
    return None


def _response(db, row, user):
    from app.services.user_service import UserService
    return dict(session_id=row.id,
                access_token=create_access_token(str(user.id), tenant_id=row.tenant_id,
                                                 mobile_session_id=row.id)['token'],
                user=UserService.build_user_response(db, user))


class MobileSessionService:
    @staticmethod
    def lock_user(db, identifier):
        db.execute(select(User.id).where(User.id == identifier).with_for_update(of=User, key_share=True))

    @staticmethod
    @guarded
    def issue(db, user):
        # Serialize credential creation with password/account changes, avoiding
        # FOR UPDATE on User's nullable joined relationships.
        MobileSessionService.lock_user(db, user.id)
        user = _user(db, user.id)
        if not user or not user.tenant_id:
            raise fail('MOBILE_IDENTITY_CHANGED')
        problem = _identity_problem(user, user.tenant_id)
        if problem:
            raise HTTPException(403, 'La plataforma no tiene sesión móvil comercial') if user.is_platform_admin else problem
        _check_contract(user)
        identifier = uuid.uuid4()
        credential = 'ms_' + identifier.hex + '.' + secrets.token_urlsafe(32)
        row = MobileSession(id=identifier, tenant_id=user.tenant_id, user_id=user.id,
                            credential_hash=hashlib.sha256(credential.encode()).hexdigest())
        db.add(row)
        db.flush()
        response = MobileSessionResponse(**_response(db, row, user), session_token=credential)
        db.commit()
        return response

    @staticmethod
    @guarded
    def refresh(db, session_token):
        row = _lookup(db, session_token)
        if row is None:
            raise fail('MOBILE_SESSION_INVALID')
        if row.revoked_at is not None:
            raise fail('MOBILE_SESSION_REVOKED')
        user = _user(db, row.user_id)
        problem = _identity_problem(user, row.tenant_id)
        if problem:
            row.revoked_at = utc_now()
            db.commit()
            raise problem
        _check_contract(user)
        row.last_used_at = utc_now()
        result = MobileRefreshResponse(**_response(db, row, user))
        db.commit()
        return result

    @staticmethod
    @guarded
    def revoke(db, session_token):
        row = _lookup(db, session_token)
        if row is not None and row.revoked_at is None:
            row.revoked_at = utc_now()
        db.commit()

    @staticmethod
    @guarded
    def validate_access(db, payload):
        if 'mobile_session_id' not in payload:
            return
        try:
            identifier = uuid.UUID(str(payload['mobile_session_id']))
        except (ValueError, TypeError, AttributeError):
            raise fail('MOBILE_SESSION_INVALID') from None
        row = db.execute(select(MobileSession).where(MobileSession.id == identifier)
                         .execution_options(populate_existing=True)).scalar_one_or_none()
        if row is None:
            raise fail('MOBILE_SESSION_INVALID')
        # A mismatched signed claim must not revoke somebody else's session.
        if str(row.user_id) != payload.get('sub') or str(row.tenant_id) != payload.get('tenant_id'):
            raise fail('MOBILE_IDENTITY_CHANGED')
        if row.revoked_at is not None:
            raise fail('MOBILE_SESSION_REVOKED')
        problem = _identity_problem(_user(db, row.user_id), row.tenant_id)
        if problem:
            # This runs at the authentication boundary before business writes.
            row.revoked_at = utc_now()
            db.commit()
            raise problem

    @staticmethod
    def revoke_user(db, identifier):
        if db.info.get('platform_actor_id'):
            # Platform accounts cannot own commercial mobile sessions.
            return
        table = MobileSession.__table__
        condition = (table.c.user_id == identifier)
        if db.info.get('tenant_scope'):
            condition &= table.c.tenant_id == db.info['tenant_scope']
        db.connection().execute(update(table).where(condition, table.c.revoked_at.is_(None)).values(revoked_at=utc_now()))

    @staticmethod
    def revoke_company(db, identifier):
        if db.info.get('tenant_scope') and db.info['tenant_scope'] != identifier:
            raise HTTPException(403, 'No se puede revocar otra empresa')
        table = MobileSession.__table__
        db.connection().execute(update(table).where(table.c.tenant_id == identifier,
                               table.c.revoked_at.is_(None)).values(revoked_at=utc_now()))
