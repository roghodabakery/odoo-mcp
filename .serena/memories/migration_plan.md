# Migration Plan: adk-odoo → odoo-mcp

## Goal
Full replacement of the MCP server layer from `adk-odoo/server/` into `odoo-mcp/`,
using `uv` as the package manager, with remote-accessible HTTP transport and Docker deployment.

The ADK agent, Streamlit UI, and FastAPI wrapper are NOT migrated — this project
is the standalone MCP server only, accessible to any MCP-compatible client or agent.

---

## What Is Migrated

| adk-odoo source | odoo-mcp destination |
|---|---|
| `server/server.py` | `main.py` |
| `server/config.py` | `settings.py` (pydantic-settings) |
| `server/schemas.py` | `schemas.py` |
| `server/tools/ocr.py` | `tools/ocr.py` |
| `server/tools/supplier.py` | `tools/supplier.py` |
| `server/tools/product.py` | `tools/product.py` |
| `server/tools/purchase.py` | `tools/purchase.py` |
| `server/utils/odoo_client.py` | `utils/odoo_client.py` |
| `server/utils/normalizer.py` | `utils/normalizer.py` |
| `requirements.txt` (pip) | `pyproject.toml` (uv) |
| `docker/mcp-server/Dockerfile` | `Dockerfile` |
| `docker-compose.yml` (3 services) | `docker-compose.yaml` (1 service: mcp-server only) |

---

## Step 1: Fix pyproject.toml and .python-version

uv manages Python automatically — it will download and install the pinned version
when running `uv sync` or `uv run`. No manual pyenv/system Python needed.

Update `.python-version` to a stable released version:
```
3.13
```

Replace the skeleton `pyproject.toml`:

```toml
[project]
name = "odoo-mcp"
version = "1.0.0"
description = "Standalone FastMCP server for Odoo purchasing automation"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "fastmcp>=3.1.1",
    "pydantic-settings>=2.13.1",
    "pytesseract>=0.3.10",
    "opencv-python-headless>=4.9",
    "pillow>=10.0",
    "pdf2image>=1.17",
    "rapidfuzz>=3.6",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["tools", "utils"]
```

Run: `uv sync` — uv downloads Python 3.13 automatically if not present, then installs all deps.

---

## Step 2: Fix settings.py

Add missing `odoo_db` field and MCP auth token:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    odoo_url: str
    odoo_db: str
    odoo_user: str
    odoo_api_key: str
    mcp_server_token: str = ""  # optional Bearer token for remote auth

settings = Settings()
```

---

## Step 3: Fix utils/odoo_client.py

Already partially implemented. Fix `settings.odoo_db` reference (field was missing).
After Step 2 fix this works. Add `get_odoo_config()` helper already present.

---

## Step 4: Migrate utils/normalizer.py

Copy from `adk-odoo/server/utils/normalizer.py` verbatim.
Functions: `preprocess_for_ocr`, `run_tesseract`, `normalize_ocr_text`, `decode_base64_file`

---

## Step 5: Migrate schemas.py

Copy from `adk-odoo/server/schemas.py` verbatim.
Pydantic models: `InvoiceJSON`, `DraftPlan`, `ApprovedPlan`

---

## Step 6: Migrate tools/

Copy each tool file from `adk-odoo/server/tools/` to `odoo-mcp/tools/`.
Update imports: `from server.config import ...` → `from settings import settings`
Update imports: `from server.utils.odoo_client import get_models` → `from utils.odoo_client import get_models`

**tools/ocr.py**: `register_tools(mcp_instance)` pattern — keep as-is, update imports
**tools/supplier.py**: inline `@mcp.tool()` decorators — keep as-is
**tools/product.py**: thread-safe cache + fuzzy match — keep as-is
**tools/purchase.py**: all write tools — keep as-is

---

## Step 7: Rewrite main.py (Server Entry Point)

Replace stub `main.py` with FastMCP server:

```python
from fastmcp import FastMCP
from fastmcp.server.auth import TokenVerifier
from settings import settings
from tools.ocr import register_tools as register_ocr_tools
from tools.supplier import register_tools as register_supplier_tools
from tools.product import register_tools as register_product_tools
from tools.purchase import register_tools as register_purchase_tools

# Optional Bearer token auth for remote access
if settings.mcp_server_token:
    class SimpleTokenVerifier(TokenVerifier):
        async def verify_token(self, token: str) -> dict:
            if token != settings.mcp_server_token:
                raise ValueError("Invalid token")
            return {"sub": "mcp-client"}
    auth = SimpleTokenVerifier()
else:
    auth = None

mcp = FastMCP("odoo-mcp-server", auth=auth)

# Register all tools
register_ocr_tools(mcp)
register_supplier_tools(mcp)
register_product_tools(mcp)
register_purchase_tools(mcp)

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
```

**Note:** The `/ocr` endpoint has been removed. Vision models will be used instead of Tesseract OCR
for processing invoice images.

---

## Step 8: Remote Access Configuration

FastMCP supports two HTTP transports:
- `"streamable-http"` → recommended, supports SSE streaming at `/mcp`
- `"sse"` → legacy SSE transport

For remote access:
1. Set `MCP_SERVER_TOKEN` in `.env` to enable Bearer auth
2. Expose port 8080 via Docker
3. Use a reverse proxy (nginx/Caddy) for TLS termination in production

Remote client connects to: `https://your-server/mcp`
With header: `Authorization: Bearer <MCP_SERVER_TOKEN>`

---

## Step 9: Update Dockerfile

Use `ghcr.io/astral-sh/uv:python3.13-bookworm-slim` as the base image.
uv pre-installs Python 3.13 into the image — no separate Python base image needed.
Two-phase install maximizes Docker layer caching (deps layer only rebuilds when lockfile changes).

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# System deps for Tesseract OCR + OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-ind \
    tesseract-ocr-eng \
    poppler-utils \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# uv settings for Docker
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0

# Phase 1: Install dependencies only (cached layer — only rebuilds on lockfile change)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Phase 2: Copy app code and install project
COPY main.py settings.py schemas.py ./
COPY tools/ ./tools/
COPY utils/ ./utils/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "main.py"]
```

---

## Step 10: Update docker-compose.yaml

```yaml
services:
  odoo-mcp:
    build: .
    ports:
      - "8080:8080"
    env_file: .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/mcp"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
```

---

## Step 11: .env.example

```env
ODOO_URL=https://your-odoo-instance.com
ODOO_DB=your_database_name
ODOO_USER=admin
ODOO_API_KEY=your_odoo_api_key

# Optional: Bearer token for remote MCP access (leave empty to disable auth)
MCP_SERVER_TOKEN=
```

---

## Import Path Changes Summary

| Old (adk-odoo) | New (odoo-mcp) |
|---|---|
| `from server.config import ODOO_URL, ODOO_DB, ...` | `from settings import settings` |
| `from server.utils.odoo_client import get_models` | `from utils.odoo_client import get_models` |
| `from server.utils.normalizer import ...` | `from utils.normalizer import ...` |
| `from server.schemas import ...` | `from schemas import ...` |

---

## Key Differences vs adk-odoo

1. **uv only** — no `requirements.txt`, no pip, lockfile-based installs
2. **pydantic-settings** — all config via `Settings` class, not raw `os.environ`
3. **Single container** — only MCP server, no agent/UI containers
4. **Remote-first** — `streamable-http` transport, Bearer token auth, Docker-exposed
5. **Python 3.13** — managed automatically by uv; `.python-version` pins it; no manual pyenv needed
6. **Tool registration** — all tools use `register_tools(mcp)` pattern for clean modules

---

## Validation Commands

```bash
# Verify all 8 tools registered
docker compose exec odoo-mcp uv run python -c "
from main import mcp
import asyncio
async def main():
    tools = await mcp.list_tools()
    for t in tools: print(t.name)
asyncio.run(main())
"

# Test MCP endpoint is live
curl -f http://localhost:8080/mcp

# Test /ocr endpoint (expects 400 with no file — proves it's reachable)
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8080/ocr

# Test with auth token
curl -H "Authorization: Bearer <token>" http://localhost:8080/mcp
```
