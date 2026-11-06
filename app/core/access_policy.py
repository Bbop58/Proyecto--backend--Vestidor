"""A server UUID grant is necessary, but never grants commercial access."""
from enum import Enum

PLATFORM_ROLE = 'superadmin_plataforma'


class AccessScope(str, Enum):
    PLATFORM = 'PLATFORM'
    COMPANY = 'COMPANY'
    DENIED = 'DENIED'


def is_platform_candidate(user):
    from app.config import settings
    return bool(user and (
        str(user.id) in settings.PLATFORM_ADMIN_USER_IDS
        or getattr(getattr(user, 'role', None), 'name', None) == PLATFORM_ROLE
        or getattr(getattr(user, 'tenant', None), 'es_operadora', False)))


def resolve_access_scope(user):
    from app.config import settings
    company = getattr(user, 'tenant', None)
    role = getattr(user, 'role', None)
    if not user or not user.is_active or not company:
        return AccessScope.DENIED
    if is_platform_candidate(user):
        valid = (company.activo and str(user.id) in settings.PLATFORM_ADMIN_USER_IDS
                 and company.es_operadora and role and role.name == PLATFORM_ROLE
                 and role.tenant_id == company.id)
        return AccessScope.PLATFORM if valid else AccessScope.DENIED
    return AccessScope.COMPANY
