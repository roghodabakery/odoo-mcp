import time
import xmlrpc.client

from settings import settings

_odoo_uid: int | None = None
_odoo_models: xmlrpc.client.ServerProxy | None = None
_odoo_last_auth: float = 0.0
_AUTH_TTL: float = 3600.0

def get_models() -> tuple[xmlrpc.client.ServerProxy, int]:
    global _odoo_uid, _odoo_last_auth, _odoo_models

    if _odoo_uid is None or time.time() - _odoo_last_auth > _AUTH_TTL:
        common = xmlrpc.client.ServerProxy(f"{settings.odoo_url}/xmlrpc/2/common")
        _odoo_uid = common.authenticate(settings.odoo_db, settings.odoo_user, settings.odoo_api_key, {})

        if not _odoo_uid :
            raise RuntimeError("Odoo Authentcation Failed, check ODOO_USERNAME and ODOO_API_KEY")

        _odoo_models = xmlrpc.client.ServerProxy(f"{settings.odoo_url}/xmlrpc/2/object")
        _odoo_last_auth = time.time()

    return _odoo_models, _odoo_uid

def get_odoo_config() -> dict:
    _, uid = get_models()

    return {
        "db": settings.odoo_db,
        "uid": uid,
        "key": settings.odoo_api_key,
    }
