"""Product tools for Odoo MCP server."""
import threading
import time
from typing import Dict, Any, List

from rapidfuzz import process, fuzz, utils as rfutils
from fastmcp import Context

from settings import settings
from utils.odoo_client import get_models

# Thread-safe product cache for fuzzy matching
_product_cache: List[Dict[str, Any]] = []
_cache_lock = threading.Lock()
_cache_last_refresh: float = 0.0
CACHE_TTL_SECONDS = 1800  # 30 minutes


def refresh_product_cache() -> None:
    """
    Refresh the product cache with all active purchasable products from Odoo.
    Thread-safe implementation with lock protection.
    """
    global _product_cache, _cache_last_refresh

    models, uid = get_models()
    products = models.execute_kw(
        settings.odoo_db, uid, settings.odoo_api_key, "product.product", "search_read",
        [[("active", "=", True), ("purchase_ok", "=", True)]],
        {"fields": ["id", "name", "default_code", "uom_id", "standard_price"], "limit": 5000}
    )

    with _cache_lock:
        _product_cache = products
        _cache_last_refresh = time.time()


def _ensure_cache_fresh() -> None:
    """
    Ensure the product cache is fresh by refreshing if TTL has expired.
    Internal function called before cache operations.
    """
    if time.time() - _cache_last_refresh > CACHE_TTL_SECONDS:
        refresh_product_cache()


def find_or_flag_product(
    invoice_name: str,
    quantity: float,
    unit_price: float
) -> Dict[str, Any]:
    """
    Search for an existing Odoo product matching the invoice line description
    using a 3-layer fuzzy cascade.

    Layer 1: Odoo ilike DB-side pre-filter (fast)
    Layer 2: RapidFuzz full cache scan (comprehensive)
    Layer 3: No match flagging (safety net)

    Args:
        invoice_name: Product description from invoice
        quantity: Quantity from invoice
        unit_price: Unit price from invoice

    Returns:
        dict with one of:
        - {"status": "exact_match", "product_id": N, "product_name": "...", "score": 95.2}
        - {"status": "fuzzy_match", "product_id": N, "product_name": "...", "score": 82.1, "needs_review": True}
        - {"status": "uncertain_match", "product_id": N, "product_name": "...", "score": 64.0, "needs_review": True}
        - {"status": "no_match", "suggested_name": "...", "needs_creation": True}
    """
    _ensure_cache_fresh()
    models, uid = get_models()

    # Layer 1: Odoo ilike — fast DB-side pre-filter
    words = invoice_name.split()[:3]
    odoo_hits = models.execute_kw(
        settings.odoo_db, uid, settings.odoo_api_key, "product.product", "search_read",
        [[("name", "ilike", " ".join(words)), ("active", "=", True)]],
        {"fields": ["id", "name", "default_code", "uom_id", "standard_price"], "limit": 10}
    )

    if odoo_hits:
        best = max(
            odoo_hits,
            key=lambda p: fuzz.token_set_ratio(
                invoice_name, p["name"],
                processor=rfutils.default_process
            )
        )
        score = fuzz.token_set_ratio(
            invoice_name, best["name"],
            processor=rfutils.default_process
        )

        # Exact match: auto-use, no review needed
        if score >= 92:
            return {
                "status": "exact_match",
                "product_id": best["id"],
                "product_name": best["name"],
                "default_code": best.get("default_code"),
                "uom": best.get("uom_id", [None, "Unit(s)"])[1] if best.get("uom_id") else "Unit(s)",
                "standard_price": best.get("standard_price", 0.0),
                "score": score
            }

        # Good enough matches: needs review
        if score >= 50:
            status = "fuzzy_match" if score >= 72 else "uncertain_match"
            return {
                "status": status,
                "product_id": best["id"],
                "product_name": best["name"],
                "default_code": best.get("default_code"),
                "uom": best.get("uom_id", [None, "Unit(s)"])[1] if best.get("uom_id") else "Unit(s)",
                "standard_price": best.get("standard_price", 0.0),
                "score": score,
                "invoice_name": invoice_name,
                "needs_review": True
            }

    # Layer 2: Full cache scan with RapidFuzz
    with _cache_lock:
        cache_snapshot = list(_product_cache)

    if cache_snapshot:
        names = [p["name"] for p in cache_snapshot]
        results = process.extract(
            invoice_name,
            names,
            scorer=fuzz.token_set_ratio,
            processor=rfutils.default_process,
            limit=3
        )

        if results and results[0][1] >= 50:
            matched_name, score, idx = results[0]
            product = cache_snapshot[idx]
            status = "fuzzy_match" if score >= 72 else "uncertain_match"
            return {
                "status": status,
                "product_id": product["id"],
                "product_name": matched_name,
                "default_code": product.get("default_code"),
                "uom": product.get("uom_id", [None, "Unit(s)"])[1] if product.get("uom_id") else "Unit(s)",
                "standard_price": product.get("standard_price", 0.0),
                "score": score,
                "invoice_name": invoice_name,
                "needs_review": True
            }

    # Layer 3: No match — flag for creation
    return {
        "status": "no_match",
        "suggested_name": invoice_name,
        "suggested_qty": quantity,
        "suggested_price": unit_price,
        "needs_creation": True
    }


def register_tools(mcp):
    """
    Register product tools with the MCP server instance.

    Args:
        mcp: FastMCP server instance
    """

    @mcp.tool(annotations={"readOnlyHint": True})
    def find_or_flag_product_tool(
        description: str,
        quantity: float,
        unit_price: float,
        ctx: Context = None
    ) -> Dict[str, Any]:
        """
        Search for an existing Odoo product matching the invoice line description.
        Returns match status and product_id if found, or flags for creation.
        NEVER creates a product — only searches.

        Uses a 3-layer fuzzy cascade:
        1. Odoo ilike DB-side pre-filter (fast)
        2. RapidFuzz full cache scan (comprehensive)
        3. No match flagging (safety net)

        Args:
            description: Product description from invoice
            quantity: Quantity from invoice
            unit_price: Unit price from invoice
            ctx: MCP context for logging

        Returns:
            dict with one of:
            - {"status": "exact_match", "product_id": N, "product_name": "...", "uom": "...", "standard_price": N.N, "score": 95.2}
            - {"status": "fuzzy_match", "product_id": N, "product_name": "...", "uom": "...", "standard_price": N.N, "score": 82.1, "needs_review": True}
            - {"status": "uncertain_match", "product_id": N, "product_name": "...", "uom": "...", "standard_price": N.N, "score": 64.0, "needs_review": True}
            - {"status": "no_match", "suggested_name": "...", "suggested_qty": N, "suggested_price": N.N, "needs_creation": True}
        """
        if ctx:
            ctx.info(f"Searching for product: {description}")

        result = find_or_flag_product(description, quantity, unit_price)

        if ctx:
            ctx.info(f"Product search result: {result['status']}")

        return result

    @mcp.tool()
    def create_product(
        name: str,
        product_type: str = "product",
        standard_price: float = 0.0,
        uom_name: str = "Unit(s)",
        ctx: Context = None
    ) -> dict:
        """
        Create a new product in Odoo. Call only after user approves.

        Args:
            name: Product name
            product_type: 'product' (storable) or 'service' (no stock — use for UMKM without warehouse)
            standard_price: Cost price of the product
            uom_name: Unit of measure name (e.g., "Unit(s)", "kg", "m")
            ctx: MCP context for logging

        Returns:
            dict with success status, product_id, and name
        """
        if ctx:
            ctx.info(f"Creating product: {name}")

        models, uid = get_models()

        # Find UOM by name
        uom_hits = models.execute_kw(
            settings.odoo_db, uid, settings.odoo_api_key, "uom.uom", "search_read",
            [[("name", "ilike", uom_name)]],
            {"fields": ["id", "name"], "limit": 1}
        )

        # Fallback to "Unit" if specific UOM not found
        if not uom_hits:
            uom_hits = models.execute_kw(
                settings.odoo_db, uid, settings.odoo_api_key, "uom.uom", "search_read",
                [[("name", "ilike", "Unit")]],
                {"fields": ["id", "name"], "limit": 1}
            )

        uom_id = uom_hits[0]["id"] if uom_hits else None

        # Prepare product values
        vals = {
            "name": name,
            "type": product_type,
            "purchase_ok": True,
            "sale_ok": False,
            "standard_price": standard_price
        }

        if uom_id:
            vals["uom_id"] = uom_id
            vals["uom_po_id"] = uom_id

        # Create the product
        product_id = models.execute_kw(
            settings.odoo_db, uid, settings.odoo_api_key,
            "product.product", "create",
            [vals]
        )

        # Refresh cache after creating new product
        refresh_product_cache()

        if ctx:
            ctx.info(f"Product created successfully with ID: {product_id}")

        return {
            "success": True,
            "product_id": product_id,
            "name": name
        }
