"""Private demo provisioning, without provider calls or resetting owner edits."""
from sqlalchemy import select
from app.models.tenant import Tenant
from app.services.company_paypal_service import CompanyPayPalService as Service
from app.core.payment_credentials_crypto import _key


def validate_seed_config(value):
    if value is None: return {}
    if not isinstance(value, dict): raise ValueError('Invalid private payment configuration')
    for slug, item in value.items():
        if (not isinstance(slug, str) or not slug or not isinstance(item, dict)
                or set(item) != {'client_id', 'client_secret', 'mode'} or item['mode'] != 'sandbox'):
            raise ValueError('Demo payment configuration must contain sandbox merchant pairs')
        Service._validate(item['client_id'], item['client_secret'], item['mode'])
    if value: _key()  # Validate protection before any destructive demo DDL.
    return value


def seed_company_paypal(connection, config):
    for slug, item in validate_seed_config(config).items():
        tenant = connection.execute(select(Tenant.id).where(Tenant.slug == slug, Tenant.es_operadora.is_(False))).first()
        if not tenant: raise ValueError('Configured demo company is unavailable')
        if Service.legacy_required(connection,tenant.id):
            raise ValueError('Migrate legacy payment configuration before demo provisioning')
        previous = Service.latest(connection, tenant.id)
        if previous:
            # Even matching input must not rotate ciphertext or verification dates.
            Service.verify_history(connection,tenant.id)
            continue  # Owner edits and explicit disabling always win over seed defaults.
        Service.append(connection, tenant.id, client_id=item['client_id'], secret=item['client_secret'], mode=item['mode'])
