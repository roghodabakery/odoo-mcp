# odoo-mcp Project Onboarding

## Purpose
Standalone FastMCP server exposing Odoo purchasing tools for AI agents.
Target: replace `adk-odoo` server layer with a clean `uv`-managed Python project
that can be deployed remotely via Docker and accessed by any MCP-compatible client.

## Tech Stack
- Python 3.11+ (uv managed)
- FastMCP >=3.1.1 (MCP server framework)
- pydantic-settings >=2.x (env config)
- xmlrpc.client (stdlib — Odoo XML-RPC connection)
- Tesseract OCR + OpenCV (OCR pipeline)
- RapidFuzz (product fuzzy matching)
- pdf2image + pillow (PDF/image preprocessing)
- Docker + docker-compose for deployment

## Package Manager
`uv` — all dependency management via `pyproject.toml` + `uv.lock`
No `requirements.txt` used.

## Project Root Structure
```
odoo-mcp/
├── pyproject.toml       # uv project file
├── uv.lock              # lockfile
├── main.py              # FastMCP server entry point
├── settings.py          # pydantic-settings config
├── Dockerfile
├── docker-compose.yaml
├── tools/               # MCP tool modules
│   ├── ocr.py
│   ├── supplier.py
│   ├── product.py
│   └── purchase.py
└── utils/               # Shared utilities
    ├── odoo_client.py
    └── normalizer.py
```

## Key Commands
```bash
# Install dependencies
uv sync

# Run server locally
uv run python main.py

# Build Docker image
docker compose build

# Start services
docker compose up -d

# View logs
docker compose logs -f
```

## Code Style
- Type hints on all function signatures
- pydantic-settings for all env config (never raw os.environ)
- Tools registered via @mcp.tool() decorator
- OCR tools use register_tools(mcp) pattern
