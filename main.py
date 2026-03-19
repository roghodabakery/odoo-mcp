"""Main FastMCP server entry point for Odoo MCP Server."""
import logging


from fastmcp import FastMCP
from fastmcp.server.auth import TokenVerifier, AccessToken

from settings import settings
from tools.ocr import register_tools as register_ocr_tools
from tools.supplier import register_tools as register_supplier_tools
from tools.product import register_tools as register_product_tools
from tools.purchase import register_tools as register_purchase_tools
from utils.odoo_client import get_models, get_odoo_config

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Optional Bearer token auth for remote access
if settings.mcp_server_token:
    class SimpleTokenVerifier(TokenVerifier):
        """Simple token verifier that validates against a single environment variable token."""

        def __init__(self, expected_token: str):
            super().__init__()
            self.expected_token = expected_token

        async def verify_token(self, token: str) -> AccessToken | None:
            """Verify the token matches the expected value."""
            if token == self.expected_token:
                return AccessToken(
                    token=token,
                    client_id="mcp-client",
                    scopes=[]
                )
            return None

    auth = SimpleTokenVerifier(settings.mcp_server_token)
else:
    auth = None

# Initialize FastMCP server
mcp = FastMCP("odoo-mcp-server", auth=auth)

# Register all tools
register_ocr_tools(mcp)
register_supplier_tools(mcp)
register_product_tools(mcp)
register_purchase_tools(mcp)




if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
