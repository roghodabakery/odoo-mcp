"""Purchase order tools for Odoo MCP server."""
import logging
from typing import List, Dict, Any

from fastmcp import Context

from settings import settings
from utils.odoo_client import get_models

logger = logging.getLogger(__name__)

# MCP instance will be set during registration
mcp = None


def register_tools(mcp_instance):
    """
    Register purchase order tools with the MCP server instance.

    Args:
        mcp_instance: FastMCP server instance
    """
    global mcp
    mcp = mcp_instance

    @mcp.tool()
    def create_purchase_order(
        partner_id: int,
        invoice_date: str,
        lines: List[Dict[str, Any]],
        ctx: Context = None
    ) -> dict:
        """
        Create a draft purchase order in Odoo.

        Call only after all product IDs are resolved and user approved.
        Returns po_id, po_name, total. State will be 'draft'.

        Args:
            partner_id: Odoo partner ID (supplier)
            invoice_date: Date string (YYYY-MM-DD)
            lines: List of line items [{"product_id": N, "name": "", "qty": N, "price_unit": N}]
            ctx: MCP context for logging

        Returns:
            dict with po_id, po_name, total, state
        """
        if ctx:
            ctx.info(f"Creating purchase order for partner {partner_id} with {len(lines)} lines")

        models, uid = get_models()

        order_lines = []
        for line in lines:
            product_id = line.get("product_id")
            if not product_id:
                continue  # skip unresolved lines — product_id must be resolved before PO creation

            order_lines.append((0, 0, {
                "product_id": product_id,
                "name": line.get("name") or line.get("description", ""),
                "product_qty": line.get("qty") or line.get("quantity", 1),
                "price_unit": line.get("price_unit") or line.get("unit_price", 0),
                "date_planned": invoice_date,
            }))

        if not order_lines:
            raise ValueError(
                "No lines with a resolved product_id — all lines were skipped. "
                "Resolve product IDs before calling create_purchase_order."
            )

        po_id = models.execute_kw(
            settings.odoo_db, uid, settings.odoo_api_key,
            "purchase.order", "create",
            [{
                "partner_id": partner_id,
                "order_line": order_lines,
            }]
        )

        po = models.execute_kw(
            settings.odoo_db, uid, settings.odoo_api_key,
            "purchase.order", "read",
            [[po_id]],
            {"fields": ["name", "amount_total", "state"]}
        )[0]

        if ctx:
            ctx.info(f"Purchase order created: {po['name']} with total {po['amount_total']}")

        return {
            "po_id": po_id,
            "po_name": po["name"],
            "total": po["amount_total"],
            "state": "draft"
        }

    @mcp.tool()
    def confirm_order(po_id: int, ctx: Context = None) -> dict:
        """
        Confirm a draft purchase order (draft → purchase).

        Odoo auto-creates a stock.picking receipt.
        Only call after explicit second confirmation from user.

        Args:
            po_id: Purchase order ID to confirm
            ctx: MCP context for logging

        Returns:
            dict with success, po_id, picking_id, message
        """
        if ctx:
            ctx.info(f"Confirming purchase order {po_id}")

        models, uid = get_models()

        models.execute_kw(
            settings.odoo_db, uid, settings.odoo_api_key,
            "purchase.order", "button_confirm",
            [[po_id]]
        )

        pickings = models.execute_kw(
            settings.odoo_db, uid, settings.odoo_api_key,
            "stock.picking", "search_read",
            [[("purchase_id", "=", po_id), ("state", "!=", "done")]],
            {"fields": ["id", "name", "state"], "limit": 1}
        )

        picking_id = pickings[0]["id"] if pickings else None

        if ctx:
            ctx.info(f"Purchase order {po_id} confirmed. Receipt ID: {picking_id}")

        return {
            "success": True,
            "po_id": po_id,
            "picking_id": picking_id,
            "message": "PO confirmed. Receipt created automatically."
        }

    @mcp.tool()
    def validate_receipt(picking_id: int, ctx: Context = None) -> dict:
        """
        Validate stock receipt (mark goods as received).

        Updates stock.quant. Skip for businesses with service-type products or no warehouse.

        Args:
            picking_id: Stock picking ID to validate
            ctx: MCP context for logging

        Returns:
            dict with success, picking_id, message
        """
        if ctx:
            ctx.info(f"Validating receipt {picking_id}")

        models, uid = get_models()

        picking = models.execute_kw(
            settings.odoo_db, uid, settings.odoo_api_key,
            "stock.picking", "read",
            [[picking_id]],
            {"fields": ["move_ids"]}
        )[0]

        for move_id in picking["move_ids"]:
            move = models.execute_kw(
                settings.odoo_db, uid, settings.odoo_api_key,
                "stock.move", "read",
                [[move_id]],
                {"fields": ["product_uom_qty"]}
            )[0]

            move_lines = models.execute_kw(
                settings.odoo_db, uid, settings.odoo_api_key,
                "stock.move.line", "search_read",
                [[("move_id", "=", move_id)]],
                {"fields": ["id"]}
            )

            for ml in move_lines:
                models.execute_kw(
                    settings.odoo_db, uid, settings.odoo_api_key,
                    "stock.move.line", "write",
                    [[ml["id"]], {"qty_done": move["product_uom_qty"]}]
                )

        models.execute_kw(
            settings.odoo_db, uid, settings.odoo_api_key,
            "stock.picking", "button_validate",
            [[picking_id]]
        )

        if ctx:
            ctx.info(f"Receipt {picking_id} validated. Stock updated.")

        return {
            "success": True,
            "picking_id": picking_id,
            "message": "Receipt validated. Stock updated."
        }

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_purchase_order(po_id: int, ctx: Context = None) -> dict:
        """
        Retrieve purchase order details from Odoo.

        Args:
            po_id: Purchase order ID to retrieve
            ctx: MCP context for logging

        Returns:
            dict with po_id, po_name, partner_id, amount_total, state, order_lines
        """
        if ctx:
            ctx.info(f"Retrieving purchase order {po_id}")

        models, uid = get_models()

        po = models.execute_kw(
            settings.odoo_db, uid, settings.odoo_api_key,
            "purchase.order", "read",
            [[po_id]],
            {"fields": ["name", "partner_id", "amount_total", "state", "order_line"]}
        )

        if not po:
            return {
                "success": False,
                "error": f"Purchase order {po_id} not found"
            }

        po_data = po[0]

        # Get order line details
        if po_data.get("order_line"):
            lines = models.execute_kw(
                settings.odoo_db, uid, settings.odoo_api_key,
                "purchase.order.line", "read",
                [po_data["order_line"]],
                {"fields": ["product_id", "name", "product_qty", "price_unit", "price_subtotal"]}
            )
            po_data["order_lines"] = lines
        else:
            po_data["order_lines"] = []

        if ctx:
            ctx.info(f"Purchase order {po_id} retrieved successfully")

        return {
            "success": True,
            "po_id": po_id,
            "po_name": po_data.get("name"),
            "partner_id": po_data.get("partner_id"),
            "amount_total": po_data.get("amount_total"),
            "state": po_data.get("state"),
            "order_lines": po_data["order_lines"]
        }

    logger.info("Purchase order tools registered")
