import time
import xmlrpc.client

from settings import settings

_odoo_uid: int | None = None
_odoo_last_auth: float = 0.0
_AUTH_TTL: float = 3600.0

def get_models() -> tuple[xmlrpc.client.ServerProxy, int]:
    global _odoo_uid, _odoo_last_auth

    if _odoo_uid is None or time.time() - _odoo_last_auth > _AUTH_TTL:
        common = xmlrpc.client.ServerProxy(f"{settings.odoo_url}/xmlrpc/2/common")
        _odoo_uid = common.authenticate(settings.odoo_db, settings.odoo_user, settings.odoo_api_key, {})

        if not _odoo_uid:
            raise RuntimeError("Odoo Authentication Failed, check ODOO_USERNAME and ODOO_API_KEY")

        _odoo_last_auth = time.time()

    # Always create a fresh ServerProxy — xmlrpc HTTPConnection is not thread-safe
    models = xmlrpc.client.ServerProxy(f"{settings.odoo_url}/xmlrpc/2/object")
    return models, _odoo_uid

def get_odoo_config() -> dict:
    _, uid = get_models()

    return {
        "db": settings.odoo_db,
        "uid": uid,
        "key": settings.odoo_api_key,
    }
