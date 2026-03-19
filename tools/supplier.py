"""Supplier-related MCP tools for Odoo Buying Agent."""

from typing import Optional

from rapidfuzz import fuzz
from rapidfuzz import utils as rfutils

from settings import settings
from utils.odoo_client import get_models


def register_tools(mcp):
    """
    Register supplier tools with the MCP server instance.

    Args:
        mcp: FastMCP server instance
    """

    @mcp.tool(annotations={"readOnlyHint": True})
    def find_or_flag_supplier(name: str) -> dict:
        """
        Search Odoo for an existing supplier by name only.
        Never creates — only searches using fuzzy name matching.

        Note: tax_id is typically empty for most suppliers, so we only search by name.

        Args:
            name: Supplier name to search for

        Returns:
            dict with:
            - status: "found" | "fuzzy_match" | "not_found"
            - odoo_id: int (if found)
            - odoo_name: str (if found)
            - score: int 0-100 (similarity score, always returned)
            - needs_review: bool (True if manual review needed)
            - needs_creation: bool (True if supplier should be created)
        """
        models, uid = get_models()
        domain = [("supplier_rank", ">", 0)]

        # Fuzzy name match using first 3 words
        words = name.split()[:3]
        hits = models.execute_kw(
            settings.odoo_db,
            uid,
            settings.odoo_api_key,
            "res.partner",
            "search_read",
            [domain + [("name", "ilike", " ".join(words))]],
            {"fields": ["id", "name"], "limit": 5},
        )

        if hits:
            # Find best match using fuzzy string matching
            best = max(
                hits,
                key=lambda p: fuzz.token_set_ratio(
                    name, p["name"], processor=rfutils.default_process
                ),
            )
            score = fuzz.token_set_ratio(
                name, best["name"], processor=rfutils.default_process
            )

            if score >= 90:
                return {
                    "status": "found",
                    "odoo_id": best["id"],
                    "odoo_name": best["name"],
                    "score": score,
                    "needs_review": False,
                }
            else:
                return {
                    "status": "fuzzy_match",
                    "odoo_id": best["id"],
                    "odoo_name": best["name"],
                    "score": score,
                    "needs_review": True,
                    "message": "Found potential match but needs manual review due to low confidence",
                }

        # No match found - supplier needs to be created
        return {
            "status": "not_found",
            "invoice_name": name,
            "score": 0,
            "needs_creation": True,
            "message": "No existing supplier found. Ready to create new supplier.",
        }

    @mcp.tool()
    def create_supplier(
        name: str, tax_id: str = "", address: str = "", phone: str = "", email: str = ""
    ) -> dict:
        """
        Create a new vendor in Odoo res.partner.

        Call only after user approves. This creates a company-type partner
        with supplier_rank=1 (vendor) and customer_rank=0.

        Args:
            name: Supplier company name (required)
            tax_id: Optional tax ID / NPWP
            address: Optional street address
            phone: Optional phone number
            email: Optional email address

        Returns:
            dict with:
            - success: bool
            - partner_id: int (Odoo partner ID)
            - name: str (supplier name)
            - message: str (success message)
        """
        models, uid = get_models()

        # Build partner values
        vals = {
            "name": name,
            "supplier_rank": 1,
            "customer_rank": 0,
            "company_type": "company",
        }

        if tax_id:
            vals["vat"] = tax_id
        if address:
            vals["street"] = address
        if phone:
            vals["phone"] = phone
        if email:
            vals["email"] = email

        # Create the partner
        partner_id = models.execute_kw(
            settings.odoo_db, uid, settings.odoo_api_key, "res.partner", "create", [vals]
        )

        return {
            "success": True,
            "partner_id": partner_id,
            "name": name,
            "message": f"Successfully created supplier '{name}' with ID {partner_id}",
        }
